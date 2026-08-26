from datetime import date

from polytrader.congress.backtest import run_backtest
from polytrader.congress.price_history import PricePoint
from polytrader.congress.senate_client import SenateTrade
from polytrader.core.risk import RiskLimits


def make_limits(**overrides) -> RiskLimits:
    defaults = dict(
        max_risk_per_trade_pct=0.05,
        kelly_fraction=0.35,
        max_total_exposure_pct=0.50,
        max_market_concentration_pct=0.20,
        max_category_concentration_pct=0.50,
        daily_loss_pause_pct=0.05,
        max_drawdown_halt_pct=0.50,
        min_adjusted_edge=0.03,
        probability_extreme_guard=0.90,
        max_data_staleness_seconds=900,
        thesis_cooldown_hours=24,
        kill_switch=False,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def make_trade(**overrides) -> SenateTrade:
    defaults = dict(
        senator="Test Senator",
        ticker="ACME",
        asset_description="Acme Corp",
        transaction_type="purchase",
        transaction_date=date(2020, 1, 2),
        disclosure_date=None,
        amount_low=50_001.0,
        amount_high=100_000.0,
        ptr_link="https://example.com/ptr/1",
    )
    defaults.update(overrides)
    return SenateTrade(**defaults)


class FakePriceClient:
    def __init__(self, series: dict[str, list[PricePoint]]):
        self._series = series

    def get_daily_history(self, ticker: str) -> list[PricePoint]:
        return self._series.get(ticker, [])


def test_profitable_round_trip_records_positive_pnl():
    trades = [
        make_trade(ticker="ACME", transaction_type="purchase", transaction_date=date(2020, 1, 2)),
        make_trade(ticker="ACME", transaction_type="sale_full", transaction_date=date(2020, 2, 1)),
    ]
    prices = FakePriceClient(
        {
            "ACME": [
                PricePoint(date(2020, 2, 1), 10.0),  # entry price on the assumed disclosure date of the buy
                PricePoint(date(2020, 3, 2), 20.0),  # exit price on the assumed disclosure date of the sell
            ]
        }
    )

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=prices,
        fee_bps=0.0,
        assumed_disclosure_lag_days=30,
    )

    assert len(report.closed_trades) == 1
    trade = report.closed_trades[0]
    assert trade.pnl > 0
    assert trade.exit_reason == "sold_by_legislator"
    assert report.win_rate == 1.0
    assert report.ending_bankroll > report.starting_bankroll


def test_losing_round_trip_records_negative_pnl():
    trades = [
        make_trade(ticker="ACME", transaction_type="purchase", transaction_date=date(2020, 1, 2)),
        make_trade(ticker="ACME", transaction_type="sale_full", transaction_date=date(2020, 2, 1)),
    ]
    prices = FakePriceClient(
        {"ACME": [PricePoint(date(2020, 2, 1), 20.0), PricePoint(date(2020, 3, 2), 10.0)]}
    )

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=prices,
        fee_bps=0.0,
    )

    assert len(report.closed_trades) == 1
    assert report.closed_trades[0].pnl < 0
    assert report.win_rate == 0.0
    assert report.ending_bankroll < report.starting_bankroll


def test_open_position_marked_to_market_at_backtest_end():
    trades = [make_trade(ticker="ACME", transaction_type="purchase", transaction_date=date(2020, 1, 2))]
    prices = FakePriceClient(
        {"ACME": [PricePoint(date(2020, 2, 1), 10.0), PricePoint(date(2020, 12, 31), 15.0)]}
    )

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=prices,
        fee_bps=0.0,
    )

    assert len(report.closed_trades) == 1
    assert report.closed_trades[0].exit_reason == "backtest_end"
    assert report.closed_trades[0].exit_date == date(2020, 12, 31)


def test_missing_price_data_is_counted_not_silently_dropped():
    trades = [make_trade(ticker="UNKNOWNTICKER")]
    prices = FakePriceClient({})  # no data for this ticker at all

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=prices,
    )

    assert report.considered == 1
    assert report.skipped_no_price_data == 1
    assert report.closed_trades == []


def test_non_actionable_trades_are_counted():
    trades = [make_trade(ticker=None)]  # e.g. a bond with unresolvable ticker
    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=FakePriceClient({}),
    )
    assert report.skipped_not_actionable == 1
    assert report.considered == 1


def test_risk_engine_rejections_are_counted_not_silently_traded():
    trades = [make_trade(ticker="ACME", amount_low=25_000_001.0, amount_high=50_000_000.0)]
    prices = FakePriceClient({"ACME": [PricePoint(date(2020, 2, 1), 10.0)]})

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(kill_switch=True),
        price_client=prices,
    )
    assert report.skipped_by_risk_engine == 1
    assert report.closed_trades == []
    assert report.ending_bankroll == report.starting_bankroll


def test_trades_outside_date_range_are_excluded():
    trades = [make_trade(ticker="ACME", transaction_date=date(2015, 1, 1))]
    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=FakePriceClient({}),
    )
    assert report.considered == 0


def test_max_drawdown_and_total_return_reflect_the_equity_curve():
    trades = [
        make_trade(ticker="A", transaction_date=date(2020, 1, 2), transaction_type="purchase"),
        make_trade(ticker="A", transaction_date=date(2020, 2, 1), transaction_type="sale_full"),
        make_trade(ticker="B", transaction_date=date(2020, 3, 1), transaction_type="purchase"),
        make_trade(ticker="B", transaction_date=date(2020, 4, 1), transaction_type="sale_full"),
    ]
    prices = FakePriceClient(
        {
            "A": [PricePoint(date(2020, 2, 1), 10.0), PricePoint(date(2020, 3, 2), 5.0)],  # loses
            "B": [PricePoint(date(2020, 3, 31), 10.0), PricePoint(date(2020, 5, 1), 30.0)],  # wins big
        }
    )

    report = run_backtest(
        trades,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=1_000.0,
        risk_limits=make_limits(),
        price_client=prices,
        fee_bps=0.0,
    )

    assert len(report.closed_trades) == 2
    assert report.total_return_pct == ((report.ending_bankroll / report.starting_bankroll) - 1) * 100
    assert report.max_drawdown_pct >= 0.0
