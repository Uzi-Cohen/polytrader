from datetime import date

from polytrader.congress.price_history import _parse_csv, price_on_or_before

SAMPLE_STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2020-01-02,74.06,75.15,73.80,75.09,135480400
2020-01-03,74.29,75.14,74.13,74.36,146322800
2020-01-06,73.45,74.99,73.19,74.95,118387200
"""


def test_parse_csv_extracts_close_prices_in_ascending_date_order():
    points = _parse_csv(SAMPLE_STOOQ_CSV)
    assert len(points) == 3
    assert points[0].trade_date == date(2020, 1, 2)
    assert points[0].close == 75.09
    assert points[-1].trade_date == date(2020, 1, 6)


def test_parse_csv_handles_unknown_symbol_response():
    assert _parse_csv("No data") == []
    assert _parse_csv("") == []
    assert _parse_csv("<html>not found</html>") == []


def test_price_on_or_before_finds_most_recent_prior_close():
    points = _parse_csv(SAMPLE_STOOQ_CSV)
    # exact match
    assert price_on_or_before(points, date(2020, 1, 3)) == 74.36
    # weekend/holiday gap -> most recent prior trading day
    assert price_on_or_before(points, date(2020, 1, 5)) == 74.36
    # before any data
    assert price_on_or_before(points, date(2019, 12, 1)) is None


def test_price_on_or_before_never_returns_a_future_price():
    points = _parse_csv(SAMPLE_STOOQ_CSV)
    # asking for a date after the last close should never peek forward
    result = price_on_or_before(points, date(2020, 1, 4))
    assert result == 74.36  # the 1/3 close, not the 1/6 close
