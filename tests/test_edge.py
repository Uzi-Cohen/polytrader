import pytest

from polytrader.core.edge import compute_edge


def test_raw_edge_yes_side_matches_probability_gap():
    result = compute_edge(
        fair_probability=0.60,
        market_price=0.45,
        side="yes",
        spread=0.0,
        fee_bps=0.0,
        slippage_bps=0.0,
        uncertainty_buffer=0.0,
    )
    assert result.raw_edge == pytest.approx(0.15)
    assert result.adjusted_edge == pytest.approx(0.15)


def test_friction_reduces_adjusted_edge_but_not_raw_edge():
    result = compute_edge(
        fair_probability=0.60,
        market_price=0.45,
        fee_bps=200.0,
        slippage_bps=100.0,
        uncertainty_buffer=0.02,
    )
    # friction = (200+100)/10000 + 0.02 = 0.05
    assert result.friction == pytest.approx(0.05)
    assert result.raw_edge == pytest.approx(0.15)
    assert result.adjusted_edge == pytest.approx(0.10)


def test_no_side_inverts_probability_and_price():
    result = compute_edge(
        fair_probability=0.30,
        market_price=0.45,
        side="no",
        fee_bps=0.0,
        slippage_bps=0.0,
        uncertainty_buffer=0.0,
    )
    # fair NO probability = 0.70, market NO price = 0.55 -> raw edge 0.15
    assert result.raw_edge == pytest.approx(0.15)


def test_passes_threshold_reflects_min_adjusted_edge():
    weak = compute_edge(fair_probability=0.50, market_price=0.48, min_adjusted_edge=0.03)
    assert weak.passes_threshold is False

    strong = compute_edge(
        fair_probability=0.80,
        market_price=0.45,
        fee_bps=0.0,
        slippage_bps=0.0,
        uncertainty_buffer=0.0,
        min_adjusted_edge=0.03,
    )
    assert strong.passes_threshold is True


def test_expected_value_positive_for_genuine_edge():
    result = compute_edge(
        fair_probability=0.70,
        market_price=0.50,
        fee_bps=0.0,
        slippage_bps=0.0,
        uncertainty_buffer=0.0,
    )
    assert result.expected_value > 0


def test_expected_value_negative_when_market_price_exceeds_fair_probability():
    result = compute_edge(
        fair_probability=0.30,
        market_price=0.50,
        fee_bps=0.0,
        slippage_bps=0.0,
        uncertainty_buffer=0.0,
    )
    assert result.expected_value < 0


@pytest.mark.parametrize("bad_prob", [-0.01, 1.01])
def test_rejects_out_of_range_probability(bad_prob):
    with pytest.raises(ValueError):
        compute_edge(fair_probability=bad_prob, market_price=0.5)


@pytest.mark.parametrize("bad_price", [-0.01, 1.01])
def test_rejects_out_of_range_price(bad_price):
    with pytest.raises(ValueError):
        compute_edge(fair_probability=0.5, market_price=bad_price)


def test_rejects_invalid_side():
    with pytest.raises(ValueError):
        compute_edge(fair_probability=0.5, market_price=0.5, side="maybe")
