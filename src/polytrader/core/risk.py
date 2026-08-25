"""Deterministic risk engine (PRD 1 section 7, FR-RISK-01..08).

Design rule this file exists to enforce: the AI can recommend a trade; the
risk engine can veto it. No forecast, edge estimate, or mirror-strategy
proposal -- from Polymarket or the congressional mirror -- can bypass this.
`RiskEngine.evaluate` is a pure function of (proposal, portfolio state,
limits): no I/O, no database access, so every invariant below is directly
unit-testable without a running system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from polytrader.core.config import Settings
from polytrader.core.models import AssetClass, RiskDecisionOutcome

# Limit violations that reject the trade outright (approved_size = 0)
# rather than merely shrinking it.
_HARD_BLOCKS = frozenset(
    {
        "kill_switch",
        "stale_data",
        "edge_below_threshold",
        "extreme_probability_requires_review",
        "daily_loss_pause",
        "max_drawdown_halt",
        "thesis_cooldown",
        "no_sizing_input",
    }
)


def kelly_fraction(p_win: float, executable_price: float) -> float:
    """Full-Kelly fraction of bankroll for a binary bet at `executable_price`
    in (0, 1) with true win probability p_win. Returns 0 when there is no
    edge (never a negative stake)."""
    if not 0.0 < executable_price < 1.0:
        return 0.0
    if not 0.0 <= p_win <= 1.0:
        raise ValueError(f"p_win must be in [0, 1], got {p_win}")
    b = (1.0 - executable_price) / executable_price
    q = 1.0 - p_win
    f_star = (p_win * b - q) / b
    return max(0.0, f_star)


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: float
    kelly_fraction: float
    max_total_exposure_pct: float
    max_market_concentration_pct: float
    max_category_concentration_pct: float
    daily_loss_pause_pct: float
    max_drawdown_halt_pct: float
    min_adjusted_edge: float
    probability_extreme_guard: float
    max_data_staleness_seconds: int
    thesis_cooldown_hours: int
    kill_switch: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "RiskLimits":
        return cls(
            max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
            kelly_fraction=settings.kelly_fraction,
            max_total_exposure_pct=settings.max_total_exposure_pct,
            max_market_concentration_pct=settings.max_market_concentration_pct,
            max_category_concentration_pct=settings.max_category_concentration_pct,
            daily_loss_pause_pct=settings.daily_loss_pause_pct,
            max_drawdown_halt_pct=settings.max_drawdown_halt_pct,
            min_adjusted_edge=settings.min_adjusted_edge,
            probability_extreme_guard=settings.probability_extreme_guard,
            max_data_staleness_seconds=settings.max_data_staleness_seconds,
            thesis_cooldown_hours=settings.thesis_cooldown_hours,
            kill_switch=settings.kill_switch,
        )


@dataclass(frozen=True)
class PortfolioState:
    """Everything the risk engine needs to know about current exposure.
    Built fresh from the Position table before every evaluation -- never
    mutated by the engine itself."""

    bankroll: float
    total_exposure: float = 0.0
    market_exposure: dict[str, float] = field(default_factory=dict)
    category_exposure: dict[str, float] = field(default_factory=dict)
    daily_pnl: float = 0.0
    peak_bankroll: float | None = None
    thesis_cooldowns: dict[str, datetime] = field(default_factory=dict)

    def drawdown_pct(self) -> float:
        peak = self.peak_bankroll if self.peak_bankroll is not None else self.bankroll
        if peak <= 0:
            return 0.0
        return max(0.0, 1.0 - self.bankroll / peak)

    def daily_loss_pct(self) -> float:
        if self.bankroll <= 0 or self.daily_pnl >= 0:
            return 0.0
        return -self.daily_pnl / self.bankroll


@dataclass(frozen=True)
class TradeProposal:
    asset_class: AssetClass
    market_or_symbol: str
    executable_price: float
    adjusted_edge: float
    category: str | None = None
    fair_probability: float | None = None
    proposed_notional: float | None = None
    confidence: float = 0.5
    manual_override: bool = False
    data_timestamp: datetime | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class RiskEvaluation:
    outcome: RiskDecisionOutcome
    proposed_size: float
    approved_size: float
    kelly_fraction_used: float | None
    limits_hit: list[str]
    reasons: str


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate(self, proposal: TradeProposal, portfolio: PortfolioState) -> RiskEvaluation:
        now = proposal.now or datetime.now(timezone.utc)
        limits_hit: list[str] = []
        reasons: list[str] = []

        # FR-RISK-07: kill switch is checked before every order, first,
        # unconditionally.
        if self.limits.kill_switch:
            return self._reject(proposal, ["kill_switch"], "Kill switch is engaged; no new orders.")

        # FR-RISK-08: reject on stale evidence.
        if proposal.data_timestamp is not None:
            age_seconds = (now - proposal.data_timestamp).total_seconds()
            if age_seconds > self.limits.max_data_staleness_seconds:
                limits_hit.append("stale_data")
                reasons.append(
                    f"Data is {age_seconds:.0f}s old, exceeds max "
                    f"{self.limits.max_data_staleness_seconds}s"
                )

        if proposal.adjusted_edge < self.limits.min_adjusted_edge:
            limits_hit.append("edge_below_threshold")
            reasons.append(
                f"Adjusted edge {proposal.adjusted_edge:.4f} below minimum "
                f"{self.limits.min_adjusted_edge:.4f}"
            )

        if proposal.fair_probability is not None and not proposal.manual_override:
            extreme_hi = self.limits.probability_extreme_guard
            extreme_lo = 1.0 - self.limits.probability_extreme_guard
            if proposal.fair_probability >= extreme_hi or proposal.fair_probability <= extreme_lo:
                limits_hit.append("extreme_probability_requires_review")
                reasons.append(
                    f"Fair probability {proposal.fair_probability:.3f} is beyond the "
                    f"{extreme_lo:.2f}/{extreme_hi:.2f} guard band and needs manual override"
                )

        if portfolio.daily_loss_pct() >= self.limits.daily_loss_pause_pct:
            limits_hit.append("daily_loss_pause")
            reasons.append(
                f"Daily loss {portfolio.daily_loss_pct():.2%} at/above pause threshold "
                f"{self.limits.daily_loss_pause_pct:.2%}"
            )

        if portfolio.drawdown_pct() >= self.limits.max_drawdown_halt_pct:
            limits_hit.append("max_drawdown_halt")
            reasons.append(
                f"Drawdown {portfolio.drawdown_pct():.2%} at/above halt threshold "
                f"{self.limits.max_drawdown_halt_pct:.2%}"
            )

        cooldown_until = portfolio.thesis_cooldowns.get(proposal.market_or_symbol)
        if cooldown_until is not None and now < cooldown_until:
            limits_hit.append("thesis_cooldown")
            reasons.append(f"{proposal.market_or_symbol} is in cooldown until {cooldown_until.isoformat()}")

        if any(item in _HARD_BLOCKS for item in limits_hit):
            return self._reject(proposal, limits_hit, "; ".join(reasons))

        kelly_used: float | None = None
        if proposal.proposed_notional is not None:
            proposed_size = proposal.proposed_notional
        elif proposal.fair_probability is not None:
            f_star = kelly_fraction(proposal.fair_probability, proposal.executable_price)
            kelly_used = f_star * self.limits.kelly_fraction
            proposed_size = kelly_used * portfolio.bankroll
        else:
            limits_hit.append("no_sizing_input")
            return self._reject(
                proposal,
                limits_hit,
                "Neither proposed_notional nor fair_probability was provided to size the trade.",
            )

        approved_size = max(0.0, proposed_size)

        max_trade_size = self.limits.max_risk_per_trade_pct * portfolio.bankroll
        if approved_size > max_trade_size:
            limits_hit.append("max_risk_per_trade")
            approved_size = max_trade_size

        remaining_total_capacity = max(
            0.0, self.limits.max_total_exposure_pct * portfolio.bankroll - portfolio.total_exposure
        )
        if approved_size > remaining_total_capacity:
            limits_hit.append("max_total_exposure")
            approved_size = min(approved_size, remaining_total_capacity)

        current_market_exposure = portfolio.market_exposure.get(proposal.market_or_symbol, 0.0)
        max_market_size = self.limits.max_market_concentration_pct * portfolio.bankroll
        remaining_market_capacity = max(0.0, max_market_size - current_market_exposure)
        if approved_size > remaining_market_capacity:
            limits_hit.append("max_market_concentration")
            approved_size = min(approved_size, remaining_market_capacity)

        if proposal.category:
            current_category_exposure = portfolio.category_exposure.get(proposal.category, 0.0)
            max_category_size = self.limits.max_category_concentration_pct * portfolio.bankroll
            remaining_category_capacity = max(0.0, max_category_size - current_category_exposure)
            if approved_size > remaining_category_capacity:
                limits_hit.append("max_category_concentration")
                approved_size = min(approved_size, remaining_category_capacity)

        approved_size = max(0.0, approved_size)

        if approved_size <= 0.0:
            outcome = RiskDecisionOutcome.REJECTED
            if not reasons:
                reasons.append("Approved size reduced to zero by risk limits.")
        elif approved_size < proposed_size - 1e-9:
            outcome = RiskDecisionOutcome.REDUCED
            reasons.append(
                f"Size reduced from {proposed_size:.2f} to {approved_size:.2f} by: "
                f"{', '.join(limits_hit)}"
            )
        else:
            outcome = RiskDecisionOutcome.APPROVED
            reasons.append("Approved at full proposed size.")

        return RiskEvaluation(
            outcome=outcome,
            proposed_size=proposed_size,
            approved_size=approved_size,
            kelly_fraction_used=kelly_used,
            limits_hit=limits_hit,
            reasons="; ".join(reasons),
        )

    def _reject(self, proposal: TradeProposal, limits_hit: list[str], reason: str) -> RiskEvaluation:
        return RiskEvaluation(
            outcome=RiskDecisionOutcome.REJECTED,
            proposed_size=proposal.proposed_notional or 0.0,
            approved_size=0.0,
            kelly_fraction_used=None,
            limits_hit=limits_hit,
            reasons=reason or "Rejected by risk rules.",
        )
