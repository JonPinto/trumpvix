#!/bin/bash
set -e

echo "=== TrumpVIX Deploy Script ==="

# Initialize database
echo "Initializing database..."
python scripts/init_db.py

# Ingest RSS feed
echo "Ingesting RSS feed..."
python scripts/ingest_truth_rss.py || echo "RSS ingestion failed (non-fatal)"

# Ingest intraday data via yfinance
echo "Ingesting intraday market data..."
python scripts/ingest_intraday_yfinance.py || echo "Intraday ingestion failed (non-fatal)"

# Ingest FRED daily indicators
echo "Ingesting FRED indicators..."
python scripts/ingest_fred.py || echo "FRED ingestion failed (non-fatal)"

# Fetch link previews
echo "Fetching link previews..."
python scripts/fetch_link_previews.py --limit 30 || echo "Link preview fetch failed (non-fatal)"

echo "=== Deploy complete ==="
