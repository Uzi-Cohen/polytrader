# Data sources

Every connector below writes a `SourceDocument` (or, for Polymarket
snapshots, the raw API response is the source of truth by URL) so every
downstream fact stays traceable — per both PRDs' provenance requirement,
and because prediction-market trading on anything but public information
is exactly the practice under scrutiny industry-wide right now. Nothing in
this system ingests non-public or authenticated government data.

## Polymarket — `polymarket/gamma_client.py`

- **Endpoint:** `https://gamma-api.polymarket.com` (public market/event
  discovery — no auth, no key).
- **Used for:** active markets, questions, categories, prices, volume,
  liquidity, resolution metadata (FR-SCAN-01/02).
- **Not used:** the CLOB (`clob.polymarket.com`) — that's wallet-authenticated
  order placement/live order books, out of scope for this paper-only build.
  `polymarket/clob_client.py` exists only as a documented interface stub
  for when live trading is deliberately enabled later.
- **Rate limits:** ~4,000 req/10s on Gamma as of 2026 — the scanner's
  default cadence stays well under that.

## US contract awards — `contracts/usaspending_client.py`

- **Endpoint:** `https://api.usaspending.gov/api/v2/search/spending_by_award/`
  (POST, free, no key).
- **Used for:** federal award records — vendor, awarding agency, value,
  dates, award description/category, permalink — across all agencies, not
  just one sector, so this covers "general US contracts" per the user's
  request (broader than PRD 2's original Israel/cybersecurity pilot scope).
- **Value semantics:** USASpending exposes `award_amount` (disclosed) and
  potential/ceiling amounts separately where available; `Contract.value_type`
  records which one was stored, per PRD 2's "never combine incompatible
  value measures" principle.

## Executive/regulatory actions — `contracts/federal_register_client.py`

- **Endpoint:** `https://www.federalregister.gov/api/v1/documents.json`
  (free, no key).
- **Used for:** executive orders, agency rules/notices — the "regulatory
  decisions" and "executive actions" event types from the merged PRD
  thesis, feeding the same `ContractEvent`-style timeline and the signal
  matcher.

## Congressional trading — `congress/senate_client.py`, `congress/house_client.py`

STOCK Act periodic transaction reports are the official source
(`efdsearch.senate.gov` for the Senate, `disclosures-clerk.house.gov` for
the House), but neither exposes a clean structured API — both are
filing-search UIs backed by PDFs (and Senate additionally offers per-filer
XML/paper images).

- **Senate — real, working data this build.**
  `congress/senate_client.py` pulls from
  [`timothycarambat/senate-stock-watcher-data`](https://github.com/timothycarambat/senate-stock-watcher-data),
  a GitHub-hosted JSON mirror confirmed actively maintained as of this
  build (updated as filings land), itself derived from the official Senate
  eFD system. Each `CongressTrade` keeps `source_url` pointing at the
  underlying filing so the mirror is auditable back to a specific
  disclosure, not just "GitHub said so."
- **House — known gap, not faked.** `congress/house_client.py` scrapes the
  House Clerk's public financial-disclosure **index**
  (filer name, filing date, report type, PDF link) — real metadata, real
  links. It does **not** extract individual buy/sell transactions from the
  PTR PDFs themselves; that needs a PDF (sometimes scanned-image/OCR)
  extraction pipeline that's out of scope for this pass and is called out
  explicitly here and in `docs/ARCHITECTURE.md` rather than approximated
  with placeholder data. Swapping in a maintained House-side JSON mirror,
  if/when one exists again (House Stock Watcher's public feed is dead as
  of this build — confirmed 403s, unmaintained since mid-2025), or adding
  PDF extraction, are the two ways to close this gap.
- **Disclosure lag.** STOCK Act filings are due within 45 days of the
  transaction. `CongressTrade.transaction_date` and `.disclosure_date` are
  both stored and never collapsed into one value — `mirror_strategy.py`
  uses the gap explicitly rather than pretending the signal is fresher
  than it is.

## Historical stock prices — `congress/price_history.py`

- **Endpoint:** `https://stooq.com/q/d/l/` (free, no key, daily CSV
  history per ticker).
- **Used for:** backtesting the congress mirror strategy only
  (`congress/backtest.py`, `polytrader backtest-congress-mirror`) --
  never in live/paper mode, which has no live price feed wired in (see
  `congress/mirror_strategy.py`).
- Not live-tested from inside this build's sandbox (same egress-policy
  block as every other non-GitHub host). The backtest pipeline itself
  *was* run end-to-end against the real, live Senate feed (8,350 real
  transactions, 995 real tickers) with a synthetic flat price stand-in,
  to prove the code handles real-world data shape and volume without
  choking -- see `tests/test_backtest_live.py`. The price parsing itself
  is fixture-tested against Stooq's documented CSV format
  (`tests/test_price_history.py`); verify against a live response the
  first time it runs somewhere that can reach the host.

## Not integrated (documented, not silently missing)

- **SAM.gov** (entity/contract-opportunity data) — requires an
  `api.data.gov` key the user hasn't provided.
- **Congress.gov** (bills/legislative activity) — same key requirement.
- **Blockchain/on-chain Polymarket activity** — the merged-thesis writeup
  mentions this as a future signal; not built here.

## Legal/compliance note

Every source above is public and free; connectors respect documented rate
limits and don't authenticate against or scrape access-controlled systems,
per both PRDs' non-goals. This does not constitute legal advice on
scraping/ToS for any given source — review each source's terms before
scaling collection frequency or volume beyond this build's defaults.
