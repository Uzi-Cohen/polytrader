"""Runs the backtest pipeline against the real, live Senate disclosure
feed (thousands of real transactions, hundreds of real tickers) to prove
it doesn't choke on real-world data shape/scale -- not to assert real P&L,
since Stooq (real historical prices) isn't reachable from every
environment this test might run in. A synthetic flat price series stands
in for that half; see test_backtest.py for price-sensitive assertions
against controlled fixtures. Skips gracefully wherever network access
isn't available so it never blocks a CI run without egress.
"""
from datetime import date

import httpx
import pytest

from polytrader.congress.backtest import run_backtest
from polytrader.congress.price_history import PricePoint
from polytrader.congress.senate_client import SenateStockWatcherClient
from polytrader.core.risk import RiskLimits


def _network_available() -> bool:
    try:
        httpx.get("https://raw.githubusercontent.com", timeout=5)
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _network_available(), reason="raw.githubusercontent.com not reachable")


class _FlatPriceClient:
    """Every ticker prices flat at 100.0 -- isolates the test from real
    market moves so it only proves the pipeline runs, not that any
    particular return is correct."""

    def get_daily_history(self, ticker: str) -> list[PricePoint]:
        return [PricePoint(date(2000, 1, 1), 100.0)]


def make_limits() -> RiskLimits:
    return RiskLimits(
        max_risk_per_trade_pct=0.02,
        kelly_fraction=0.35,
        max_total_exposure_pct=0.50,
        max_market_concentration_pct=0.08,
        max_category_concentration_pct=0.25,
        daily_loss_pause_pct=0.05,
        max_drawdown_halt_pct=0.18,
        min_adjusted_edge=0.03,
        probability_extreme_guard=0.90,
        max_data_staleness_seconds=900,
        thesis_cooldown_hours=24,
        kill_switch=False,
    )


def test_backtest_runs_against_real_disclosure_history_without_error():
    with SenateStockWatcherClient() as client:
        trades = client.fetch_all_transactions()

    report = run_backtest(
        trades,
        start_date=date(2013, 1, 1),
        end_date=date(2024, 12, 31),
        starting_bankroll=100_000.0,
        base_mirror_unit=250.0,
        risk_limits=make_limits(),
        price_client=_FlatPriceClient(),
        fee_bps=10.0,
    )

    assert report.considered > 1000  # real feed has thousands of transactions
    assert report.skipped_not_actionable > 0  # bonds/funds with no resolvable ticker, always present
    assert len(report.closed_trades) > 0
    # flat prices + a flat fee drag should never produce a "win"
    assert report.win_rate == 0.0
    assert report.ending_bankroll <= report.starting_bankroll
