import pytest

from polytrader.core.config import Settings
from polytrader.polymarket.research import (
    ClaudeEstimator,
    NoOpEstimator,
    build_research_packet,
    get_default_estimator,
)


def test_noop_estimator_never_fabricates_a_probability():
    packet = build_research_packet("m1", "Will X happen?")
    result = NoOpEstimator().forecast(packet)
    assert result is None


def test_get_default_estimator_returns_noop_without_api_key():
    settings = Settings(anthropic_api_key=None)
    estimator = get_default_estimator(settings)
    assert isinstance(estimator, NoOpEstimator)


def test_claude_estimator_refuses_construction_without_api_key():
    settings = Settings(anthropic_api_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ClaudeEstimator(settings)


def test_build_research_packet_defaults_to_empty_lists():
    packet = build_research_packet("m1", "Will X happen?")
    assert packet.facts == []
    assert packet.unknowns == []
    assert packet.contradictions == []
    assert packet.assumptions == []
