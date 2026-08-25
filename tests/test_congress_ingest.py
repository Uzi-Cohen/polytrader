from datetime import date

from polytrader.congress.house_client import HouseFilingIndexEntry
from polytrader.congress.ingest import ingest_house_filing_index_entry, ingest_senate_trade
from polytrader.congress.senate_client import SenateTrade
from polytrader.core.events import get_event_bus
from polytrader.core.models import CongressTrade, Legislator


def make_trade(**overrides) -> SenateTrade:
    defaults = dict(
        senator="Ron L Wyden",
        ticker="BYND",
        asset_description="Beyond Meat, Inc.",
        transaction_type="sale_full",
        transaction_date=date(2020, 11, 10),
        disclosure_date=None,
        amount_low=50_001.0,
        amount_high=100_000.0,
        ptr_link="https://efdsearch.senate.gov/search/view/ptr/abc/",
    )
    defaults.update(overrides)
    return SenateTrade(**defaults)


def test_ingest_senate_trade_creates_legislator_and_trade(db_session):
    trade = ingest_senate_trade(db_session, make_trade())
    assert trade is not None
    legislator = db_session.get(Legislator, trade.legislator_id)
    assert legislator.full_name == "Ron L Wyden"
    assert legislator.chamber == "senate"
    assert trade.ticker == "BYND"
    assert trade.disclosure_date is None


def test_ingest_senate_trade_is_idempotent(db_session):
    ingest_senate_trade(db_session, make_trade())
    result = ingest_senate_trade(db_session, make_trade())
    assert result is None
    assert db_session.query(CongressTrade).count() == 1


def test_ingest_senate_trade_backfills_disclosure_date_from_daily_filing(db_session):
    ingest_senate_trade(db_session, make_trade(disclosure_date=None))
    result = ingest_senate_trade(db_session, make_trade(disclosure_date=date(2020, 11, 12)))

    assert result is None  # still treated as the same trade, not a duplicate
    stored = db_session.query(CongressTrade).one()
    assert stored.disclosure_date is not None


def test_ingest_senate_trade_publishes_domain_event(db_session):
    received = []
    get_event_bus().subscribe("congress_trade", received.append)

    ingest_senate_trade(db_session, make_trade())

    assert len(received) == 1
    assert received[0].payload["ticker"] == "BYND"
    assert received[0].payload["chamber"] == "senate"


def test_ingest_house_filing_index_entry_creates_legislator_and_source(db_session):
    entry = HouseFilingIndexEntry(
        first_name="Nancy",
        last_name="Pelosi",
        state_district="CA11",
        filing_type="P",
        filing_date=date(2026, 8, 15),
        doc_id="20026001",
        pdf_url="https://disclosures-clerk.house.gov/public_disc/financial-pdfs/20026001.pdf",
    )
    source = ingest_house_filing_index_entry(db_session, entry)
    assert source.url == entry.pdf_url

    legislator = db_session.query(Legislator).filter_by(full_name="Nancy Pelosi", chamber="house").one()
    assert legislator is not None
