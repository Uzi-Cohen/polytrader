# PolyTrader

A risk-first Polymarket trading system whose research edge comes from a
proprietary intelligence layer: US government contract awards/regulatory
actions, and congressional (STOCK Act) trading disclosures. One
deterministic risk engine gates every trade — Polymarket or equity-mirror —
before any capital (paper today, live only behind an explicit gate) moves.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for what each connector
pulls from, and its known gaps.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,llm]"
cp .env.example .env   # edit as needed; nothing is required to run paper mode

polytrader init-db

polytrader scan-polymarket --category politics --limit 20
polytrader ingest-contracts --keyword "artificial intelligence" --agency "Department of Defense"
polytrader ingest-congress
polytrader signals
polytrader portfolio
polytrader risk-check --asset-class polymarket --bankroll 10000 --proposed-size 500 \
  --fair-probability 0.6 --market-price 0.45
```

All commands work against real, free, public APIs (Polymarket Gamma,
USASpending.gov, Federal Register, the maintained Senate Stock Watcher
data mirror) — no API keys required for paper-mode data ingestion. Setting
`ANTHROPIC_API_KEY` additionally enables the Claude-backed Polymarket
probability estimator (`polytrader/polymarket/research.py`); without it,
forecasts simply aren't generated — the system never fabricates a
probability to fill the gap.

## Status

Paper-trading only. Live execution (Polymarket CLOB, any equities broker)
is out of scope for this build and stays behind `LIVE_TRADING_ENABLED=false`
in every code path that matters, per both PRDs' Live-Trading Gate.

## Tests

```bash
pytest
```
