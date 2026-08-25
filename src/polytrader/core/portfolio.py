"""Builds a `RiskEngine.PortfolioState` snapshot from the database.

Shared by every trading surface (Polymarket, congress mirror) so "what's my
current exposure" is computed exactly one way, from the same Position
rows, keyed by `asset_class`. Category is only meaningful for Polymarket
today (joined from Market.category); equity positions have no category
grouping yet, so `category_exposure` stays empty for the equity side --
that's a real limitation, not an oversight (concentration limits by
sector for equities is a natural Phase 2 addition, see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.core.models import AssetClass, Market, Position
from polytrader.core.risk import PortfolioState


def _open_positions(session: Session, asset_class: AssetClass) -> list[Position]:
    stmt = select(Position).where(Position.asset_class == asset_class, Position.closed_at.is_(None))
    return list(session.scalars(stmt))


def cost_basis(position: Position) -> float:
    return position.avg_entry * position.size


def market_value(position: Position) -> float:
    mark = position.current_mark if position.current_mark is not None else position.avg_entry
    return mark * position.size


def realized_pnl_total(session: Session, asset_class: AssetClass) -> float:
    stmt = select(Position).where(Position.asset_class == asset_class)
    return sum((p.realized_pnl for p in session.scalars(stmt)), 0.0)


def unrealized_pnl_total(session: Session, asset_class: AssetClass) -> float:
    return sum((p.unrealized_pnl for p in _open_positions(session, asset_class)), 0.0)


def build_portfolio_state(
    session: Session,
    *,
    asset_class: AssetClass,
    starting_bankroll: float,
    peak_bankroll: float | None = None,
    daily_pnl: float = 0.0,
    thesis_cooldowns: dict[str, datetime] | None = None,
) -> PortfolioState:
    open_positions = _open_positions(session, asset_class)

    equity = starting_bankroll + realized_pnl_total(session, asset_class) + unrealized_pnl_total(session, asset_class)

    total_exposure = sum((cost_basis(p) for p in open_positions), 0.0)

    market_exposure: dict[str, float] = {}
    category_exposure: dict[str, float] = {}
    for position in open_positions:
        key = position.market_id or position.symbol or "unknown"
        market_exposure[key] = market_exposure.get(key, 0.0) + cost_basis(position)

        if position.market_id:
            market = session.get(Market, position.market_id)
            category = market.category if market else None
            if category:
                category_exposure[category] = category_exposure.get(category, 0.0) + cost_basis(position)

    return PortfolioState(
        bankroll=equity,
        total_exposure=total_exposure,
        market_exposure=market_exposure,
        category_exposure=category_exposure,
        daily_pnl=daily_pnl,
        peak_bankroll=peak_bankroll if peak_bankroll is not None else max(equity, starting_bankroll),
        thesis_cooldowns=thesis_cooldowns or {},
    )
