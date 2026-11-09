from __future__ import annotations

import csv
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from db import connect, now_iso

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FETCH_WORKERS = 6

SERIES = [
    {
        "id": "fred:VIXCLS",
        "name": "VIX",
        "series_id": "VIXCLS",
        "frequency": "daily",
        "unit": "index",
    },
    {
        "id": "fred:SP500",
        "name": "S&P 500",
        "series_id": "SP500",
        "frequency": "daily",
        "unit": "index",
    },
    {
        "id": "fred:NASDAQCOM",
        "name": "Nasdaq Composite",
        "series_id": "NASDAQCOM",
        "frequency": "daily",
        "unit": "index",
    },
    {
        "id": "fred:DJIA",
        "name": "Dow Jones Industrial Average",
        "series_id": "DJIA",
        "frequency": "daily",
        "unit": "index",
    },
    {
        "id": "fred:DGS2",
        "name": "US 2Y Treasury",
        "series_id": "DGS2",
        "frequency": "daily",
        "unit": "percent",
    },
    {
        "id": "fred:DGS10",
        "name": "US 10Y Treasury",
        "series_id": "DGS10",
        "frequency": "daily",
        "unit": "percent",
    },
    {
        "id": "fred:DTWEXBGS",
        "name": "Broad Dollar Index",
        "series_id": "DTWEXBGS",
        "frequency": "daily",
        "unit": "index",
    },
    {
        "id": "fred:DEXUSEU",
        "name": "USD to Euro",
        "series_id": "DEXUSEU",
        "frequency": "daily",
        "unit": "usd_per_eur",
    },
    {
        "id": "fred:DEXJPUS",
        "name": "Japanese Yen to USD",
        "series_id": "DEXJPUS",
        "frequency": "daily",
        "unit": "yen_per_usd",
    },
    {
        "id": "fred:DCOILWTICO",
        "name": "WTI Crude Oil",
        "series_id": "DCOILWTICO",
        "frequency": "daily",
        "unit": "usd_per_barrel",
    },
    {
        "id": "fred:DCOILBRENTEU",
        "name": "Brent Crude Oil",
        "series_id": "DCOILBRENTEU",
        "frequency": "daily",
        "unit": "usd_per_barrel",
    },
    {
        "id": "fred:DHHNGSP",
        "name": "Natural Gas (Henry Hub)",
        "series_id": "DHHNGSP",
        "frequency": "daily",
        "unit": "usd_per_mmbtu",
    },
    {
        "id": "fred:GASREGCOVW",
        "name": "Gasoline (US Regular Retail)",
        "series_id": "GASREGCOVW",
        "frequency": "weekly",
        "unit": "usd_per_gallon",
    },
    {
        "id": "fred:PCOPPUSDM",
        "name": "Copper",
        "series_id": "PCOPPUSDM",
        "frequency": "daily",
        "unit": "usd_per_metric_ton",
    },
]



def fetch_csv(series_id: str, since: str | None = None) -> list[tuple[str, str]]:
    url = FRED_URL.format(series_id=series_id)
    if since:
        # cosd = observation start date (inclusive); avoids re-downloading decades of history.
        url += f"&cosd={since}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            last_err = None
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            # Simple backoff to ride out transient 5xx/timeouts.
            time.sleep(1.5 * (attempt + 1))

    if last_err is not None:
        raise last_err

    rows = []
    reader = csv.reader(resp.text.splitlines())
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        rows.append((row[0], row[1]))
    return rows


def get_max_date(conn, indicator_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS max_date FROM indicator_points WHERE indicator_id = ?",
        (indicator_id,),
    ).fetchone()
    if row and row["max_date"]:
        return row["max_date"]
    return None


def fetch_series(series: dict, max_date: str | None) -> tuple[list[tuple[str, str]] | None, Exception | None]:
    try:
        return fetch_csv(series["series_id"], since=max_date), None
    except Exception as err:  # noqa: BLE001
        return None, err


def main() -> None:
    ingest_time = now_iso()

    with connect() as conn:
        for series in SERIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO indicators (
                    id, name, series_id, frequency, source, unit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    series["id"],
                    series["name"],
                    series["series_id"],
                    series["frequency"],
                    "FRED",
                    series["unit"],
                    ingest_time,
                ),
            )
        conn.commit()
        max_dates = {series["id"]: get_max_date(conn, series["id"]) for series in SERIES}

    # Download all series concurrently; the requests are latency-bound, not bandwidth-bound.
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(lambda s: fetch_series(s, max_dates[s["id"]]), SERIES))

    with connect() as conn:
        for series, (rows, err) in zip(SERIES, results):
            if err is not None:
                print(f"{series['name']} ({series['series_id']}): fetch failed: {err}")
                continue
            max_date = max_dates[series["id"]]
            points = []
            for date_str, value_str in rows:
                if value_str in (".", ""):
                    continue
                if max_date and date_str <= max_date:
                    continue
                points.append((series["id"], date_str, float(value_str), ingest_time))

            conn.executemany(
                """
                INSERT OR IGNORE INTO indicator_points (
                    indicator_id, date, value, ingested_at
                ) VALUES (?, ?, ?, ?)
                """,
                points,
            )
            print(
                f"{series['name']} ({series['series_id']}): inserted {len(points)} rows"
            )
        conn.commit()

    print("FRED ingestion complete.")


if __name__ == "__main__":
    main()
