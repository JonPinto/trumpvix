# Trump Statement Impact Dashboard (Local MVP)

Local-only MVP: ingest Trump Truth RSS + FRED indicators into SQLite, then view a neutral timeline and indicator context.

## Requirements
- Python 3.10+
- Internet access for RSS + FRED CSV

## Quick start
1) Create venv and install deps:
   - `python -m venv .venv`
   - `source .venv/bin/activate`
   - `python -m pip install -r requirements.txt`

2) Initialize local DB:
   - `python scripts/init_db.py`

3) Ingest data:
   - `python scripts/ingest_truth_rss.py`
   - `python scripts/ingest_fred.py`
   - `python scripts/fetch_link_previews.py --limit 30`

4) Print a local snapshot:
   - `python scripts/preview.py`

5) Launch the local dashboard:
   - `python scripts/server.py`
   - Open `http://127.0.0.1:5000` in your browser.

## Config
- `MVP_DB_PATH`: override the SQLite path (default: `data/mvp.sqlite3`).

## Notes
- Statements are sourced from `https://trumpstruth.org/feed` and stored with provenance fields.
- Indicators are sourced from FRED CSV endpoints and stored as daily series.
- No inference is performed. Timeline and indicators are presented side by side.
