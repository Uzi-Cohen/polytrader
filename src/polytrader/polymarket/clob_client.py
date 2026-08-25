"""Documented stub for Polymarket's CLOB (live order placement).

This build is paper-only end to end (see docs/ARCHITECTURE.md's
Live-Trading Gate). Nothing calls this module. It exists so the seam for
live execution is visible and typed, rather than requiring a redesign
later: when the gate criteria in the original PRD are actually met
(minimum paper-trading duration, positive friction-adjusted expectancy,
tested kill switch, explicit operator enablement, `LIVE_TRADING_ENABLED=true`),
implement `ClobClient.submit_order` here using `py-clob-client`, wired
behind that flag -- and nowhere else.
"""
from __future__ import annotations

from dataclasses import dataclass

CLOB_BASE_URL = "https://clob.polymarket.com"


@dataclass(frozen=True)
class LiveOrderRequest:
    market_id: str
    side: str
    price: float
    size: float
    tif: str = "GTC"


class ClobClient:
    def __init__(self, *, live_trading_enabled: bool) -> None:
        self.live_trading_enabled = live_trading_enabled

    def submit_order(self, order: LiveOrderRequest) -> None:
        raise NotImplementedError(
            "Live Polymarket order submission is not implemented in this build. "
            "It stays out of scope until the Live-Trading Gate criteria in "
            "docs/ARCHITECTURE.md are met and LIVE_TRADING_ENABLED=true."
        )
