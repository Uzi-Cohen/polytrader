"""Historical daily close prices via Stooq (free, no key, no auth).

Used only for backtesting the congress-mirror strategy against real
historical prices (congress/backtest.py). Not used anywhere in live/paper
mode -- mirror_strategy.py deliberately has no live price feed wired in
(see its module docstring), so this stays backtest-only.

Not live-tested from inside this build's sandbox: stooq.com is blocked by
org egress policy (confirmed 403 at the proxy's CONNECT layer, the same
pattern as every other host this project talks to except GitHub's).
Covered by fixture-based parsing tests against Stooq's documented CSV
format; verify against a live response the first time this runs
somewhere that can reach the host.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

import httpx

STOOQ_BASE_URL = "https://stooq.com"


@dataclass(frozen=True)
class PricePoint:
    trade_date: date
    close: float


def _parse_csv(text: str) -> list[PricePoint]:
    if not text or text.lstrip().startswith("<") or "No data" in text[:80]:
        return []
    points = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            trade_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            close = float(row["Close"])
        except (KeyError, ValueError, TypeError):
            continue
        points.append(PricePoint(trade_date=trade_date, close=close))
    points.sort(key=lambda p: p.trade_date)
    return points


def price_on_or_before(points: list[PricePoint], target: date) -> float | None:
    """The most recent close at or before `target` -- never a future
    price, since using one would be lookahead bias in a backtest."""
    candidates = [p for p in points if p.trade_date <= target]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.trade_date).close


class StooqClient:
    def __init__(self, base_url: str = STOOQ_BASE_URL, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StooqClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get_daily_history(self, ticker: str) -> list[PricePoint]:
        """Full available daily close history for `ticker`, ascending by
        date. Empty for symbols Stooq doesn't carry -- common for the
        thinly-traded/OTC securities that show up in STOCK Act filings;
        callers must treat that as 'can't price this trade,' not an
        error."""
        response = self._client.get("/q/d/l/", params={"s": f"{ticker.lower()}.us", "i": "d"})
        response.raise_for_status()
        return _parse_csv(response.text)
