import random
from datetime import datetime, timedelta, timezone

import pytest

from polytrader.core.models import AssetClass, RiskDecisionOutcome
from polytrader.core.risk import (
    PortfolioState,
    RiskEngine,
    RiskLimits,
    TradeProposal,
    kelly_fraction,
)

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def make_limits(**overrides) -> RiskLimits:
    defaults = dict(
        max_risk_per_trade_pct=0.02,
        kelly_fraction=0.35,
        max_total_exposure_pct=0.50,
        max_market_concentration_pct=0.08,
        max_category_concentration_pct=0.25,
        daily_loss_pause_pct=0.05,
        max_drawdown_halt_pct=0.18,
        min_adjusted_edge=0.03,
        probability_extreme_guard=0.90,
        max_data_staleness_seconds=900,
        thesis_cooldown_hours=24,
        kill_switch=False,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def make_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        asset_class=AssetClass.POLYMARKET,
        market_or_symbol="market-1",
        executable_price=0.45,
        adjusted_edge=0.10,
        category="politics",
        fair_probability=0.60,
        now=NOW,
        data_timestamp=NOW,
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def make_portfolio(**overrides) -> PortfolioState:
    defaults = dict(bankroll=10_000.0)
    defaults.update(overrides)
    return PortfolioState(**defaults)


# --- Kelly sizing ---


def test_kelly_fraction_zero_when_no_edge():
    # Fair coin priced fairly at 0.5 -> no edge -> zero stake.
    assert kelly_fraction(p_win=0.5, executable_price=0.5) == pytest.approx(0.0)


def test_kelly_fraction_positive_with_genuine_edge():
    f = kelly_fraction(p_win=0.6, executable_price=0.45)
    assert f > 0.0


def test_kelly_fraction_never_negative():
    f = kelly_fraction(p_win=0.2, executable_price=0.45)
    assert f == 0.0


def test_kelly_fraction_rejects_bad_price():
    assert kelly_fraction(p_win=0.5, executable_price=0.0) == 0.0
    assert kelly_fraction(p_win=0.5, executable_price=1.0) == 0.0


# --- Hard blocks ---


def test_kill_switch_always_rejects():
    engine = RiskEngine(make_limits(kill_switch=True))
    decision = engine.evaluate(make_proposal(), make_portfolio())
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert decision.approved_size == 0.0
    assert "kill_switch" in decision.limits_hit


def test_stale_data_rejected():
    engine = RiskEngine(make_limits(max_data_staleness_seconds=60))
    stale_proposal = make_proposal(data_timestamp=NOW - timedelta(minutes=10))
    decision = engine.evaluate(stale_proposal, make_portfolio())
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "stale_data" in decision.limits_hit


def test_edge_below_threshold_rejected():
    engine = RiskEngine(make_limits(min_adjusted_edge=0.05))
    decision = engine.evaluate(make_proposal(adjusted_edge=0.01), make_portfolio())
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "edge_below_threshold" in decision.limits_hit


def test_extreme_probability_requires_manual_override():
    engine = RiskEngine(make_limits())
    blocked = engine.evaluate(make_proposal(fair_probability=0.95), make_portfolio())
    assert blocked.outcome == RiskDecisionOutcome.REJECTED
    assert "extreme_probability_requires_review" in blocked.limits_hit

    overridden = engine.evaluate(
        make_proposal(fair_probability=0.95, manual_override=True), make_portfolio()
    )
    assert overridden.outcome != RiskDecisionOutcome.REJECTED or "extreme_probability_requires_review" not in overridden.limits_hit


def test_daily_loss_pause_blocks_new_entries():
    engine = RiskEngine(make_limits(daily_loss_pause_pct=0.05))
    portfolio = make_portfolio(bankroll=9_000.0, daily_pnl=-600.0)  # -6.7% today
    decision = engine.evaluate(make_proposal(), portfolio)
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "daily_loss_pause" in decision.limits_hit


def test_max_drawdown_halts_trading_regardless_of_edge():
    engine = RiskEngine(make_limits(max_drawdown_halt_pct=0.18))
    portfolio = make_portfolio(bankroll=8_000.0, peak_bankroll=10_000.0)  # -20% drawdown
    decision = engine.evaluate(make_proposal(adjusted_edge=0.50, fair_probability=0.9, manual_override=True), portfolio)
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "max_drawdown_halt" in decision.limits_hit


def test_thesis_cooldown_blocks_reentry():
    engine = RiskEngine(make_limits())
    portfolio = make_portfolio(thesis_cooldowns={"market-1": NOW + timedelta(hours=5)})
    decision = engine.evaluate(make_proposal(), portfolio)
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "thesis_cooldown" in decision.limits_hit


def test_missing_sizing_input_rejected():
    engine = RiskEngine(make_limits())
    decision = engine.evaluate(make_proposal(fair_probability=None), make_portfolio())
    assert decision.outcome == RiskDecisionOutcome.REJECTED
    assert "no_sizing_input" in decision.limits_hit


# --- Sizing caps ---


def test_approved_size_never_exceeds_max_risk_per_trade():
    engine = RiskEngine(make_limits(max_risk_per_trade_pct=0.02))
    portfolio = make_portfolio(bankroll=10_000.0)
    # Huge explicit proposal, well above the 2% ($200) cap.
    decision = engine.evaluate(make_proposal(proposed_notional=5_000.0), portfolio)
    assert decision.approved_size <= 200.0 + 1e-6
    assert decision.outcome == RiskDecisionOutcome.REDUCED
    assert "max_risk_per_trade" in decision.limits_hit


def test_approved_size_never_exceeds_total_exposure_capacity():
    engine = RiskEngine(make_limits(max_risk_per_trade_pct=1.0, max_total_exposure_pct=0.5))
    portfolio = make_portfolio(bankroll=10_000.0, total_exposure=4_900.0)  # $100 of capacity left
    decision = engine.evaluate(make_proposal(proposed_notional=1_000.0), portfolio)
    assert decision.approved_size <= 100.0 + 1e-6
    assert "max_total_exposure" in decision.limits_hit


def test_approved_size_never_exceeds_market_concentration_cap():
    engine = RiskEngine(make_limits(max_risk_per_trade_pct=1.0, max_total_exposure_pct=1.0, max_market_concentration_pct=0.08))
    portfolio = make_portfolio(bankroll=10_000.0, market_exposure={"market-1": 700.0})  # $100 left of $800 cap
    decision = engine.evaluate(make_proposal(proposed_notional=1_000.0), portfolio)
    assert decision.approved_size <= 100.0 + 1e-6
    assert "max_market_concentration" in decision.limits_hit


def test_approved_size_never_exceeds_category_concentration_cap():
    engine = RiskEngine(
        make_limits(
            max_risk_per_trade_pct=1.0,
            max_total_exposure_pct=1.0,
            max_market_concentration_pct=1.0,
            max_category_concentration_pct=0.25,
        )
    )
    portfolio = make_portfolio(bankroll=10_000.0, category_exposure={"politics": 2_400.0})  # $100 left of $2500
    decision = engine.evaluate(make_proposal(proposed_notional=1_000.0, category="politics"), portfolio)
    assert decision.approved_size <= 100.0 + 1e-6
    assert "max_category_concentration" in decision.limits_hit


def test_full_approval_when_well_within_all_limits():
    engine = RiskEngine(make_limits())
    portfolio = make_portfolio(bankroll=10_000.0)
    decision = engine.evaluate(make_proposal(proposed_notional=50.0), portfolio)
    assert decision.outcome == RiskDecisionOutcome.APPROVED
    assert decision.approved_size == pytest.approx(50.0)
    assert decision.limits_hit == []


# --- Property-style test: across many randomized proposals/portfolios,
# approved size must never exceed ANY configured cap, and the kill switch
# must always drive approved size to zero. This substitutes for a
# hypothesis-based property test without adding that dependency. ---


def test_property_approved_size_never_exceeds_any_cap():
    rng = random.Random(1234)
    limits = make_limits()
    engine = RiskEngine(limits)

    for _ in range(500):
        bankroll = rng.uniform(1_000, 1_000_000)
        proposed = rng.uniform(0, bankroll * 2)
        total_exposure = rng.uniform(0, bankroll)
        market_exposure = rng.uniform(0, bankroll)
        category_exposure = rng.uniform(0, bankroll)

        portfolio = PortfolioState(
            bankroll=bankroll,
            total_exposure=total_exposure,
            market_exposure={"m": market_exposure},
            category_exposure={"c": category_exposure},
        )
        proposal = TradeProposal(
            asset_class=AssetClass.POLYMARKET,
            market_or_symbol="m",
            category="c",
            executable_price=0.4,
            adjusted_edge=0.10,
            proposed_notional=proposed,
            now=NOW,
        )
        decision = engine.evaluate(proposal, portfolio)

        assert decision.approved_size <= limits.max_risk_per_trade_pct * bankroll + 1e-6
        remaining_total = max(0.0, limits.max_total_exposure_pct * bankroll - total_exposure)
        assert decision.approved_size <= remaining_total + 1e-6
        remaining_market = max(0.0, limits.max_market_concentration_pct * bankroll - market_exposure)
        assert decision.approved_size <= remaining_market + 1e-6
        remaining_category = max(0.0, limits.max_category_concentration_pct * bankroll - category_exposure)
        assert decision.approved_size <= remaining_category + 1e-6
        assert decision.approved_size >= 0.0


def test_property_kill_switch_zeroes_every_proposal():
    rng = random.Random(99)
    engine = RiskEngine(make_limits(kill_switch=True))

    for _ in range(100):
        portfolio = PortfolioState(bankroll=rng.uniform(1_000, 100_000))
        proposal = TradeProposal(
            asset_class=AssetClass.EQUITY,
            market_or_symbol="AAPL",
            executable_price=0.5,
            adjusted_edge=rng.uniform(0, 1),
            proposed_notional=rng.uniform(1, 10_000),
            now=NOW,
        )
        decision = engine.evaluate(proposal, portfolio)
        assert decision.approved_size == 0.0
        assert decision.outcome == RiskDecisionOutcome.REJECTED
