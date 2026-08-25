# Architecture

## Thesis

The government-contract intelligence engine is not a separate product; it
is the proprietary evidence layer that lets the Polymarket trader estimate
probabilities from concrete government/market actions instead of asking an
LLM to guess. The congressional-trading tracker is a second evidence
stream feeding the same pipeline, plus its own directly-tradable idea
(mirror what Congress discloses).

```
Government/Congress intelligence -> Event detection -> Probability model
    -> Polymarket edge -> Risk manager -> Trade (paper)
```

## Five layers

1. **Ingestion** (`polymarket/gamma_client.py`, `contracts/usaspending_client.py`,
   `contracts/federal_register_client.py`, `congress/senate_client.py`,
   `congress/house_client.py`) — source-specific connectors. Every record
   they produce carries a source URL and an ingestion timestamp; see
   `docs/DATA_SOURCES.md`.
2. **Canonical store** (`core/models.py`, SQLite by default / Postgres via
   `DATABASE_URL`) — Markets, Contracts, CongressTrades, Organizations,
   Agencies, SourceDocuments, and the trading pipeline tables below.
3. **Event intelligence** (`contracts/entity_resolution.py`,
   `signals/matcher.py`) — normalizes entity name variants ("DoD" / "Dept.
   of Defense" / "Department of Defense" -> one `GovernmentAgency` row) and
   asks, for every new contract award or congress trade, whether it affects
   any open Polymarket market (rule-based keyword/entity overlap in v1).
4. **Trading intelligence** (`polymarket/research.py`, `core/edge.py`) —
   turns a market + evidence into a `Forecast` (P(YES), confidence,
   uncertainty) and then an `EdgeCalculator` result (fair probability vs.
   executable price, net of spread/fees/slippage/uncertainty buffer).
5. **Risk + execution** (`core/risk.py`, `polymarket/paper_portfolio.py`,
   `congress/mirror_strategy.py`) — completely separate from the layers
   above. A `Forecast` saying "87% probability" is a recommendation; the
   risk engine can and will veto it. This boundary is the one rule that
   makes the merge of the three PRDs coherent: **every** signal, regardless
   of which module produced it or which asset class it trades, becomes a
   `Signal` row, gets sized/vetoed by the *same* `RiskDecision` logic, and
   only then becomes an `Order`.

## Why one risk engine for two asset classes

`Signal`, `RiskDecision`, `Order`, `Position`, and `TradeDecision`
(`core/models.py`) all carry an `asset_class` (`polymarket` | `equity`).
The Polymarket edge engine and the congressional mirror strategy are
different *signal generators* feeding the identical downstream pipeline:
fractional-Kelly sizing, per-trade/market/category/portfolio caps, daily
loss pause, max-drawdown halt, kill switch. Concretely this means a
correlated-exposure bug or a bankroll blowup can't hide in one module
because it wasn't wired through the shared gate — there's only one gate.

## What's deliberately not built yet

- **Live trading of any kind.** `Settings.live_trading_enabled` defaults to
  `false`; nothing in this codebase places a real order. Polymarket's CLOB
  client (`polymarket/clob_client.py`) is a documented stub. The
  congressional mirror strategy paper-executes into our own portfolio
  simulator, not a real brokerage — no broker credentials are required to
  run this system, and none are handled anywhere in the code.
- **Web dashboard / alerts.** The CLI (`polytrader ...`) is the only UX.
  A FastAPI read layer over the same database is the natural next step
  when a dashboard is wanted.
- **SAM.gov and Congress.gov connectors.** Both require an `api.data.gov`
  key the user hasn't provided; `contracts/usaspending_client.py` and
  `contracts/federal_register_client.py` need no key and cover contract
  awards and executive/regulatory actions without one.
- **Full PDF/OCR extraction of House PTR filings.** See
  `docs/DATA_SOURCES.md` for the House Clerk gap.
- **LLM-assisted event -> market matching and true entity resolution.**
  `signals/matcher.py` and `contracts/entity_resolution.py` are honest v1
  heuristics (keyword overlap, alias table), not ML models — upgrading them
  doesn't require touching anything downstream, since they still just
  produce `Signal` / canonical `Organization` rows.

## Roadmap (beyond this build)

- Phase 2: FastAPI + web dashboard, alerting (email/Discord/Telegram),
  SAM.gov + Congress.gov connectors, LLM-assisted entity resolution and
  event-market matching, House PTR PDF extraction.
- Phase 3: Alpaca (or similar) paper-then-live equities broker for the
  congress mirror strategy, Polymarket CLOB live execution behind the
  Live-Trading Gate criteria from the original PRD (minimum paper-trading
  duration, positive friction-adjusted expectancy, calibration tracking,
  tested kill switch, explicit operator enablement).
- Phase 4: multi-model research ensemble, portfolio optimization across
  correlated events, backtest/replay + calibration reporting.
