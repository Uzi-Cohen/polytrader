"""Backtests the congress-mirror strategy against real historical Senate
disclosures and real historical stock prices: "if this system had been
running for the last decade, what would have happened?"

Reuses the exact decision-making code the live system uses --
`build_mirror_proposal` for sizing/edge and `RiskEngine.evaluate` for risk
gating -- so this isn't a separate, possibly-diverging simulation of the
strategy; it's the real strategy logic driven by history instead of a
live event feed. Execution/accounting here is backtest-only, with real
entry/exit prices from Stooq; it never touches the live paper-trading
Position/Order tables (those stay notional-only in mirror_strategy.py
because no live price feed is wired in there).

Stated limitations, not hidden ones:
- Every decision is evaluated with `now` set to the (real or assumed)
  disclosure date itself -- i.e. this assumes same-day detection of every
  disclosure. mirror_strategy's edge-decay-with-lag logic is therefore
  always at its freshest value here (0.12) and never actually discriminates
  between trades on edge; what the risk engine is really testing in this
  backtest is the sizing/exposure caps, not the edge threshold. A version
  that models bot polling latency (detecting a disclosure some days after
  it posts) would need to pass a later `now` and would see real edge decay.
- `senate_client.fetch_all_transactions()` (the practical way to get a
  decade of history in one request) has no per-transaction disclosure
  date -- only `fetch_daily_filing` does, and pulling exact disclosure
  dates for a decade would mean thousands of day-by-day requests. Instead
  this assumes a configurable disclosure lag (default 30 days) after each
  transaction_date. Every closed trade in the report is tagged with
  whether its entry used a real or assumed disclosure date, and the
  report's assumed-lag count is always shown -- this build always uses
  the assumed value below, since it never fetches per-day filings itself,
  but the field exists so a caller who mixes in real Legislator/CongressTrade
  rows (which do carry real disclosure_date) isn't silently misrepresented.
- Exit rule: a mirrored position closes when the same legislator discloses
  a sale of that ticker, or is marked-to-market at the backtest's end date
  if never sold. A real strategy might use a different exit rule (a stop,
  a holding-period limit, etc.) -- this is "buy when they buy, sell when
  they sell," nothing more.
- "Shares" are notional / price, not the legislator's actual share count
  -- position size comes from the STOCK Act amount bracket via
  mirror_strategy's conviction heuristic, not from mirroring real share
  quantities.
- A flat `fee_bps` assumption stands in for real bid/ask spread, since
  there's no historical spread data for these securities.
- Tickers Stooq has no history for (delisted, OTC, renamed, mis-parsed
  from HTML) are skipped and counted in `skipped_no_price_data`, not
  silently dropped from the trade count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from polytrader.congress.mirror_strategy import build_mirror_proposal
from polytrader.congress.price_history import PricePoint, StooqClient, price_on_or_before
from polytrader.congress.senate_client import SenateTrade
from polytrader.core.models import CongressTrade
from polytrader.core.risk import PortfolioState, RiskDecisionOutcome, RiskEngine, RiskLimits

DEFAULT_ASSUMED_DISCLOSURE_LAG_DAYS = 30


@dataclass
class _OpenPosition:
    ticker: str
    entry_date: date
    entry_price: float
    notional: float


@dataclass(frozen=True)
class ClosedTrade:
    ticker: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    notional: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "sold_by_legislator" | "backtest_end"
    disclosure_date_assumed: bool


@dataclass
class BacktestReport:
    starting_bankroll: float
    ending_bankroll: float
    assumed_disclosure_lag_days: int
    considered: int = 0
    skipped_not_actionable: int = 0
    skipped_no_price_data: int = 0
    skipped_by_risk_engine: int = 0
    closed_trades: list[ClosedTrade] = field(default_factory=list)

    @property
    def total_return_pct(self) -> float:
        if self.starting_bankroll <= 0:
            return 0.0
        return (self.ending_bankroll / self.starting_bankroll - 1) * 100

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        return wins / len(self.closed_trades)

    @property
    def max_drawdown_pct(self) -> float:
        equity = self.starting_bankroll
        peak = equity
        max_dd = 0.0
        for trade in sorted(self.closed_trades, key=lambda t: t.exit_date):
            equity += trade.pnl
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd * 100


def _to_backtest_congress_trade(trade: SenateTrade, *, assumed_lag_days: int) -> tuple[CongressTrade, bool]:
    """Builds a transient (never persisted) CongressTrade so we can reuse
    build_mirror_proposal's real conviction/edge-decay logic. Never added
    to a session -- this does not touch the database or its
    'NULL disclosure_date means unknown, not same-day' invariant."""
    disclosure_date_assumed = trade.disclosure_date is None
    disclosure_date = trade.disclosure_date or (trade.transaction_date + timedelta(days=assumed_lag_days))
    congress_trade = CongressTrade(
        trade_id=f"backtest:{trade.senator}:{trade.ticker}:{trade.transaction_date}:{trade.transaction_type}",
        legislator_id="backtest",
        ticker=trade.ticker,
        asset_description=trade.asset_description,
        transaction_type=trade.transaction_type,
        transaction_date=datetime(*trade.transaction_date.timetuple()[:3], tzinfo=timezone.utc),
        disclosure_date=datetime(*disclosure_date.timetuple()[:3], tzinfo=timezone.utc),
        amount_range_low=trade.amount_low,
        amount_range_high=trade.amount_high,
        source_url=trade.ptr_link,
    )
    return congress_trade, disclosure_date_assumed


def run_backtest(
    trades: list[SenateTrade],
    *,
    start_date: date,
    end_date: date,
    starting_bankroll: float,
    base_mirror_unit: float,
    risk_limits: RiskLimits,
    price_client: StooqClient,
    fee_bps: float = 10.0,
    assumed_disclosure_lag_days: int = DEFAULT_ASSUMED_DISCLOSURE_LAG_DAYS,
) -> BacktestReport:
    engine = RiskEngine(risk_limits)
    bankroll = starting_bankroll
    open_positions: dict[str, _OpenPosition] = {}
    price_cache: dict[str, list[PricePoint]] = {}
    report = BacktestReport(
        starting_bankroll=starting_bankroll,
        ending_bankroll=starting_bankroll,
        assumed_disclosure_lag_days=assumed_disclosure_lag_days,
    )

    def get_prices(ticker: str) -> list[PricePoint]:
        if ticker not in price_cache:
            price_cache[ticker] = price_client.get_daily_history(ticker)
        return price_cache[ticker]

    prepared = []
    for raw_trade in trades:
        if not (start_date <= raw_trade.transaction_date <= end_date):
            continue
        congress_trade, lag_assumed = _to_backtest_congress_trade(
            raw_trade, assumed_lag_days=assumed_disclosure_lag_days
        )
        prepared.append((congress_trade, lag_assumed))
    prepared.sort(key=lambda pair: pair[0].disclosure_date)

    for congress_trade, lag_assumed in prepared:
        report.considered += 1
        disclosure_date = congress_trade.disclosure_date.date()
        proposal = build_mirror_proposal(
            congress_trade, base_mirror_unit=base_mirror_unit, now=congress_trade.disclosure_date
        )
        if proposal is None:
            report.skipped_not_actionable += 1
            continue

        prices = get_prices(proposal.ticker)
        price = price_on_or_before(prices, disclosure_date)

        if proposal.direction == "sell":
            position = open_positions.pop(proposal.ticker, None)
            if position is None:
                continue
            if price is None:
                report.skipped_no_price_data += 1
                continue
            exit_price = price * (1 - fee_bps / 10_000)
            pnl = (exit_price / position.entry_price - 1) * position.notional
            bankroll += pnl
            report.closed_trades.append(
                ClosedTrade(
                    ticker=position.ticker,
                    entry_date=position.entry_date,
                    exit_date=disclosure_date,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    notional=position.notional,
                    pnl=pnl,
                    pnl_pct=(exit_price / position.entry_price - 1) * 100,
                    exit_reason="sold_by_legislator",
                    disclosure_date_assumed=lag_assumed,
                )
            )
            continue

        # buy
        if proposal.ticker in open_positions:
            continue  # already mirroring this ticker -- no pyramiding in this backtest
        if price is None:
            report.skipped_no_price_data += 1
            continue

        evaluation = engine.evaluate(proposal.trade_proposal, PortfolioState(bankroll=bankroll))
        if evaluation.outcome == RiskDecisionOutcome.REJECTED or evaluation.approved_size <= 0:
            report.skipped_by_risk_engine += 1
            continue

        entry_price = price * (1 + fee_bps / 10_000)
        open_positions[proposal.ticker] = _OpenPosition(
            ticker=proposal.ticker, entry_date=disclosure_date, entry_price=entry_price, notional=evaluation.approved_size
        )

    for position in open_positions.values():
        exit_price = price_on_or_before(get_prices(position.ticker), end_date)
        if exit_price is None:
            continue
        pnl = (exit_price / position.entry_price - 1) * position.notional
        bankroll += pnl
        report.closed_trades.append(
            ClosedTrade(
                ticker=position.ticker,
                entry_date=position.entry_date,
                exit_date=end_date,
                entry_price=position.entry_price,
                exit_price=exit_price,
                notional=position.notional,
                pnl=pnl,
                pnl_pct=(exit_price / position.entry_price - 1) * 100,
                exit_reason="backtest_end",
                disclosure_date_assumed=True,
            )
        )

    report.ending_bankroll = bankroll
    return report
