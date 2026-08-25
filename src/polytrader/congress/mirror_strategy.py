"""Congressional-trade mirror strategy.

Turns a disclosed CongressTrade into a TradeProposal for the *same*
deterministic risk engine used by the Polymarket trader (core/risk.py),
sized by a documented heuristic rather than fractional Kelly -- there's no
probability model here for "will this stock go up," only a disclosed
direction, amount bracket, and disclosure lag. What's genuinely reused,
not just visually similar, is the risk gate itself: this strategy cannot
get a trade past max-risk-per-trade, exposure caps, the kill switch, or
any other limit in core/risk.py, because it produces the same
RiskDecision-shaped output as every Polymarket signal.

No live equity price feed is wired into this build (see
docs/DATA_SOURCES.md), so mirrored equity Positions are tracked in
*dollar notional*, not real share counts: avg_entry is a fixed 1.0
placeholder and current_mark never moves. Unrealized P&L on a mirrored
position is therefore always 0 -- this system honestly reports what it
would have mirrored and how much, not a fabricated return.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.core.decision_log import record_risk_decision, record_signal, record_trade_decision
from polytrader.core.models import (
    AssetClass,
    CongressTrade,
    Order,
    OrderStatus,
    Position,
    RiskDecisionOutcome,
    SignalSourceType,
)
from polytrader.core.risk import PortfolioState, RiskEngine, TradeProposal
from polytrader.core.timeutil import ensure_aware

# Bigger disclosed trades imply more legislator conviction, within limits.
# Reference midpoint is the common $15,001-$50,000 STOCK Act bracket.
_REFERENCE_MIDPOINT = 32_500.0
_MAX_CONVICTION_MULTIPLIER = 3.0
_MIN_CONVICTION_MULTIPLIER = 0.25

# adjusted_edge proxy: mirror confidence decays with disclosure lag. This
# stands in for a real edge estimate (there isn't one for "will this stock
# go up") -- it exists only so the shared risk engine's edge-threshold
# check (FR-EDGE-03) has something to gate on. ~40 days to decay below the
# default 0.03 threshold lines up with the STOCK Act's 45-day filing
# deadline: a disclosure filed right at the deadline is already borderline
# too stale to mirror under default settings.
_FRESH_EDGE = 0.12
_EDGE_HALF_LIFE_DAYS = 20.0


@dataclass(frozen=True)
class MirrorProposal:
    trade_proposal: TradeProposal
    congress_trade_id: str
    ticker: str
    direction: str  # "buy" | "sell"


def _conviction_multiplier(trade: CongressTrade) -> float:
    if trade.amount_range_low is None or trade.amount_range_high is None:
        return 1.0
    midpoint = (trade.amount_range_low + trade.amount_range_high) / 2
    return max(_MIN_CONVICTION_MULTIPLIER, min(_MAX_CONVICTION_MULTIPLIER, midpoint / _REFERENCE_MIDPOINT))


def _edge_proxy(trade: CongressTrade, now: datetime) -> float:
    if trade.disclosure_date is None:
        return 0.0
    lag_days = max(0.0, (now - ensure_aware(trade.disclosure_date)).total_seconds() / 86_400)
    decay = 0.5 ** (lag_days / _EDGE_HALF_LIFE_DAYS)
    return _FRESH_EDGE * decay


def build_mirror_proposal(
    trade: CongressTrade, *, base_mirror_unit: float, now: datetime | None = None
) -> MirrorProposal | None:
    """Returns None when the trade isn't actionable: no resolvable ticker
    (bond/fund/option line items often have none), or an unknown
    disclosure_date -- the system refuses to guess the lag rather than
    assume same-day disclosure. See congress/senate_client.py:
    fetch_daily_filing is the ingestion path that actually knows
    disclosure_date; fetch_all_transactions does not."""
    if not trade.ticker or trade.disclosure_date is None:
        return None
    if trade.transaction_type not in ("purchase", "sale_full", "sale_partial"):
        return None

    now = now or datetime.now(timezone.utc)
    direction = "buy" if trade.transaction_type == "purchase" else "sell"
    conviction = _conviction_multiplier(trade)
    adjusted_edge = _edge_proxy(trade, now)

    proposal = TradeProposal(
        asset_class=AssetClass.EQUITY,
        market_or_symbol=trade.ticker,
        executable_price=0.5,  # unused for sizing (proposed_notional is explicit); satisfies the dataclass
        adjusted_edge=adjusted_edge,
        category=None,
        fair_probability=None,
        proposed_notional=base_mirror_unit * conviction,
        confidence=min(1.0, conviction / _MAX_CONVICTION_MULTIPLIER),
        data_timestamp=ensure_aware(trade.disclosure_date),
        now=now,
    )
    return MirrorProposal(
        trade_proposal=proposal, congress_trade_id=trade.trade_id, ticker=trade.ticker, direction=direction
    )


def get_open_equity_position(session: Session, symbol: str) -> Position | None:
    stmt = select(Position).where(
        Position.asset_class == AssetClass.EQUITY,
        Position.symbol == symbol,
        Position.side == "buy",
        Position.closed_at.is_(None),
    )
    return session.scalars(stmt).first()


def paper_mirror_buy(session: Session, *, symbol: str, notional: float) -> Position:
    position = get_open_equity_position(session, symbol)
    if position is None:
        position = Position(
            asset_class=AssetClass.EQUITY,
            symbol=symbol,
            side="buy",
            avg_entry=1.0,
            size=notional,
            current_mark=1.0,
            opened_at=datetime.now(timezone.utc),
        )
        session.add(position)
    else:
        position.size += notional
    session.flush()
    return position


def paper_mirror_sell(session: Session, *, symbol: str, notional: float) -> Position | None:
    """A disclosed sell either reduces/closes an existing mirrored long, or
    -- if nothing is held -- is a signal-only event (no short positions in
    this build); returns None in that second case."""
    position = get_open_equity_position(session, symbol)
    if position is None:
        return None
    position.size = max(0.0, position.size - notional)
    if position.size <= 1e-9:
        position.size = 0.0
        position.closed_at = datetime.now(timezone.utc)
    session.flush()
    return position


def mirror_trade(
    session: Session,
    trade: CongressTrade,
    *,
    risk_engine: RiskEngine,
    portfolio: PortfolioState,
    base_mirror_unit: float,
    now: datetime | None = None,
) -> dict:
    """End-to-end: CongressTrade -> Signal -> RiskDecision -> (paper) Order
    -> TradeDecision, all logged via core/decision_log.py regardless of
    outcome (FR-MON-04). Returns a small summary dict for the CLI."""
    now = now or datetime.now(timezone.utc)
    mirror_proposal = build_mirror_proposal(trade, base_mirror_unit=base_mirror_unit, now=now)

    if mirror_proposal is None:
        record_trade_decision(
            session,
            signal=None,
            risk_decision=None,
            order_id=None,
            outcome="skipped",
            notes=f"Congress trade {trade.trade_id} not actionable (no ticker or unknown disclosure_date).",
        )
        return {"outcome": "skipped", "congress_trade_id": trade.trade_id}

    signal = record_signal(
        session,
        source_type=SignalSourceType.CONGRESS_TRADE,
        asset_class=AssetClass.EQUITY,
        direction=mirror_proposal.direction,
        symbol=mirror_proposal.ticker,
        adjusted_edge=mirror_proposal.trade_proposal.adjusted_edge,
        confidence=mirror_proposal.trade_proposal.confidence,
        source_ref_type="congress_trade",
        source_ref_id=trade.trade_id,
        thesis=f"Mirroring {trade.transaction_type} by a member of Congress, disclosed {trade.disclosure_date}.",
        data_as_of=mirror_proposal.trade_proposal.data_timestamp,
    )

    evaluation = risk_engine.evaluate(mirror_proposal.trade_proposal, portfolio)

    risk_decision = record_risk_decision(
        session,
        signal=signal,
        asset_class=AssetClass.EQUITY,
        bankroll=portfolio.bankroll,
        proposed_size=evaluation.proposed_size,
        approved_size=evaluation.approved_size,
        kelly_fraction_used=evaluation.kelly_fraction_used,
        outcome=evaluation.outcome,
        limits_hit=evaluation.limits_hit,
        reasons=evaluation.reasons,
    )

    if evaluation.outcome == RiskDecisionOutcome.REJECTED or evaluation.approved_size <= 0:
        record_trade_decision(
            session, signal=signal, risk_decision=risk_decision, order_id=None, outcome="rejected", notes=evaluation.reasons
        )
        return {"outcome": "rejected", "congress_trade_id": trade.trade_id, "reasons": evaluation.reasons}

    if mirror_proposal.direction == "buy":
        position = paper_mirror_buy(session, symbol=mirror_proposal.ticker, notional=evaluation.approved_size)
    else:
        position = paper_mirror_sell(session, symbol=mirror_proposal.ticker, notional=evaluation.approved_size)

    order = Order(
        risk_decision_id=risk_decision.id,
        asset_class=AssetClass.EQUITY,
        symbol=mirror_proposal.ticker,
        side=mirror_proposal.direction,
        price=1.0,
        size=evaluation.approved_size,
        status=OrderStatus.FILLED,
        is_paper=True,
        idempotency_key=f"mirror:{trade.trade_id}",
        filled_price=1.0,
        filled_size=evaluation.approved_size,
    )
    session.add(order)
    session.flush()

    record_trade_decision(
        session,
        signal=signal,
        risk_decision=risk_decision,
        order_id=order.order_id,
        outcome="traded",
        notes=f"Paper-mirrored {mirror_proposal.direction} ${evaluation.approved_size:.2f} of {mirror_proposal.ticker}.",
    )

    return {
        "outcome": "traded",
        "congress_trade_id": trade.trade_id,
        "ticker": mirror_proposal.ticker,
        "direction": mirror_proposal.direction,
        "approved_size": evaluation.approved_size,
        "position_id": position.id if position else None,
    }
