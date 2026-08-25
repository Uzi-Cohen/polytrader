"""Append-only decision persistence (FR-MON-04): every proposal, rejection,
approval, and order must be reconstructable from stored records. Nothing
here ever updates or deletes a Signal/RiskDecision -- only Order/Position
mutate (lifecycle/mark-to-market), and TradeDecision links the rest.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from polytrader.core.models import (
    AssetClass,
    RiskDecision,
    RiskDecisionOutcome,
    Signal,
    SignalSourceType,
    SystemEvent,
    TradeDecision,
)


def record_signal(
    session: Session,
    *,
    source_type: SignalSourceType,
    asset_class: AssetClass,
    direction: str,
    market_id: str | None = None,
    symbol: str | None = None,
    forecast_id: str | None = None,
    executable_price: float | None = None,
    raw_edge: float | None = None,
    adjusted_edge: float | None = None,
    expected_value: float | None = None,
    confidence: float = 0.5,
    evidence: list[str] | None = None,
    source_ref_type: str | None = None,
    source_ref_id: str | None = None,
    thesis: str | None = None,
    data_as_of: datetime | None = None,
) -> Signal:
    signal = Signal(
        source_type=source_type,
        asset_class=asset_class,
        direction=direction,
        market_id=market_id,
        symbol=symbol,
        forecast_id=forecast_id,
        executable_price=executable_price,
        raw_edge=raw_edge,
        adjusted_edge=adjusted_edge,
        expected_value=expected_value,
        confidence=confidence,
        evidence=json.dumps(evidence or []),
        source_ref_type=source_ref_type,
        source_ref_id=source_ref_id,
        thesis=thesis,
        data_as_of=data_as_of,
    )
    session.add(signal)
    session.flush()
    return signal


def record_risk_decision(
    session: Session,
    *,
    signal: Signal | None,
    asset_class: AssetClass,
    bankroll: float,
    proposed_size: float,
    approved_size: float,
    kelly_fraction_used: float | None,
    outcome: RiskDecisionOutcome,
    limits_hit: list[str],
    reasons: str,
) -> RiskDecision:
    decision = RiskDecision(
        signal_id=signal.id if signal else None,
        asset_class=asset_class,
        bankroll=bankroll,
        proposed_size=proposed_size,
        approved_size=approved_size,
        kelly_fraction_used=kelly_fraction_used,
        outcome=outcome,
        limits_hit=json.dumps(limits_hit),
        reasons=reasons,
    )
    session.add(decision)
    session.flush()
    return decision


def record_trade_decision(
    session: Session,
    *,
    signal: Signal | None,
    risk_decision: RiskDecision | None,
    order_id: str | None,
    outcome: str,
    notes: str | None = None,
) -> TradeDecision:
    trade_decision = TradeDecision(
        signal_id=signal.id if signal else None,
        risk_decision_id=risk_decision.id if risk_decision else None,
        order_id=order_id,
        outcome=outcome,
        notes=notes,
    )
    session.add(trade_decision)
    session.flush()
    return trade_decision


def log_system_event(
    session: Session,
    *,
    source: str,
    message: str,
    severity: str = "info",
    correlation_id: str | None = None,
) -> SystemEvent:
    event = SystemEvent(
        source=source,
        message=message,
        severity=severity,
        correlation_id=correlation_id,
        timestamp=datetime.now(timezone.utc),
    )
    session.add(event)
    session.flush()
    return event
