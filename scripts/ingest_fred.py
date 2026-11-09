from __future__ import annotations

import csv
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from db import connect, now_iso
from series import FRED_SERIES, Series, fred_indicator_id

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FETCH_WORKERS = 6


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


def fetch_series(series: Series, max_date: str | None) -> tuple[list[tuple[str, str]] | None, Exception | None]:
    try:
        return fetch_csv(series.series_id, since=max_date), None
    except Exception as err:  # noqa: BLE001
        return None, err


def main() -> None:
    ingest_time = now_iso()

    with connect() as conn:
        for series in FRED_SERIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO indicators (
                    id, name, series_id, frequency, source, unit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fred_indicator_id(series.series_id),
                    series.name,
                    series.series_id,
                    series.fred_frequency,
                    "FRED",
                    series.unit,
                    ingest_time,
                ),
            )
        max_dates = {s.series_id: get_max_date(conn, fred_indicator_id(s.series_id)) for s in FRED_SERIES}

    # Download all series concurrently; the requests are latency-bound, not bandwidth-bound.
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(lambda s: fetch_series(s, max_dates[s.series_id]), FRED_SERIES))

    with connect() as conn:
        for series, (rows, err) in zip(FRED_SERIES, results):
            label = f"{series.name} ({series.series_id})"
            if err is not None:
                print(f"{label}: fetch failed: {err}")
                continue
            indicator_id = fred_indicator_id(series.series_id)
            max_date = max_dates[series.series_id]
            points = []
            for date_str, value_str in rows:
                if value_str in (".", ""):
                    continue
                if max_date and date_str <= max_date:
                    continue
                points.append((indicator_id, date_str, float(value_str), ingest_time))

            conn.executemany(
                """
                INSERT OR IGNORE INTO indicator_points (
                    indicator_id, date, value, ingested_at
                ) VALUES (?, ?, ?, ?)
                """,
                points,
            )
            print(f"{label}: inserted {len(points)} rows")

    print("FRED ingestion complete.")


if __name__ == "__main__":
    main()
