from __future__ import annotations

from datetime import timezone

import pandas as pd
import yfinance as yf

from db import connect, now_iso, iso_utc
from series import INTRADAY_SERIES, intraday_indicator_id


def ensure_indicators(ingested_at: str) -> None:
    with connect() as conn:
        for series in INTRADAY_SERIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO indicators (id, name, series_id, frequency, source, unit, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intraday_indicator_id(series.series_id),
                    series.name,
                    series.series_id,
                    "intraday",
                    "yfinance",
                    series.unit,
                    ingested_at,
                ),
            )


def upsert_points(conn, series_id: str, source: str, points: list[tuple[str, float]], ingested_at: str) -> int:
    # Update existing bars: the newest bar is still in progress when fetched, so a later
    # fetch carries its final close.
    conn.executemany(
        """
        INSERT INTO intraday_points (series_id, ts_utc, value, source, ingested_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (series_id, ts_utc) DO UPDATE SET
            value = excluded.value,
            source = excluded.source,
            ingested_at = excluded.ingested_at
        WHERE intraday_points.value IS NOT excluded.value
        """,
        [(series_id, ts, value, source, ingested_at) for ts, value in points],
    )
    return len(points)


def fetch_intraday(tickers: list[str]) -> dict[str, list[tuple[str, float]]]:
    """Fetch 1h bars for the last 5 days for all tickers in one batched yfinance call."""
    df = yf.download(
        tickers,
        period="5d",
        interval="1h",
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
    )
    result: dict[str, list[tuple[str, float]]] = {ticker: [] for ticker in tickers}
    if df.empty:
        return result

    for ticker in tickers:
        if ticker not in df.columns.get_level_values(0):
            continue
        # The batch shares one time index across exchanges (24/7 crypto vs. stock hours),
        # so each ticker has NaN rows outside its own trading hours.
        closes = df[ticker]["Close"].dropna()
        for ts, close in closes.items():
            close = float(close)
            if close == 0:
                continue
            utc_dt = pd.Timestamp(ts).tz_convert(timezone.utc).to_pydatetime()
            result[ticker].append((iso_utc(utc_dt), close))
    return result


def main() -> None:
    ingested_at = now_iso()
    ensure_indicators(ingested_at)

    points_by_ticker = fetch_intraday([series.yahoo_ticker for series in INTRADAY_SERIES])

    with connect() as conn:
        for series in INTRADAY_SERIES:
            points = points_by_ticker.get(series.yahoo_ticker, [])
            upsert_points(conn, series.series_id, "yfinance", points, ingested_at)
            status = f"{len(points)} points" if points else "failed: no data returned"
            print(f"{series.series_id} <- {series.yahoo_ticker}: {status}")

    print("Intraday ingestion complete.")


if __name__ == "__main__":
    main()
