import pytest

from polytrader.core.models import Market
from polytrader.core.portfolio import build_portfolio_state
from polytrader.polymarket.paper_portfolio import mark_to_market, paper_buy, paper_sell
from polytrader.core.models import AssetClass


def seed_market(db_session, market_id="m1", category="Politics"):
    db_session.add(Market(market_id=market_id, question="Q?", category=category))
    db_session.flush()


def test_portfolio_state_reflects_starting_bankroll_with_no_positions(db_session):
    state = build_portfolio_state(db_session, asset_class=AssetClass.POLYMARKET, starting_bankroll=10_000.0)
    assert state.bankroll == pytest.approx(10_000.0)
    assert state.total_exposure == 0.0
    assert state.market_exposure == {}
    assert state.category_exposure == {}


def test_portfolio_state_tracks_open_exposure_and_category(db_session):
    seed_market(db_session, "m1", category="Politics")
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=400.0)

    state = build_portfolio_state(db_session, asset_class=AssetClass.POLYMARKET, starting_bankroll=10_000.0)
    assert state.total_exposure == pytest.approx(400.0)
    assert state.market_exposure["m1"] == pytest.approx(400.0)
    assert state.category_exposure["Politics"] == pytest.approx(400.0)
    # equity unchanged until marked or sold -- cost basis in, no P&L yet
    assert state.bankroll == pytest.approx(10_000.0)


def test_portfolio_state_reflects_realized_and_unrealized_pnl(db_session):
    seed_market(db_session, "m1")
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=400.0)  # 1000 shares
    mark_to_market(db_session, market_id="m1", yes_price=0.50, no_price=0.50)

    state = build_portfolio_state(db_session, asset_class=AssetClass.POLYMARKET, starting_bankroll=10_000.0)
    # unrealized: (0.50-0.40)*1000 = 100
    assert state.bankroll == pytest.approx(10_100.0)

    paper_sell(db_session, market_id="m1", side="yes", price=0.50, shares=1000.0)
    state_after_sell = build_portfolio_state(db_session, asset_class=AssetClass.POLYMARKET, starting_bankroll=10_000.0)
    assert state_after_sell.bankroll == pytest.approx(10_100.0)
    assert state_after_sell.total_exposure == 0.0  # position closed
