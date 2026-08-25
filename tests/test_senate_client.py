from datetime import date

from polytrader.congress.senate_client import (
    _normalize_ticker,
    _parse_amount_range,
    _parse_transaction,
    _strip_html,
)

# Representative records captured from the real
# timothycarambat/senate-stock-watcher-data feed during development.
REAL_SAMPLE_AGGREGATE_RECORD = {
    "transaction_date": "11/10/2020",
    "owner": "Spouse",
    "ticker": "BYND",
    "asset_description": "Beyond Meat, Inc.",
    "asset_type": "Stock",
    "type": "Sale (Full)",
    "amount": "$50,001 - $100,000",
    "comment": "--",
    "senator": "Ron L Wyden",
    "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/a0010f4a-c31a-4824-8b6d-6399b3ccb6f0/",
}

REAL_SAMPLE_HTML_TICKER_RECORD = {
    "transaction_date": "12/21/2018",
    "owner": "Joint",
    "ticker": '<a href="https://finance.yahoo.com/q?s=GFAFX" target="_blank">GFAFX</a>',
    "asset_description": "American Funds Growth Fund",
    "asset_type": "Other Securities",
    "type": "Purchase",
    "amount": "$1,001 - $15,000",
    "comment": "--",
}

REAL_SAMPLE_BOND_RECORD = {
    "transaction_date": "12/19/2018",
    "owner": "Spouse",
    "ticker": "--",
    "asset_description": (
        'Bank of America <div class="text-muted"><em>Rate/Coupon:</em> 3.0%<br> '
        "<em>Matures:</em> 06/21/2019</div>"
    ),
    "asset_type": "Corporate Bond",
    "type": "Purchase",
    "amount": "$15,001 - $50,000",
    "comment": "--",
}


def test_strip_html_removes_tags_and_unescapes_entities():
    assert _strip_html("Bank of America <div>Rate: 3.0%</div>") == "Bank of America Rate: 3.0%"
    assert _strip_html("A &amp; B") == "A & B"
    assert _strip_html(None) == ""


def test_normalize_ticker_extracts_from_html_anchor():
    assert _normalize_ticker(REAL_SAMPLE_HTML_TICKER_RECORD["ticker"]) == "GFAFX"


def test_normalize_ticker_treats_double_dash_as_unknown():
    assert _normalize_ticker("--") is None


def test_parse_amount_range_extracts_bounds():
    assert _parse_amount_range("$50,001 - $100,000") == (50_001.0, 100_000.0)
    assert _parse_amount_range("$1,000,001 - $5,000,000") == (1_000_001.0, 5_000_000.0)


def test_parse_amount_range_handles_unrecognized_strings():
    assert _parse_amount_range("Unknown") == (None, None)
    assert _parse_amount_range(None) == (None, None)


def test_parse_transaction_full_record():
    trade = _parse_transaction(
        REAL_SAMPLE_AGGREGATE_RECORD,
        senator=REAL_SAMPLE_AGGREGATE_RECORD["senator"],
        ptr_link=REAL_SAMPLE_AGGREGATE_RECORD["ptr_link"],
        disclosure_date=None,
    )
    assert trade.senator == "Ron L Wyden"
    assert trade.ticker == "BYND"
    assert trade.transaction_type == "sale_full"
    assert trade.transaction_date == date(2020, 11, 10)
    assert trade.disclosure_date is None
    assert trade.amount_low == 50_001.0
    assert trade.amount_high == 100_000.0


def test_parse_transaction_strips_html_from_bond_description():
    trade = _parse_transaction(
        REAL_SAMPLE_BOND_RECORD, senator="Thomas R Carper", ptr_link="https://example.com/ptr/1", disclosure_date=date(2019, 1, 3)
    )
    assert trade.ticker is None  # "--" means unknown
    assert "<div" not in trade.asset_description
    assert "Matures: 06/21/2019" in trade.asset_description
    assert trade.disclosure_date == date(2019, 1, 3)


def test_parse_transaction_returns_none_without_transaction_date():
    bad = dict(REAL_SAMPLE_AGGREGATE_RECORD, **{"transaction_date": None})
    assert _parse_transaction(bad, senator="X", ptr_link="", disclosure_date=None) is None


def test_parse_transaction_handles_na_type():
    raw = dict(REAL_SAMPLE_AGGREGATE_RECORD, **{"type": "N/A"})
    trade = _parse_transaction(raw, senator="X", ptr_link="", disclosure_date=None)
    assert trade.transaction_type == "n/a"
