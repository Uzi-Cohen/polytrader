from datetime import date

from polytrader.contracts.ingest import ingest_contract_award
from polytrader.contracts.usaspending_client import ContractAward
from polytrader.congress.ingest import ingest_senate_trade
from polytrader.congress.senate_client import SenateTrade
from polytrader.core.models import Market, Signal, SignalSourceType
from polytrader.signals.matcher import find_candidate_markets, record_candidate_signals, wire_signal_matcher


def seed_market(db_session, market_id, question, category="Politics"):
    db_session.add(Market(market_id=market_id, question=question, category=category, status="active"))
    db_session.flush()


def test_find_candidate_markets_requires_min_overlap(db_session):
    seed_market(db_session, "m1", "Will the Department of Defense expand AI contracts before 2027?")
    payload = {"agency": "Department of Defense", "vendor": "Example Corp", "title": "AI systems contract"}

    strong = find_candidate_markets(db_session, "contract_award", payload, min_matches=2)
    assert len(strong) == 1
    assert strong[0].market_id == "m1"

    too_strict = find_candidate_markets(db_session, "contract_award", payload, min_matches=10)
    assert too_strict == []


def test_find_candidate_markets_ignores_unrelated_markets(db_session):
    seed_market(db_session, "m1", "Will it rain in Miami tomorrow?", category="Weather")
    payload = {"agency": "Department of Defense", "vendor": "Example Corp", "title": "AI systems contract"}
    assert find_candidate_markets(db_session, "contract_award", payload, min_matches=2) == []


def test_find_candidate_markets_matches_congress_trade_ticker_and_name(db_session):
    seed_market(db_session, "m1", "Will Nvidia stock be mentioned in a Congressional NVDA insider trading probe?")
    payload = {"ticker": "NVDA", "legislator": "Nancy Pelosi"}
    matches = find_candidate_markets(db_session, "congress_trade", payload, min_matches=1)
    assert len(matches) == 1


def test_record_candidate_signals_persists_unknown_direction(db_session):
    seed_market(db_session, "m1", "Will the Department of Defense expand AI contracts before 2027?")
    payload = {"agency": "Department of Defense", "vendor": "Example Corp", "title": "AI systems contract", "contract_id": "c1"}

    signals = record_candidate_signals(db_session, "contract_award", payload, min_matches=2)

    assert len(signals) == 1
    assert signals[0].direction == "unknown"
    assert signals[0].source_type == SignalSourceType.CONTRACT_AWARD
    assert signals[0].market_id == "m1"
    stored = db_session.query(Signal).all()
    assert len(stored) == 1


def test_wire_signal_matcher_reacts_to_real_ingestion_events(db_session):
    seed_market(db_session, "m1", "Will the Department of Defense expand AI contracts before 2027?")
    wire_signal_matcher(db_session, min_matches=2)

    award = ContractAward(
        award_id="A1",
        generated_internal_id="CONT_AWD_1",
        title="AI systems contract",
        recipient_name="Example Corp",
        awarding_agency="Department of Defense",
        awarding_sub_agency=None,
        amount=1_000_000.0,
        start_date=date(2026, 1, 1),
        end_date=None,
        permalink="https://www.usaspending.gov/award/CONT_AWD_1",
        raw={},
    )
    ingest_contract_award(db_session, award)

    signals = db_session.query(Signal).filter_by(source_type=SignalSourceType.CONTRACT_AWARD).all()
    assert len(signals) == 1
    assert signals[0].market_id == "m1"


def test_wire_signal_matcher_reacts_to_congress_trades(db_session):
    seed_market(db_session, "m1", "Will Nvidia stock be mentioned in a Congressional NVDA insider trading probe?")
    wire_signal_matcher(db_session, min_matches=1)

    trade = SenateTrade(
        senator="Ron L Wyden",
        ticker="NVDA",
        asset_description="NVIDIA Corp",
        transaction_type="purchase",
        transaction_date=date(2026, 8, 1),
        disclosure_date=date(2026, 8, 15),
        amount_low=50_001.0,
        amount_high=100_000.0,
        ptr_link="https://efdsearch.senate.gov/search/view/ptr/x/",
    )
    ingest_senate_trade(db_session, trade)

    signals = db_session.query(Signal).filter_by(source_type=SignalSourceType.CONGRESS_TRADE).all()
    assert len(signals) == 1
