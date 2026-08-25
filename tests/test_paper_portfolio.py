import pytest

from polytrader.core.models import Market
from polytrader.polymarket.paper_portfolio import get_open_position, mark_to_market, paper_buy, paper_sell


def seed_market(db_session, market_id="m1"):
    db_session.add(Market(market_id=market_id, question="Q?", category="Politics"))
    db_session.flush()


def test_paper_buy_creates_position_with_correct_shares(db_session):
    seed_market(db_session)
    position = paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)
    assert position.size == pytest.approx(250.0)  # 100 / 0.40
    assert position.avg_entry == pytest.approx(0.40)
    assert position.closed_at is None


def test_paper_buy_averages_entry_price_on_second_buy(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)  # 250 shares
    position = paper_buy(db_session, market_id="m1", side="yes", price=0.60, notional=60.0)  # +100 shares
    # weighted avg = (100 + 60) / (250 + 100) = 160/350
    assert position.size == pytest.approx(350.0)
    assert position.avg_entry == pytest.approx(160.0 / 350.0)


def test_paper_sell_realizes_pnl_and_reduces_size(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)  # 250 shares @ .40
    position = paper_sell(db_session, market_id="m1", side="yes", price=0.60, shares=100.0)
    assert position.realized_pnl == pytest.approx((0.60 - 0.40) * 100.0)
    assert position.size == pytest.approx(150.0)
    assert position.closed_at is None


def test_paper_sell_full_size_closes_position(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)  # 250 shares
    position = paper_sell(db_session, market_id="m1", side="yes", price=0.50, shares=250.0)
    assert position.size == 0.0
    assert position.closed_at is not None
    assert get_open_position(db_session, "m1", "yes") is None


def test_paper_sell_more_than_held_raises(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)
    with pytest.raises(ValueError):
        paper_sell(db_session, market_id="m1", side="yes", price=0.50, shares=999.0)


def test_can_reopen_position_after_close(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)
    paper_sell(db_session, market_id="m1", side="yes", price=0.50, shares=250.0)
    assert get_open_position(db_session, "m1", "yes") is None

    reopened = paper_buy(db_session, market_id="m1", side="yes", price=0.30, notional=30.0)
    assert reopened.closed_at is None
    assert reopened.size == pytest.approx(100.0)


def test_mark_to_market_updates_unrealized_pnl(db_session):
    seed_market(db_session)
    paper_buy(db_session, market_id="m1", side="yes", price=0.40, notional=100.0)  # 250 shares
    updated = mark_to_market(db_session, market_id="m1", yes_price=0.55, no_price=0.45)
    assert len(updated) == 1
    assert updated[0].unrealized_pnl == pytest.approx((0.55 - 0.40) * 250.0)


def test_paper_buy_rejects_out_of_range_price(db_session):
    seed_market(db_session)
    with pytest.raises(ValueError):
        paper_buy(db_session, market_id="m1", side="yes", price=1.5, notional=10.0)
