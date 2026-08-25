"""Shared data model.

Every trade proposal -- whether it comes from the Polymarket edge engine or
the congressional-trade mirror strategy -- flows through the same
Signal -> RiskDecision -> Order -> Position -> TradeDecision chain, tagged
with an `asset_class`. That's what makes this one system instead of three:
one risk engine, one decision log, two asset classes.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AssetClass(str, enum.Enum):
    POLYMARKET = "polymarket"
    EQUITY = "equity"


class SignalSourceType(str, enum.Enum):
    MARKET_RESEARCH = "market_research"
    CONTRACT_AWARD = "contract_award"
    CONGRESS_TRADE = "congress_trade"


class OrderStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"


class RiskDecisionOutcome(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REDUCED = "reduced"


# --------------------------------------------------------------------------
# Polymarket domain (PRD 1 section 8)
# --------------------------------------------------------------------------


class Market(Base):
    __tablename__ = "markets"

    market_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    outcomes: Mapped[str] = mapped_column(String, default="YES,NO")
    resolution_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    snapshots: Mapped[list["MarketSnapshot"]] = relationship(
        back_populates="market", order_by="MarketSnapshot.timestamp"
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.market_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    yes_price: Mapped[float] = mapped_column(Float)
    no_price: Mapped[float] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)

    market: Mapped["Market"] = relationship(back_populates="snapshots")


class ResearchPacket(Base):
    __tablename__ = "research_packets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.market_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    facts: Mapped[str] = mapped_column(Text)  # JSON-encoded list[str]
    unknowns: Mapped[str | None] = mapped_column(Text, nullable=True)
    contradictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[str] = mapped_column(Text)  # JSON-encoded list[{url, timestamp}]
    model_version: Mapped[str] = mapped_column(String)


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.market_id"))
    research_packet_id: Mapped[str | None] = mapped_column(ForeignKey("research_packets.id"), nullable=True)
    model_version: Mapped[str] = mapped_column(String)
    p_yes: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    uncertainty_low: Mapped[float] = mapped_column(Float)
    uncertainty_high: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------
# Cross-asset trading pipeline (PRD 1 sections 8 & 10, generalized)
# --------------------------------------------------------------------------


class Signal(Base):
    """A candidate trade idea, from any source, before risk approval."""

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_type: Mapped[SignalSourceType] = mapped_column(SAEnum(SignalSourceType))
    asset_class: Mapped[AssetClass] = mapped_column(SAEnum(AssetClass))
    market_id: Mapped[str | None] = mapped_column(ForeignKey("markets.market_id"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    forecast_id: Mapped[str | None] = mapped_column(ForeignKey("forecasts.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String)  # "yes"/"no" for polymarket, "buy"/"sell" for equity
    executable_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    source_ref_type: Mapped[str | None] = mapped_column(String, nullable=True)  # "contract" | "congress_trade"
    source_ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RiskDecision(Base):
    """The deterministic risk engine's verdict on a proposed trade size.

    This is the one artifact every trading surface must produce before an
    Order can exist. See core/risk.py.
    """

    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    asset_class: Mapped[AssetClass] = mapped_column(SAEnum(AssetClass))
    bankroll: Mapped[float] = mapped_column(Float)
    proposed_size: Mapped[float] = mapped_column(Float)
    approved_size: Mapped[float] = mapped_column(Float, default=0.0)
    kelly_fraction_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[RiskDecisionOutcome] = mapped_column(SAEnum(RiskDecisionOutcome))
    limits_hit: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[str]
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    risk_decision_id: Mapped[str | None] = mapped_column(ForeignKey("risk_decisions.id"), nullable=True)
    asset_class: Mapped[AssetClass] = mapped_column(SAEnum(AssetClass))
    market_id: Mapped[str | None] = mapped_column(ForeignKey("markets.market_id"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    side: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    tif: Mapped[str] = mapped_column(String, default="GTC")
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.PROPOSED)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Position(Base):
    """At most one OPEN (closed_at is None) row should exist per
    (asset_class, market_id/symbol, side) at a time -- enforced in
    application logic (polymarket/paper_portfolio.py), not a DB constraint,
    since a plain UniqueConstraint here would also block ever reopening a
    position after it's been closed."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    asset_class: Mapped[AssetClass] = mapped_column(SAEnum(AssetClass))
    market_id: Mapped[str | None] = mapped_column(ForeignKey("markets.market_id"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    side: Mapped[str] = mapped_column(String)
    avg_entry: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    current_mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeDecision(Base):
    """Links every artifact behind a single decision_id for auditability."""

    __tablename__ = "trade_decisions"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    risk_decision_id: Mapped[str | None] = mapped_column(ForeignKey("risk_decisions.id"), nullable=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.order_id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String)  # "traded" | "rejected" | "skipped"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    severity: Mapped[str] = mapped_column(String, default="info")
    source: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------
# Government contract intelligence domain (PRD 2 section 9, US-scoped)
# --------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    org_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    canonical_name: Mapped[str] = mapped_column(String, unique=True)
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[str]
    country: Mapped[str] = mapped_column(String, default="US")
    organization_type: Mapped[str] = mapped_column(String, default="vendor")


class GovernmentAgency(Base):
    __tablename__ = "government_agencies"

    agency_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    canonical_name: Mapped[str] = mapped_column(String, unique=True)
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_agency_id: Mapped[str | None] = mapped_column(
        ForeignKey("government_agencies.agency_id"), nullable=True
    )
    jurisdiction: Mapped[str] = mapped_column(String, default="US")


class SourceDocument(Base):
    __tablename__ = "source_documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    document_type: Mapped[str] = mapped_column(String)
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Contract(Base):
    __tablename__ = "contracts"

    contract_id: Mapped[str] = mapped_column(String, primary_key=True)  # stable source award id
    title: Mapped[str] = mapped_column(Text)
    awarding_agency_id: Mapped[str | None] = mapped_column(
        ForeignKey("government_agencies.agency_id"), nullable=True
    )
    vendor_org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.org_id"), nullable=True)
    award_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_type: Mapped[str] = mapped_column(String, default="disclosed_award")
    currency: Mapped[str] = mapped_column(String, default="USD")
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    procurement_method: Mapped[str | None] = mapped_column(String, nullable=True)
    jurisdiction: Mapped[str] = mapped_column(String, default="US")
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id"), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContractEvent(Base):
    __tablename__ = "contract_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.contract_id"))
    event_type: Mapped[str] = mapped_column(String)  # award | amendment | extension | renewal | cancellation
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    value_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------
# Congressional trading domain
# --------------------------------------------------------------------------


class Legislator(Base):
    __tablename__ = "legislators"

    legislator_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String)
    chamber: Mapped[str] = mapped_column(String)  # "house" | "senate"
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    party: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("full_name", "chamber", name="uq_legislator"),)


class CongressTrade(Base):
    """A single disclosed transaction from a STOCK Act periodic transaction
    report. transaction_date is when the trade happened; disclosure_date is
    when it became public -- the gap between them (statutorily up to 45
    days) is preserved, never collapsed, because it determines whether a
    mirror strategy is even chasing information that's still actionable.
    """

    __tablename__ = "congress_trades"

    trade_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    legislator_id: Mapped[str] = mapped_column(ForeignKey("legislators.legislator_id"))
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_type: Mapped[str] = mapped_column(String)  # purchase | sale_full | sale_partial | exchange
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disclosure_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount_range_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_range_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint(
            "legislator_id",
            "ticker",
            "transaction_date",
            "transaction_type",
            "amount_range_low",
            name="uq_congress_trade",
        ),
    )

    legislator: Mapped["Legislator"] = relationship()
