from datetime import datetime, timedelta, timezone

from polytrader.core.models import Market, MarketSnapshot
from polytrader.polymarket.gamma_client import GammaMarket
from polytrader.polymarket.scanner import ScanFilters, opportunity_score, passes_filters, scan

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def make_market(**overrides) -> GammaMarket:
    defaults = dict(
        market_id="m1",
        event_id="e1",
        question="Will X happen?",
        category="Politics",
        outcomes=["Yes", "No"],
        outcome_prices=[0.4, 0.6],
        volume=10_000.0,
        liquidity=20_000.0,
        best_bid=0.39,
        best_ask=0.41,
        spread=0.02,
        active=True,
        closed=False,
        end_date=NOW + timedelta(days=30),
        raw={},
    )
    defaults.update(overrides)
    return GammaMarket(**defaults)


def test_passes_filters_accepts_eligible_market():
    filters = ScanFilters(min_liquidity=5_000, min_volume=1_000, max_spread=0.1, min_hours_to_resolution=1)
    assert passes_filters(make_market(), filters, now=NOW) is True


def test_rejects_low_liquidity():
    filters = ScanFilters(min_liquidity=50_000)
    assert passes_filters(make_market(liquidity=1_000), filters, now=NOW) is False


def test_rejects_wide_spread():
    filters = ScanFilters(max_spread=0.01)
    assert passes_filters(make_market(spread=0.05), filters, now=NOW) is False


def test_rejects_market_too_close_to_resolution():
    filters = ScanFilters(min_hours_to_resolution=48)
    near_term = make_market(end_date=NOW + timedelta(hours=2))
    assert passes_filters(near_term, filters, now=NOW) is False


def test_rejects_closed_or_inactive_markets():
    filters = ScanFilters()
    assert passes_filters(make_market(active=False), filters, now=NOW) is False
    assert passes_filters(make_market(closed=True), filters, now=NOW) is False


def test_category_filter_is_case_insensitive():
    filters = ScanFilters(category="politics")
    assert passes_filters(make_market(category="Politics"), filters, now=NOW) is True
    assert passes_filters(make_market(category="Sports"), filters, now=NOW) is False


def test_opportunity_score_penalizes_wide_spread():
    tight = make_market(liquidity=10_000, spread=0.01)
    wide = make_market(liquidity=10_000, spread=0.20)
    assert opportunity_score(tight) > opportunity_score(wide)


def test_scan_persists_market_and_snapshot(db_session):
    filters = ScanFilters(min_liquidity=1_000, min_volume=100, min_hours_to_resolution=1)
    results = scan(db_session, [make_market()], filters, now=NOW)

    assert len(results) == 1
    assert results[0].is_duplicate is False

    stored_market = db_session.get(Market, "m1")
    assert stored_market is not None
    assert stored_market.question == "Will X happen?"

    snapshots = db_session.query(MarketSnapshot).filter_by(market_id="m1").all()
    assert len(snapshots) == 1
    assert snapshots[0].yes_price == 0.4


def test_scan_dedups_unchanged_market_within_window(db_session):
    filters = ScanFilters(min_liquidity=1_000, min_volume=100, min_hours_to_resolution=1)
    scan(db_session, [make_market()], filters, now=NOW)

    # Same price, 5 minutes later -> should be flagged duplicate, no new snapshot.
    later = NOW + timedelta(minutes=5)
    results = scan(db_session, [make_market()], filters, now=later, dedup_window=timedelta(hours=1))

    assert results[0].is_duplicate is True
    snapshots = db_session.query(MarketSnapshot).filter_by(market_id="m1").all()
    assert len(snapshots) == 1  # no duplicate snapshot written


def test_scan_does_not_dedup_when_price_changes(db_session):
    filters = ScanFilters(min_liquidity=1_000, min_volume=100, min_hours_to_resolution=1)
    scan(db_session, [make_market()], filters, now=NOW)

    later = NOW + timedelta(minutes=5)
    moved = make_market(outcome_prices=[0.55, 0.45])
    results = scan(db_session, [moved], filters, now=later, dedup_window=timedelta(hours=1))

    assert results[0].is_duplicate is False
    snapshots = db_session.query(MarketSnapshot).filter_by(market_id="m1").all()
    assert len(snapshots) == 2
