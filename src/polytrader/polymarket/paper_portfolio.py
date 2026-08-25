"""Paper trading simulator for Polymarket positions (FR-EXEC-01).

Positions are share-denominated: `size` is a share count and `avg_entry` is
the average $/share paid for the YES or NO outcome token (Polymarket
outcome tokens trade in [0, 1]). This module only executes an
already-approved order against the paper wallet -- it has no opinion on
whether a trade should happen; that's the risk engine's job
(core/risk.py), invoked before this is ever called.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.core.models import AssetClass, Position


def get_open_position(session: Session, market_id: str, side: str) -> Position | None:
    stmt = select(Position).where(
        Position.asset_class == AssetClass.POLYMARKET,
        Position.market_id == market_id,
        Position.side == side,
        Position.closed_at.is_(None),
    )
    return session.scalars(stmt).first()


def paper_buy(session: Session, *, market_id: str, side: str, price: float, notional: float) -> Position:
    """Buy `notional` dollars worth of `side` shares at `price`."""
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be in (0, 1) for a Polymarket share, got {price}")
    if notional <= 0:
        raise ValueError(f"notional must be positive, got {notional}")

    shares = notional / price
    position = get_open_position(session, market_id, side)
    if position is None:
        position = Position(
            asset_class=AssetClass.POLYMARKET,
            market_id=market_id,
            side=side,
            avg_entry=price,
            size=shares,
            current_mark=price,
            opened_at=datetime.now(timezone.utc),
        )
        session.add(position)
    else:
        total_cost = position.avg_entry * position.size + price * shares
        position.size += shares
        position.avg_entry = total_cost / position.size
        position.current_mark = price
    session.flush()
    return position


def paper_sell(session: Session, *, market_id: str, side: str, price: float, shares: float) -> Position:
    """Sell `shares` of an existing `side` position at `price`, realizing
    P&L on the portion sold."""
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be in (0, 1) for a Polymarket share, got {price}")

    position = get_open_position(session, market_id, side)
    if position is None:
        raise ValueError(f"No open {side} position in market {market_id} to sell")
    if shares > position.size + 1e-9:
        raise ValueError(f"Cannot sell {shares} shares; only {position.size} held")

    realized = (price - position.avg_entry) * shares
    position.realized_pnl += realized
    position.size -= shares
    position.current_mark = price

    if position.size <= 1e-9:
        position.size = 0.0
        position.closed_at = datetime.now(timezone.utc)
        position.unrealized_pnl = 0.0

    session.flush()
    return position


def mark_to_market(session: Session, *, market_id: str, yes_price: float, no_price: float) -> list[Position]:
    """Update current_mark/unrealized_pnl for every open position in a
    market from the latest snapshot prices (FR-MON-01)."""
    prices = {"yes": yes_price, "no": no_price}
    stmt = select(Position).where(
        Position.asset_class == AssetClass.POLYMARKET,
        Position.market_id == market_id,
        Position.closed_at.is_(None),
    )
    updated = []
    for position in session.scalars(stmt):
        mark = prices.get(position.side)
        if mark is None:
            continue
        position.current_mark = mark
        position.unrealized_pnl = (mark - position.avg_entry) * position.size
        updated.append(position)
    session.flush()
    return updated
