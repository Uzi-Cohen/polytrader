"""Market scanner (FR-SCAN-01..05): filter, persist, rank, and dedup."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from polytrader.core.models import Market, MarketSnapshot
from polytrader.core.timeutil import ensure_aware
from polytrader.polymarket.gamma_client import GammaMarket


@dataclass(frozen=True)
class ScanFilters:
    """FR-SCAN-03."""

    min_liquidity: float = 5_000.0
    min_volume: float = 1_000.0
    max_spread: float = 0.10
    min_hours_to_resolution: float = 6.0
    category: str | None = None


@dataclass(frozen=True)
class ScannedMarket:
    market: GammaMarket
    yes_price: float
    no_price: float
    opportunity_score: float
    is_duplicate: bool  # True if unchanged since the last scan within the dedup window


def _yes_no_prices(market: GammaMarket) -> tuple[float, float]:
    if len(market.outcome_prices) >= 2:
        return market.outcome_prices[0], market.outcome_prices[1]
    if market.best_bid is not None and market.best_ask is not None:
        mid = (market.best_bid + market.best_ask) / 2
        return mid, 1 - mid
    return 0.5, 0.5


def passes_filters(market: GammaMarket, filters: ScanFilters, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not market.active or market.closed:
        return False
    if market.liquidity < filters.min_liquidity:
        return False
    if market.volume < filters.min_volume:
        return False
    if market.spread is not None and market.spread > filters.max_spread:
        return False
    if filters.category and (market.category or "").lower() != filters.category.lower():
        return False
    if market.end_date is not None:
        hours_left = (market.end_date - now).total_seconds() / 3600
        if hours_left < filters.min_hours_to_resolution:
            return False
    return True


def opportunity_score(market: GammaMarket) -> float:
    """FR-SCAN-04. True 'estimated edge potential' isn't known until
    research runs, so this pre-research proxy (liquidity, discounted by
    spread) only orders the research queue -- it never sizes or approves a
    trade. Re-rank with real edge once a Forecast/EdgeResult exists."""
    spread_penalty = 1.0 / (1.0 + (market.spread or 0.0) * 10)
    return market.liquidity * spread_penalty


def scan(
    session: Session,
    gamma_markets: list[GammaMarket],
    filters: ScanFilters,
    *,
    dedup_window: timedelta = timedelta(hours=1),
    now: datetime | None = None,
) -> list[ScannedMarket]:
    now = now or datetime.now(timezone.utc)
    eligible = [m for m in gamma_markets if passes_filters(m, filters, now)]

    results: list[ScannedMarket] = []
    for gm in eligible:
        db_market = session.get(Market, gm.market_id)
        if db_market is None:
            db_market = Market(
                market_id=gm.market_id,
                event_id=gm.event_id,
                question=gm.question,
                category=gm.category,
                outcomes=",".join(gm.outcomes) if gm.outcomes else "YES,NO",
                resolution_time=gm.end_date,
                status="active",
            )
            session.add(db_market)
        else:
            db_market.question = gm.question
            db_market.category = gm.category
            db_market.resolution_time = gm.end_date

        yes_price, no_price = _yes_no_prices(gm)

        last_snapshot = (
            session.query(MarketSnapshot)
            .filter(MarketSnapshot.market_id == gm.market_id)
            .order_by(MarketSnapshot.timestamp.desc())
            .first()
        )
        is_duplicate = (
            last_snapshot is not None
            and (now - ensure_aware(last_snapshot.timestamp)) < dedup_window
            and abs(last_snapshot.yes_price - yes_price) < 1e-6
        )
        if not is_duplicate:
            session.add(
                MarketSnapshot(
                    market_id=gm.market_id,
                    timestamp=now,
                    yes_price=yes_price,
                    no_price=no_price,
                    bid=gm.best_bid,
                    ask=gm.best_ask,
                    spread=gm.spread,
                    volume=gm.volume,
                    liquidity=gm.liquidity,
                )
            )

        results.append(
            ScannedMarket(
                market=gm,
                yes_price=yes_price,
                no_price=no_price,
                opportunity_score=opportunity_score(gm),
                is_duplicate=is_duplicate,
            )
        )

    session.flush()
    results.sort(key=lambda r: r.opportunity_score, reverse=True)
    return results
