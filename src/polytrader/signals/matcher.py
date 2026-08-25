"""Rule-based event -> Polymarket market matching (v1 heuristic, not ML).

Asks, for every newly-ingested government-contract or congressional-trade
event: "does this affect any open Polymarket market?" via keyword/entity
overlap between the event's terms and the market's question text. This
deliberately does NOT assert a trade direction or bullish/bearish stance
-- per the merged-thesis writeup, blindly treating every contract as
bullish is exactly the mistake to avoid. It only narrows the search
space; a `Signal.direction` of "unknown" here means "these are related,"
not "buy YES." Turning that into a real probability move is
research.py/ClaudeEstimator's job (or a human's), using the same
Estimator interface as the rest of the Polymarket pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.core.decision_log import record_signal
from polytrader.core.events import DomainEvent, get_event_bus
from polytrader.core.models import AssetClass, Market, Signal, SignalSourceType

_STOPWORDS = {
    "will", "the", "a", "an", "of", "in", "on", "for", "to", "by", "and", "or",
    "before", "after", "than", "is", "are", "be", "with", "at", "this", "that",
    "does", "not", "into", "over", "under", "new", "more", "than",
}
_WORD = re.compile(r"[a-z0-9]+")

_SOURCE_TYPE_BY_EVENT: dict[str, SignalSourceType] = {
    "contract_award": SignalSourceType.CONTRACT_AWARD,
    "congress_trade": SignalSourceType.CONGRESS_TRADE,
}


def _keywords(text: str) -> set[str]:
    words = _WORD.findall((text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


@dataclass(frozen=True)
class MatchCandidate:
    market_id: str
    question: str
    score: float
    matched_terms: frozenset[str]


def _event_keywords(event_type: str, payload: dict) -> set[str]:
    terms: set[str] = set()
    if event_type == "contract_award":
        terms |= _keywords(payload.get("agency", ""))
        terms |= _keywords(payload.get("vendor", ""))
        terms |= _keywords(payload.get("title", ""))
    elif event_type == "congress_trade":
        if payload.get("ticker"):
            terms.add(str(payload["ticker"]).lower())
        terms |= _keywords(payload.get("legislator", ""))
    elif event_type == "regulatory_action":
        terms |= _keywords(payload.get("title", ""))
        for agency in payload.get("agencies", []):
            terms |= _keywords(agency)
    return terms


def find_candidate_markets(
    session: Session, event_type: str, payload: dict, *, min_matches: int = 2, limit: int = 10
) -> list[MatchCandidate]:
    """Requires at least `min_matches` overlapping non-trivial keywords by
    default -- one shared word (both happen to mention "AI") is too weak
    to surface on its own; this is a coarse filter, not a verdict."""
    event_terms = _event_keywords(event_type, payload)
    if not event_terms:
        return []

    candidates: list[MatchCandidate] = []
    for market in session.scalars(select(Market).where(Market.status == "active")):
        market_terms = _keywords(market.question)
        overlap = event_terms & market_terms
        if len(overlap) >= min_matches:
            score = len(overlap) / max(1, len(event_terms))
            candidates.append(
                MatchCandidate(
                    market_id=market.market_id,
                    question=market.question,
                    score=score,
                    matched_terms=frozenset(overlap),
                )
            )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def record_candidate_signals(
    session: Session, event_type: str, payload: dict, *, min_matches: int = 2
) -> list[Signal]:
    source_type = _SOURCE_TYPE_BY_EVENT.get(event_type)
    if source_type is None:
        return []

    ref_id = str(payload.get("contract_id") or payload.get("trade_id") or "")
    signals = []
    for candidate in find_candidate_markets(session, event_type, payload, min_matches=min_matches):
        thesis = (
            f"{event_type} evidence overlaps market '{candidate.question}' on: "
            f"{', '.join(sorted(candidate.matched_terms))} (relatedness score={candidate.score:.2f}). "
            "Direction/edge not yet evaluated -- run research.py against this before trading it."
        )
        signals.append(
            record_signal(
                session,
                source_type=source_type,
                asset_class=AssetClass.POLYMARKET,
                direction="unknown",
                market_id=candidate.market_id,
                confidence=min(1.0, candidate.score),
                source_ref_type=event_type,
                source_ref_id=ref_id,
                thesis=thesis,
            )
        )
    return signals


def wire_signal_matcher(session: Session, *, min_matches: int = 2) -> None:
    """Subscribes to contract_award/congress_trade events on the shared
    event bus for the lifetime of `session`. Call once per CLI invocation,
    before running ingestion, so matches land in the same pass -- the bus
    is synchronous and in-process (core/events.py), so this is a direct
    call, not a queue."""
    bus = get_event_bus()

    def _handler(event: DomainEvent) -> None:
        record_candidate_signals(session, event.event_type, event.payload, min_matches=min_matches)

    for event_type in _SOURCE_TYPE_BY_EVENT:
        bus.subscribe(event_type, _handler)
