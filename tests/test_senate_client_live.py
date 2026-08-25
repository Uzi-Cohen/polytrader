"""One deliberately live test: the Senate connector is the one external
data source in this whole project confirmed reachable from this build's
sandbox (raw.githubusercontent.com; every other host -- Polymarket,
USASpending, Federal Register, House Clerk, Senate eFD itself -- is
blocked by org egress policy). Everything else is fixture-tested; this
proves the parsing actually works against today's real feed, not just a
frozen sample. Skips gracefully wherever network access isn't available
so it never blocks a CI run without egress.
"""
from datetime import date

import httpx
import pytest

from polytrader.congress.senate_client import SenateStockWatcherClient


def _network_available() -> bool:
    try:
        httpx.get("https://raw.githubusercontent.com", timeout=5)
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _network_available(), reason="raw.githubusercontent.com not reachable")


def test_fetch_all_transactions_returns_real_parsed_trades():
    with SenateStockWatcherClient() as client:
        trades = client.fetch_all_transactions()

    assert len(trades) > 1000  # the real feed has several thousand records
    assert any(t.ticker for t in trades)  # some real tickers parsed out
    assert all(isinstance(t.transaction_date, date) for t in trades)


def test_fetch_daily_filing_returns_disclosure_dated_trades():
    with SenateStockWatcherClient() as client:
        # a date with confirmed real filings, captured during development
        trades = client.fetch_daily_filing(date(2019, 1, 3))

    assert len(trades) > 0
    assert all(t.disclosure_date == date(2019, 1, 3) for t in trades)
