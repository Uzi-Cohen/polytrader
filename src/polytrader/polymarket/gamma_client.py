"""Client for Polymarket's Gamma API (FR-SCAN-01/02).

Gamma is the public market/event discovery API -- no wallet, no API key.
This module only ever reads market data; it never touches the CLOB
(clob.polymarket.com), which is wallet-authenticated and used for live
order placement. See clob_client.py for why that stays a stub.

Not live-tested from inside this build's sandbox: the sandbox's egress
policy blocks gamma-api.polymarket.com (confirmed via a 403 at the proxy's
CONNECT layer, i.e. an org policy denial, not a code or endpoint issue).
The field mapping below reflects Gamma's documented/long-stable response
shape; verify against a live response the first time this runs somewhere
that can actually reach the host, and adjust `_parse_market` if fields
have shifted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import httpx

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


@dataclass(frozen=True)
class GammaMarket:
    market_id: str
    event_id: str | None
    question: str
    category: str | None
    outcomes: list[str]
    outcome_prices: list[float]
    volume: float
    liquidity: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    active: bool
    closed: bool
    end_date: datetime | None
    raw: dict


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_market(raw: dict) -> GammaMarket:
    outcomes = [str(o) for o in _parse_json_list(raw.get("outcomes"))]
    outcome_prices = [_to_float(p) for p in _parse_json_list(raw.get("outcomePrices"))]
    volume = _to_float(raw.get("volumeNum"), _to_float(raw.get("volume")))
    liquidity = _to_float(raw.get("liquidityNum"), _to_float(raw.get("liquidity")))
    best_bid = raw.get("bestBid")
    best_ask = raw.get("bestAsk")
    events = raw.get("events") or []
    event_id = str(events[0]["id"]) if events and "id" in events[0] else raw.get("eventId")

    return GammaMarket(
        market_id=str(raw.get("id") or raw.get("conditionId")),
        event_id=str(event_id) if event_id is not None else None,
        question=raw.get("question") or raw.get("title") or "",
        category=raw.get("category"),
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        volume=volume,
        liquidity=liquidity,
        best_bid=_to_float(best_bid) if best_bid is not None else None,
        best_ask=_to_float(best_ask) if best_ask is not None else None,
        spread=_to_float(raw.get("spread")) if raw.get("spread") is not None else None,
        active=bool(raw.get("active", False)),
        closed=bool(raw.get("closed", False)),
        end_date=_parse_datetime(raw.get("endDate")),
        raw=raw,
    )


class GammaClient:
    def __init__(self, base_url: str = GAMMA_BASE_URL, timeout: float = 20.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GammaClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def list_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        category: str | None = None,
    ) -> list[GammaMarket]:
        """FR-SCAN-01: retrieve active markets on a configurable cadence."""
        params: dict = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        if category:
            params["category"] = category
        response = self._client.get("/markets", params=params)
        response.raise_for_status()
        payload = response.json()
        raw_markets = payload if isinstance(payload, list) else payload.get("data", [])
        return [_parse_market(m) for m in raw_markets]
