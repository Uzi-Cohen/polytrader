"""Single CLI entrypoint tying every module together. This is the whole
UX for this build -- no web dashboard yet (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import typer
from rich.console import Console
from rich.table import Table

from polytrader.congress.house_client import HouseClerkClient
from polytrader.congress.ingest import ingest_house_filing_index_entry, ingest_senate_trade
from polytrader.congress.mirror_strategy import mirror_trade
from polytrader.congress.senate_client import SenateStockWatcherClient
from polytrader.contracts.federal_register_client import FederalRegisterClient
from polytrader.contracts.ingest import ingest_contract_award, ingest_register_document
from polytrader.contracts.usaspending_client import USASpendingClient
from polytrader.core.config import get_settings
from polytrader.core.db import init_db, session_scope
from polytrader.core.edge import compute_edge
from polytrader.core.models import AssetClass, CongressTrade, Signal
from polytrader.core.portfolio import build_portfolio_state
from polytrader.core.risk import PortfolioState, RiskEngine, RiskLimits, TradeProposal
from polytrader.polymarket.gamma_client import GammaClient
from polytrader.polymarket.scanner import ScanFilters, scan
from polytrader.signals.matcher import wire_signal_matcher

app = typer.Typer(help="PolyTrader: Polymarket trader + US contracts intelligence + congressional mirror.")
console = Console()


@app.command("init-db")
def init_db_command() -> None:
    """Create all tables (SQLite by default; see DATABASE_URL)."""
    init_db()
    console.print("[green]Database initialized.[/green]")


@app.command("scan-polymarket")
def scan_polymarket_command(
    category: str | None = typer.Option(None, help="Filter to one Gamma category, e.g. Politics."),
    limit: int = typer.Option(50, help="Max markets to fetch from Gamma."),
    min_liquidity: float = typer.Option(5_000.0),
    min_volume: float = typer.Option(1_000.0),
    max_spread: float = typer.Option(0.10),
    min_hours_to_resolution: float = typer.Option(6.0),
) -> None:
    """FR-SCAN-01..05: discover, filter, rank eligible Polymarket markets.

    This does not produce a forecast or edge -- there's no automated
    evidence-gathering step in this build (FR-RES-01's "collect facts from
    approved tools" needs a research/search integration not wired here).
    Use this to find what's worth researching, not what to trade.
    """
    with GammaClient() as gamma, session_scope() as session:
        markets = gamma.list_markets(limit=limit, category=category)
        filters = ScanFilters(
            min_liquidity=min_liquidity,
            min_volume=min_volume,
            max_spread=max_spread,
            min_hours_to_resolution=min_hours_to_resolution,
            category=category,
        )
        results = scan(session, markets, filters)

    table = Table(title=f"{len(results)} eligible markets")
    table.add_column("Market ID")
    table.add_column("Question", max_width=50)
    table.add_column("YES")
    table.add_column("Liquidity")
    table.add_column("Volume")
    table.add_column("Score")
    for r in results:
        table.add_row(
            r.market.market_id,
            r.market.question,
            f"{r.yes_price:.2f}",
            f"{r.market.liquidity:,.0f}",
            f"{r.market.volume:,.0f}",
            f"{r.opportunity_score:,.0f}",
        )
    console.print(table)


@app.command("ingest-contracts")
def ingest_contracts_command(
    keyword: list[str] = typer.Option([], help="Repeatable keyword filter, e.g. --keyword AI --keyword drone."),
    agency: str | None = typer.Option(None, help="Awarding agency name filter."),
    since: str | None = typer.Option(None, help="ISO date, e.g. 2026-01-01."),
    limit: int = typer.Option(50),
) -> None:
    """FR ingestion: pull US federal contract awards from USASpending.gov."""
    start = date.fromisoformat(since) if since else None
    with USASpendingClient() as client, session_scope() as session:
        wire_signal_matcher(session)
        awards = client.search_awards(keywords=keyword or None, agency_name=agency, start_date=start, limit=limit)
        for award in awards:
            ingest_contract_award(session, award)

    console.print(f"[green]Ingested {len(awards)} contract award(s).[/green]")


@app.command("ingest-regulations")
def ingest_regulations_command(
    term: str | None = typer.Option(None, help="Federal Register search term, e.g. 'artificial intelligence'."),
    since: str | None = typer.Option(None, help="ISO date, e.g. 2026-01-01."),
    per_page: int = typer.Option(50),
) -> None:
    """Executive orders / agency rules from federalregister.gov."""
    start = date.fromisoformat(since) if since else None
    with FederalRegisterClient() as client, session_scope() as session:
        wire_signal_matcher(session)
        documents = client.search_documents(term=term, start_date=start, per_page=per_page)
        for document in documents:
            ingest_register_document(session, document)

    console.print(f"[green]Ingested {len(documents)} regulatory document(s).[/green]")


@app.command("ingest-congress")
def ingest_congress_command(
    days: int = typer.Option(7, help="How many recent calendar days of Senate daily filings to check."),
    backfill: bool = typer.Option(False, help="Instead, pull the full historical aggregate feed (no disclosure_date, never mirrored)."),
    mirror: bool = typer.Option(True, help="Auto-mirror newly-ingested, actionable disclosures as paper trades."),
    base_unit: float = typer.Option(250.0, help="Base paper-mirror dollar size before conviction scaling."),
) -> None:
    """Ingest Senate STOCK Act disclosures and (by default) paper-mirror
    the actionable ones through the shared risk engine."""
    settings = get_settings()
    new_trades: list[CongressTrade] = []

    with SenateStockWatcherClient() as client, session_scope() as session:
        wire_signal_matcher(session)

        if backfill:
            for trade in client.fetch_all_transactions():
                stored = ingest_senate_trade(session, trade)
                if stored:
                    new_trades.append(stored)
        else:
            today = datetime.now(timezone.utc).date()
            for offset in range(days):
                day = today - timedelta(days=offset)
                try:
                    day_trades = client.fetch_daily_filing(day)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        continue  # nothing filed that day
                    raise
                for trade in day_trades:
                    stored = ingest_senate_trade(session, trade)
                    if stored:
                        new_trades.append(stored)

        console.print(f"[green]Ingested {len(new_trades)} new Senate disclosure(s).[/green]")

        if mirror and new_trades:
            engine = RiskEngine(RiskLimits.from_settings(settings))
            mirrored = 0
            for trade in new_trades:
                portfolio = build_portfolio_state(
                    session, asset_class=AssetClass.EQUITY, starting_bankroll=settings.mirror_starting_bankroll
                )
                result = mirror_trade(session, trade, risk_engine=engine, portfolio=portfolio, base_mirror_unit=base_unit)
                if result["outcome"] == "traded":
                    mirrored += 1
            console.print(f"[green]Paper-mirrored {mirrored} of {len(new_trades)} new disclosure(s).[/green]")


@app.command("ingest-house-filings")
def ingest_house_filings_command(year: int = typer.Option(datetime.now(timezone.utc).year)) -> None:
    """Index-level only: filer, filing date, PDF link -- no transactions.
    See docs/DATA_SOURCES.md for why."""
    with HouseClerkClient() as client, session_scope() as session:
        entries = client.fetch_filing_index(year)
        for entry in entries:
            ingest_house_filing_index_entry(session, entry)

    console.print(f"[green]Indexed {len(entries)} House filing(s) for {year} (metadata only).[/green]")


@app.command("signals")
def signals_command(limit: int = typer.Option(20)) -> None:
    """Recent candidate signals, from any source."""
    with session_scope() as session:
        rows = session.query(Signal).order_by(Signal.created_at.desc()).limit(limit).all()
        table = Table(title=f"{len(rows)} recent signal(s)")
        table.add_column("Source")
        table.add_column("Asset")
        table.add_column("Market/Symbol")
        table.add_column("Direction")
        table.add_column("Confidence")
        table.add_column("Thesis", max_width=60)
        for s in rows:
            table.add_row(
                s.source_type.value,
                s.asset_class.value,
                s.market_id or s.symbol or "-",
                s.direction,
                f"{s.confidence:.2f}",
                s.thesis or "",
            )
    console.print(table)


@app.command("portfolio")
def portfolio_command() -> None:
    """Paper bankroll/exposure across both asset classes."""
    settings = get_settings()
    with session_scope() as session:
        poly_state = build_portfolio_state(
            session, asset_class=AssetClass.POLYMARKET, starting_bankroll=settings.polymarket_starting_bankroll
        )
        equity_state = build_portfolio_state(
            session, asset_class=AssetClass.EQUITY, starting_bankroll=settings.mirror_starting_bankroll
        )

    table = Table(title="Paper portfolio")
    table.add_column("Asset class")
    table.add_column("Bankroll (equity)")
    table.add_column("Exposure")
    table.add_column("Open markets/symbols")
    for label, state in (("polymarket", poly_state), ("equity (congress mirror)", equity_state)):
        table.add_row(label, f"${state.bankroll:,.2f}", f"${state.total_exposure:,.2f}", str(len(state.market_exposure)))
    console.print(table)


@app.command("risk-check")
def risk_check_command(
    asset_class: str = typer.Option("polymarket"),
    market_or_symbol: str = typer.Option(...),
    bankroll: float = typer.Option(...),
    executable_price: float = typer.Option(0.5),
    fair_probability: float | None = typer.Option(None),
    proposed_size: float | None = typer.Option(None),
    category: str | None = typer.Option(None),
) -> None:
    """Dry-run a trade proposal through the deterministic risk engine
    without touching any data -- for testing/demoing core/risk.py."""
    settings = get_settings()
    engine = RiskEngine(RiskLimits.from_settings(settings))

    edge = 0.10
    if fair_probability is not None:
        edge_result = compute_edge(fair_probability=fair_probability, market_price=executable_price)
        edge = edge_result.adjusted_edge

    proposal = TradeProposal(
        asset_class=AssetClass(asset_class),
        market_or_symbol=market_or_symbol,
        executable_price=executable_price,
        adjusted_edge=edge,
        category=category,
        fair_probability=fair_probability,
        proposed_notional=proposed_size,
        now=datetime.now(timezone.utc),
    )
    decision = engine.evaluate(proposal, PortfolioState(bankroll=bankroll))

    console.print(f"Outcome: [bold]{decision.outcome.value}[/bold]")
    console.print(f"Proposed size: ${decision.proposed_size:,.2f}")
    console.print(f"Approved size: ${decision.approved_size:,.2f}")
    console.print(f"Limits hit: {decision.limits_hit or 'none'}")
    console.print(f"Reasons: {decision.reasons}")


if __name__ == "__main__":
    app()
