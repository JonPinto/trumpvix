# TrumpVIX

Local dashboard tracking Trump Truth Social statements against market reactions. No inference, no opinions — just data.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/init_db.py
python scripts/ingest_truth_rss.py
python scripts/ingest_intraday_alpha_vantage.py
python scripts/ingest_fred.py

python scripts/server.py
# → http://127.0.0.1:5000
```

## How it works

**Data sources:**
- **Statements** — RSS feed from [trumpstruth.org](https://trumpstruth.org/feed), stored with full provenance.
- **Intraday prices** — yfinance (S&P 500, NASDAQ, DJIA, BTC, ETH, oil, gas, FX) at ~5-min resolution.
- **Daily indicators** — FRED CSV (VIX, dollar index, etc.).
- **Link previews** — OpenGraph metadata from linked articles.

**Reaction model:** For each statement, the dashboard computes market change over a **10-hour market-time window** (counting only NYSE open hours: 9:30–16:00 ET, Mon–Fri). It finds the nearest intraday price before and after the window, and reports the percentage move.

**Frontend:** Single-page dark-themed UI. Swipe through posts, see the reaction summary on each card, expand side-by-side reactions for any post. Timeline bar shows 30 days of posts as dots.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MVP_DB_PATH` | `data/mvp.sqlite3` | SQLite database path |

## Scripts

| Script | Purpose |
|---|---|
| `init_db.py` | Create SQLite schema |
| `ingest_truth_rss.py` | Pull latest posts from RSS |
| `ingest_intraday_alpha_vantage.py` | Fetch intraday prices via yfinance |
| `ingest_fred.py` | Pull daily indicators from FRED |
| `fetch_link_previews.py` | Fetch OpenGraph metadata for linked URLs |
| `preview.py` | Print DB summary to terminal |
| `server.py` | Flask dev server |

## Design principles

- **Source-first.** Every statement has `source_url` and `original_url` — no fabrication.
- **Neutral presentation.** Timeline and indicators shown side by side. No causality claims.
- **Strict provenance.** Data stored as-is from upstream. No transformation beyond rounding.
