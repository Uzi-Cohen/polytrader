"""Research & signal layer (FR-RES-01..05).

`Estimator` is the pluggable interface between a market + evidence and a
probability forecast. The only implementation that produces real numbers
is `ClaudeEstimator`, and only when `ANTHROPIC_API_KEY` is set --
`NoOpEstimator` deliberately returns `None` rather than a fabricated
probability, because a probability the system can't back with evidence is
worse than no signal at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from polytrader.core.config import Settings


@dataclass(frozen=True)
class EvidenceItem:
    fact: str
    source_url: str
    source_timestamp: datetime | None = None


@dataclass(frozen=True)
class ResearchPacketData:
    """FR-RES-01: question, known facts, unknowns, sources, contradictions
    -- built before any forecast is requested."""

    market_id: str
    question: str
    facts: list[EvidenceItem] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ForecastResult:
    """FR-RES-02: P(YES), confidence, uncertainty range -- never a bare
    directional label."""

    p_yes: float
    confidence: float
    uncertainty_low: float
    uncertainty_high: float
    rationale: str
    model_version: str


class Estimator(Protocol):
    model_version: str

    def forecast(self, packet: ResearchPacketData) -> ForecastResult | None: ...


class NoOpEstimator:
    """Always declines to forecast. Used when no LLM is configured, and in
    tests, so the system never silently invents a probability."""

    model_version = "noop-v1"

    def forecast(self, packet: ResearchPacketData) -> ForecastResult | None:
        return None


_SYSTEM_PROMPT = (
    "You are a calibrated forecaster estimating the probability that a prediction-market "
    "question resolves YES, using only the evidence provided. Do not use outside knowledge "
    "beyond the evidence list and general reasoning -- if the evidence is insufficient, "
    "reflect that with a wide uncertainty range and low confidence rather than guessing "
    "narrowly. Respond with ONLY a JSON object of the form: "
    '{"p_yes": <0-1 float>, "confidence": <0-1 float>, "uncertainty_low": <0-1 float>, '
    '"uncertainty_high": <0-1 float>, "rationale": "<one paragraph>"}'
)


class ClaudeEstimator:
    """FR-RES-02/03/04. Requires ANTHROPIC_API_KEY; raises at construction
    time rather than silently degrading -- callers without a key should
    use NoOpEstimator explicitly instead of getting a surprise fallback."""

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it to enable the Claude-backed research "
                "estimator, or construct NoOpEstimator instead."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "The 'anthropic' package is required for ClaudeEstimator. "
                "Install with: pip install -e '.[llm]'"
            ) from exc

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model_version = settings.research_model

    def forecast(self, packet: ResearchPacketData) -> ForecastResult | None:
        evidence_lines = [
            f"- {item.fact} (source: {item.source_url}"
            + (f", as of {item.source_timestamp.isoformat()})" if item.source_timestamp else ")")
            for item in packet.facts
        ]
        user_prompt = (
            f"Question: {packet.question}\n\n"
            f"Evidence:\n" + ("\n".join(evidence_lines) if evidence_lines else "(none)") + "\n\n"
            f"Known unknowns: {', '.join(packet.unknowns) or '(none)'}\n"
            f"Contradictions in the evidence: {', '.join(packet.contradictions) or '(none)'}\n"
            f"Assumptions you should make explicit if used: {', '.join(packet.assumptions) or '(none)'}\n"
        )

        response = self._client.messages.create(
            model=self.model_version,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        payload = json.loads(text)

        return ForecastResult(
            p_yes=float(payload["p_yes"]),
            confidence=float(payload["confidence"]),
            uncertainty_low=float(payload["uncertainty_low"]),
            uncertainty_high=float(payload["uncertainty_high"]),
            rationale=str(payload["rationale"]),
            model_version=self.model_version,
        )


def build_research_packet(
    market_id: str,
    question: str,
    *,
    facts: list[EvidenceItem] | None = None,
    unknowns: list[str] | None = None,
    contradictions: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> ResearchPacketData:
    return ResearchPacketData(
        market_id=market_id,
        question=question,
        facts=facts or [],
        unknowns=unknowns or [],
        contradictions=contradictions or [],
        assumptions=assumptions or [],
    )


def get_default_estimator(settings: Settings) -> Estimator:
    """Returns a Claude-backed estimator if a key is configured, otherwise
    the honest no-op. Never returns something that fabricates numbers."""
    if settings.anthropic_api_key:
        return ClaudeEstimator(settings)
    return NoOpEstimator()
