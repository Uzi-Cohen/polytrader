from datetime import datetime, timedelta, timezone

import pytest

from polytrader.congress.mirror_strategy import (
    build_mirror_proposal,
    get_open_equity_position,
    mirror_trade,
    paper_mirror_buy,
    paper_mirror_sell,
)
from polytrader.core.models import CongressTrade, Legislator, Order, RiskDecisionOutcome
from polytrader.core.risk import PortfolioState, RiskEngine, RiskLimits

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def make_limits(**overrides) -> RiskLimits:
    defaults = dict(
        max_risk_per_trade_pct=0.02,
        kelly_fraction=0.35,
        max_total_exposure_pct=0.50,
        max_market_concentration_pct=0.08,
        max_category_concentration_pct=0.25,
        daily_loss_pause_pct=0.05,
        max_drawdown_halt_pct=0.18,
        min_adjusted_edge=0.03,
        probability_extreme_guard=0.90,
        max_data_staleness_seconds=60 * 60 * 24 * 60,  # 60 days, generous for this strategy's lag
        thesis_cooldown_hours=24,
        kill_switch=False,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def make_congress_trade(db_session, **overrides) -> CongressTrade:
    legislator = db_session.query(Legislator).filter_by(full_name="Nancy Pelosi", chamber="house").first()
    if legislator is None:
        legislator = Legislator(full_name="Nancy Pelosi", chamber="house")
        db_session.add(legislator)
        db_session.flush()

    defaults = dict(
        legislator_id=legislator.legislator_id,
        ticker="NVDA",
        asset_description="NVIDIA Corp",
        transaction_type="purchase",
        transaction_date=NOW - timedelta(days=10),
        disclosure_date=NOW - timedelta(days=2),
        amount_range_low=50_001.0,
        amount_range_high=100_000.0,
        source_url="https://example.com/ptr/1",
    )
    defaults.update(overrides)
    trade = CongressTrade(**defaults)
    db_session.add(trade)
    db_session.flush()
    return trade


def test_build_mirror_proposal_none_without_ticker(db_session):
    trade = make_congress_trade(db_session, ticker=None)
    assert build_mirror_proposal(trade, base_mirror_unit=500.0, now=NOW) is None


def test_build_mirror_proposal_none_without_disclosure_date(db_session):
    trade = make_congress_trade(db_session, disclosure_date=None)
    assert build_mirror_proposal(trade, base_mirror_unit=500.0, now=NOW) is None


def test_build_mirror_proposal_buy_direction_for_purchase(db_session):
    trade = make_congress_trade(db_session, transaction_type="purchase")
    proposal = build_mirror_proposal(trade, base_mirror_unit=500.0, now=NOW)
    assert proposal.direction == "buy"
    assert proposal.trade_proposal.proposed_notional > 0


def test_build_mirror_proposal_sell_direction_for_sale(db_session):
    trade = make_congress_trade(db_session, transaction_type="sale_full")
    proposal = build_mirror_proposal(trade, base_mirror_unit=500.0, now=NOW)
    assert proposal.direction == "sell"


def test_edge_decays_with_disclosure_lag(db_session):
    fresh = make_congress_trade(
        db_session, transaction_date=NOW - timedelta(days=2), disclosure_date=NOW - timedelta(days=1)
    )
    stale = make_congress_trade(
        db_session, transaction_date=NOW - timedelta(days=41), disclosure_date=NOW - timedelta(days=40)
    )

    fresh_proposal = build_mirror_proposal(fresh, base_mirror_unit=500.0, now=NOW)
    stale_proposal = build_mirror_proposal(stale, base_mirror_unit=500.0, now=NOW)

    assert fresh_proposal.trade_proposal.adjusted_edge > stale_proposal.trade_proposal.adjusted_edge


def test_larger_disclosed_amount_increases_conviction_and_size(db_session):
    small = make_congress_trade(db_session, amount_range_low=1_001.0, amount_range_high=15_000.0)
    large = make_congress_trade(db_session, amount_range_low=1_000_001.0, amount_range_high=5_000_000.0)

    small_proposal = build_mirror_proposal(small, base_mirror_unit=500.0, now=NOW)
    large_proposal = build_mirror_proposal(large, base_mirror_unit=500.0, now=NOW)

    assert large_proposal.trade_proposal.proposed_notional > small_proposal.trade_proposal.proposed_notional


def test_paper_mirror_buy_accumulates_notional(db_session):
    paper_mirror_buy(db_session, symbol="NVDA", notional=100.0)
    position = paper_mirror_buy(db_session, symbol="NVDA", notional=50.0)
    assert position.size == pytest.approx(150.0)
    assert position.avg_entry == 1.0  # no live price feed -- notional placeholder, not a real price


def test_paper_mirror_sell_reduces_and_closes_position(db_session):
    paper_mirror_buy(db_session, symbol="NVDA", notional=100.0)
    position = paper_mirror_sell(db_session, symbol="NVDA", notional=100.0)
    assert position.size == 0.0
    assert position.closed_at is not None
    assert get_open_equity_position(db_session, "NVDA") is None


def test_paper_mirror_sell_with_nothing_held_returns_none(db_session):
    assert paper_mirror_sell(db_session, symbol="NVDA", notional=100.0) is None


def test_mirror_trade_skipped_when_not_actionable(db_session):
    trade = make_congress_trade(db_session, ticker=None)
    engine = RiskEngine(make_limits())
    portfolio = PortfolioState(bankroll=100_000.0)

    result = mirror_trade(db_session, trade, risk_engine=engine, portfolio=portfolio, base_mirror_unit=500.0, now=NOW)
    assert result["outcome"] == "skipped"


def test_mirror_trade_rejected_by_kill_switch(db_session):
    trade = make_congress_trade(db_session)
    engine = RiskEngine(make_limits(kill_switch=True))
    portfolio = PortfolioState(bankroll=100_000.0)

    result = mirror_trade(db_session, trade, risk_engine=engine, portfolio=portfolio, base_mirror_unit=500.0, now=NOW)
    assert result["outcome"] == "rejected"


def test_mirror_trade_executes_paper_buy_and_records_full_chain(db_session):
    trade = make_congress_trade(db_session, transaction_type="purchase", disclosure_date=NOW - timedelta(days=1))
    engine = RiskEngine(make_limits())
    portfolio = PortfolioState(bankroll=100_000.0)

    result = mirror_trade(db_session, trade, risk_engine=engine, portfolio=portfolio, base_mirror_unit=500.0, now=NOW)

    assert result["outcome"] == "traded"
    assert result["ticker"] == "NVDA"
    assert result["direction"] == "buy"

    position = get_open_equity_position(db_session, "NVDA")
    assert position is not None
    assert position.size == pytest.approx(result["approved_size"])

    order = db_session.query(Order).filter_by(idempotency_key=f"mirror:{trade.trade_id}").one()
    assert order.status.value == "filled" if hasattr(order.status, "value") else order.status == "filled"


def test_mirror_trade_respects_max_risk_per_trade_cap(db_session):
    trade = make_congress_trade(
        db_session, amount_range_low=25_000_001.0, amount_range_high=50_000_000.0
    )  # max conviction multiplier
    engine = RiskEngine(make_limits(max_risk_per_trade_pct=0.001))  # very tight cap
    portfolio = PortfolioState(bankroll=100_000.0)

    result = mirror_trade(db_session, trade, risk_engine=engine, portfolio=portfolio, base_mirror_unit=5_000.0, now=NOW)

    assert result["outcome"] == "traded"
    assert result["approved_size"] <= 100.0 + 1e-6  # 0.1% of 100,000
