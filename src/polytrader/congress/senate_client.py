"""Client for Senate STOCK Act trade disclosures.

The official source is efdsearch.senate.gov, which has no clean API --
just a filing-search UI backed by PTR documents. This connector instead
uses `timothycarambat/senate-stock-watcher-data`, a GitHub-hosted JSON
mirror of those filings, confirmed actively maintained. It is also the
one external data source in this whole project confirmed *live-reachable*
from this build's sandbox (raw.githubusercontent.com is allowed; every
other host this project talks to -- Polymarket, USASpending, Federal
Register, House Clerk, Senate eFD itself -- is blocked by org egress
policy). The parsing here was built and checked against real fetched
samples of both feeds during development.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

SENATE_STOCK_WATCHER_BASE = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master"
)

_HTML_TAG = re.compile(r"<[^>]+>")
_AMOUNT_RANGE = re.compile(r"\$?([\d,]+)\s*-\s*\$?([\d,]+)")

# The feed's `type` values, lowercased, mapped onto our transaction_type
# vocabulary (core/models.py CongressTrade.transaction_type).
_TYPE_MAP = {
    "purchase": "purchase",
    "sale (full)": "sale_full",
    "sale (partial)": "sale_partial",
    "exchange": "exchange",
}


@dataclass(frozen=True)
class SenateTrade:
    senator: str
    ticker: str | None
    asset_description: str
    transaction_type: str
    transaction_date: date
    disclosure_date: date | None
    amount_low: float | None
    amount_high: float | None
    ptr_link: str


def _strip_html(value: str | None) -> str:
    """Asset descriptions and (rarely) tickers in this feed carry raw HTML
    -- e.g. a bond's rate/maturity in a <div>, or a ticker wrapped in a
    Yahoo Finance <a> link. Real observed behavior, not a hypothetical."""
    if not value:
        return ""
    return _HTML_TAG.sub("", html.unescape(value)).strip()


def _parse_mdY(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_amount_range(value: str | None) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    match = _AMOUNT_RANGE.search(value)
    if not match:
        return None, None
    return float(match.group(1).replace(",", "")), float(match.group(2).replace(",", ""))


def _normalize_transaction_type(raw_type: str | None) -> str:
    if not raw_type:
        return "unknown"
    key = raw_type.strip().lower()
    return _TYPE_MAP.get(key, key.replace(" ", "_"))


def _normalize_ticker(raw_ticker: str | None) -> str | None:
    ticker = _strip_html(raw_ticker)
    if not ticker or ticker == "--":
        return None
    return ticker.upper()


def _parse_transaction(
    raw: dict, *, senator: str, ptr_link: str, disclosure_date: date | None
) -> SenateTrade | None:
    transaction_date = _parse_mdY(raw.get("transaction_date"))
    if transaction_date is None:
        return None
    amount_low, amount_high = _parse_amount_range(raw.get("amount"))
    return SenateTrade(
        senator=senator,
        ticker=_normalize_ticker(raw.get("ticker")),
        asset_description=_strip_html(raw.get("asset_description")),
        transaction_type=_normalize_transaction_type(raw.get("type")),
        transaction_date=transaction_date,
        disclosure_date=disclosure_date,
        amount_low=amount_low,
        amount_high=amount_high,
        ptr_link=ptr_link,
    )


class SenateStockWatcherClient:
    def __init__(self, base_url: str = SENATE_STOCK_WATCHER_BASE, timeout: float = 30.0) -> None:
        self._client = httpx.Client(timeout=timeout)
        self._base_url = base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SenateStockWatcherClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def fetch_all_transactions(self) -> list[SenateTrade]:
        """The full merged transaction history: real, complete,
        transaction-level data -- but this feed has no per-transaction
        disclosure date (disclosure_date is always None here). Use
        fetch_daily_filing when the exact filing lag matters."""
        response = self._client.get(f"{self._base_url}/aggregate/all_transactions.json")
        response.raise_for_status()
        trades = []
        for raw in response.json():
            trade = _parse_transaction(
                raw,
                senator=raw.get("senator", "Unknown"),
                ptr_link=raw.get("ptr_link", ""),
                disclosure_date=None,
            )
            if trade:
                trades.append(trade)
        return trades

    def fetch_daily_filing(self, report_date: date) -> list[SenateTrade]:
        """One day's filing index. Has the real disclosure date
        (`date_recieved` in the source feed) per filer -- the piece
        fetch_all_transactions is missing. Not every date has a file
        (senators don't file every day); a missing file raises
        httpx.HTTPStatusError with a 404 response, which callers should
        treat as 'nothing filed that day', not an error."""
        filename = report_date.strftime("transaction_report_for_%m_%d_%Y.json")
        response = self._client.get(f"{self._base_url}/data/{filename}")
        response.raise_for_status()

        trades = []
        for filer in response.json():
            senator = " ".join(part for part in [filer.get("first_name"), filer.get("last_name")] if part)
            disclosure_date = _parse_mdY(filer.get("date_recieved"))
            ptr_link = filer.get("ptr_link", "")
            for raw_transaction in filer.get("transactions", []):
                trade = _parse_transaction(
                    raw_transaction,
                    senator=senator or "Unknown",
                    ptr_link=ptr_link,
                    disclosure_date=disclosure_date,
                )
                if trade:
                    trades.append(trade)
        return trades
