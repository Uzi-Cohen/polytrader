"""Orchestrates Senate/House ingestion into the canonical congress-trade
store. Publishes a `congress_trade` DomainEvent per newly-stored
disclosure for signals/matcher.py and congress/mirror_strategy.py.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.congress.house_client import HouseFilingIndexEntry
from polytrader.congress.senate_client import SenateTrade
from polytrader.core.events import DomainEvent, get_event_bus
from polytrader.core.models import CongressTrade, Legislator, SourceDocument


def _as_datetime(d: date | None) -> datetime | None:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def resolve_legislator(session: Session, full_name: str, chamber: str) -> Legislator:
    legislator = session.scalars(
        select(Legislator).where(Legislator.full_name == full_name, Legislator.chamber == chamber)
    ).first()
    if legislator is None:
        legislator = Legislator(full_name=full_name, chamber=chamber)
        session.add(legislator)
        session.flush()
    return legislator


def ingest_senate_trade(session: Session, trade: SenateTrade) -> CongressTrade | None:
    """Returns None if this exact trade is already stored (idempotent on
    the natural key legislator+ticker+date+type+amount)."""
    legislator = resolve_legislator(session, trade.senator, "senate")
    transaction_date = _as_datetime(trade.transaction_date)

    existing = session.scalars(
        select(CongressTrade).where(
            CongressTrade.legislator_id == legislator.legislator_id,
            CongressTrade.ticker == trade.ticker,
            CongressTrade.transaction_date == transaction_date,
            CongressTrade.transaction_type == trade.transaction_type,
            CongressTrade.amount_range_low == trade.amount_low,
        )
    ).first()
    if existing is not None:
        # A later daily-filing fetch can supply the disclosure_date the
        # bulk aggregate feed didn't have -- backfill it instead of
        # skipping, since that's the one field mirror_strategy needs.
        if existing.disclosure_date is None and trade.disclosure_date is not None:
            existing.disclosure_date = _as_datetime(trade.disclosure_date)
            session.flush()
        return None

    source = None
    if trade.ptr_link:
        source = SourceDocument(
            url=trade.ptr_link,
            source_name="senate-stock-watcher-data / efdsearch.senate.gov",
            document_type="periodic_transaction_report",
            ingestion_timestamp=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()

    congress_trade = CongressTrade(
        legislator_id=legislator.legislator_id,
        ticker=trade.ticker,
        asset_description=trade.asset_description,
        transaction_type=trade.transaction_type,
        transaction_date=transaction_date,
        disclosure_date=_as_datetime(trade.disclosure_date),
        amount_range_low=trade.amount_low,
        amount_range_high=trade.amount_high,
        source_document_id=source.document_id if source else None,
        source_url=trade.ptr_link,
    )
    session.add(congress_trade)
    session.flush()

    get_event_bus().publish(
        DomainEvent(
            event_type="congress_trade",
            payload={
                "trade_id": congress_trade.trade_id,
                "legislator": legislator.full_name,
                "chamber": legislator.chamber,
                "ticker": congress_trade.ticker,
                "transaction_type": congress_trade.transaction_type,
                "transaction_date": trade.transaction_date.isoformat(),
                "disclosure_date": trade.disclosure_date.isoformat() if trade.disclosure_date else None,
            },
        )
    )
    return congress_trade


def ingest_house_filing_index_entry(session: Session, entry: HouseFilingIndexEntry) -> SourceDocument:
    """Records that a filing happened -- real filer/date/PDF-link metadata
    -- without fabricating the transactions inside it. See
    congress/house_client.py for why."""
    full_name = f"{entry.first_name} {entry.last_name}".strip()
    resolve_legislator(session, full_name, "house")

    source = SourceDocument(
        url=entry.pdf_url,
        source_name="house_clerk_financial_disclosures",
        document_type=f"house_{entry.filing_type.lower().replace(' ', '_')}",
        publication_date=_as_datetime(entry.filing_date),
        ingestion_timestamp=datetime.now(timezone.utc),
    )
    session.add(source)
    session.flush()
    return source
