"""Edge calculator (PRD 1 FR-EDGE-01..04).

Pure functions, no I/O and no database access, so this is trivially unit
testable in isolation. Every call returns both the pre-friction ("raw")
and post-friction ("adjusted") edge -- FR-EDGE-04 -- so a rejected trade's
paper trail shows exactly how much of the apparent edge was consumed by
trading costs versus how much never existed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeResult:
    raw_edge: float
    adjusted_edge: float
    expected_value: float
    executable_price: float
    friction: float
    passes_threshold: bool


def compute_edge(
    *,
    fair_probability: float,
    market_price: float,
    side: str = "yes",
    spread: float = 0.0,
    fee_bps: float = 200.0,
    slippage_bps: float = 100.0,
    uncertainty_buffer: float = 0.02,
    min_adjusted_edge: float = 0.03,
) -> EdgeResult:
    """FR-EDGE-01: raw edge = fair probability - market probability for the
    requested side. FR-EDGE-02: adjust for spread, fees, slippage, and an
    explicit uncertainty buffer. FR-EDGE-03: `passes_threshold` tells the
    caller whether to proceed; it does not raise -- rejection is the risk
    engine's job, this just computes the number.
    """
    if not 0.0 <= fair_probability <= 1.0:
        raise ValueError(f"fair_probability must be in [0, 1], got {fair_probability}")
    if not 0.0 <= market_price <= 1.0:
        raise ValueError(f"market_price must be in [0, 1], got {market_price}")
    if side not in ("yes", "no"):
        raise ValueError(f"side must be 'yes' or 'no', got {side!r}")
    if spread < 0:
        raise ValueError(f"spread must be >= 0, got {spread}")

    if side == "yes":
        p_win = fair_probability
        raw_edge = fair_probability - market_price
        executable_price = min(1.0, market_price + spread / 2)
    else:
        p_win = 1.0 - fair_probability
        market_no_price = 1.0 - market_price
        raw_edge = p_win - market_no_price
        executable_price = min(1.0, market_no_price + spread / 2)

    friction = (fee_bps + slippage_bps) / 10_000.0 + uncertainty_buffer
    adjusted_edge = raw_edge - friction

    if executable_price <= 0.0 or executable_price >= 1.0:
        expected_value = 0.0
    else:
        payout_if_win = (1.0 - executable_price) / executable_price
        expected_value = p_win * payout_if_win - (1.0 - p_win) - friction

    return EdgeResult(
        raw_edge=raw_edge,
        adjusted_edge=adjusted_edge,
        expected_value=expected_value,
        executable_price=executable_price,
        friction=friction,
        passes_threshold=adjusted_edge >= min_adjusted_edge,
    )
