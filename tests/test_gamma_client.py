import json

from polytrader.polymarket.gamma_client import _parse_market

RAW_MARKET = {
    "id": "253591",
    "question": "Will the US government shut down before 2027?",
    "conditionId": "0xabc123",
    "category": "Politics",
    "outcomes": json.dumps(["Yes", "No"]),
    "outcomePrices": json.dumps(["0.42", "0.58"]),
    "volumeNum": 1_250_000.5,
    "liquidityNum": 84_000.0,
    "bestBid": 0.41,
    "bestAsk": 0.43,
    "spread": 0.02,
    "active": True,
    "closed": False,
    "endDate": "2026-12-31T12:00:00Z",
    "events": [{"id": "9001", "title": "Government shutdown 2026"}],
}


def test_parses_prices_and_liquidity_from_gamma_shape():
    market = _parse_market(RAW_MARKET)
    assert market.market_id == "253591"
    assert market.event_id == "9001"
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == [0.42, 0.58]
    assert market.volume == 1_250_000.5
    assert market.liquidity == 84_000.0
    assert market.active is True
    assert market.closed is False
    assert market.end_date is not None
    assert market.end_date.year == 2026


def test_falls_back_to_condition_id_when_no_id():
    raw = dict(RAW_MARKET)
    del raw["id"]
    market = _parse_market(raw)
    assert market.market_id == "0xabc123"


def test_handles_missing_optional_fields_gracefully():
    minimal = {"id": "1", "question": "Q?", "outcomes": "[]", "outcomePrices": "[]"}
    market = _parse_market(minimal)
    assert market.market_id == "1"
    assert market.outcomes == []
    assert market.volume == 0.0
    assert market.liquidity == 0.0
    assert market.best_bid is None
    assert market.end_date is None
    assert market.active is False
