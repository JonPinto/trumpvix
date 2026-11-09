from __future__ import annotations

from datetime import timezone

import pandas as pd
import yfinance as yf

from db import connect, now_iso, iso_utc

INDICATOR_META = {
    "SP500": ("S&P 500", "index"),
    "NASDAQCOM": ("Nasdaq Composite", "index"),
    "DJIA": ("Dow Jones Industrial Average", "index"),
    "DTWEXBGS": ("Broad Dollar Index", "index"),
    "DCOILWTICO": ("WTI Crude Oil", "usd_per_barrel"),
    "DCOILBRENTEU": ("Brent Crude Oil", "usd_per_barrel"),
    "DHHNGSP": ("Natural Gas (Henry Hub)", "usd_per_mmbtu"),
    "GASREGCOVW": ("Gasoline (US Regular Retail)", "usd_per_gallon"),
    "VIXCLS": ("VIX", "index"),
    "BTCUSD": ("Bitcoin", "usd"),
    "ETHUSD": ("Ethereum", "usd"),
    "DEXUSEU": ("USD to Euro", "usd_per_eur"),
    "DEXJPUS": ("Japanese Yen to USD", "yen_per_usd"),
}


def ensure_indicators(series_ids: list[str], ingested_at: str) -> None:
    with connect() as conn:
        for sid in series_ids:
            name, unit = INDICATOR_META.get(sid, (sid, "index"))
            conn.execute(
                """
                INSERT OR IGNORE INTO indicators (id, name, series_id, frequency, source, unit, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (f"intraday:{sid}", name, sid, "intraday", "yfinance", unit, ingested_at),
            )
        conn.commit()


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

    equity_map = {
        "SP500": "SPY",
        "NASDAQCOM": "QQQ",
        "DJIA": "DIA",
        "DTWEXBGS": "UUP",
        "DCOILWTICO": "USO",
        "DCOILBRENTEU": "BNO",
        "DHHNGSP": "UNG",
        "GASREGCOVW": "UGA",
        "VIXCLS": "VXX",
        "BTCUSD": "BTC-USD",
        "ETHUSD": "ETH-USD",
    }

    fx_map = {
        "DEXUSEU": "EURUSD=X",
        "DEXJPUS": "JPY=X",
    }

    all_series = list(equity_map.keys()) + list(fx_map.keys())
    ensure_indicators(all_series, ingested_at)

    ticker_by_series = {**equity_map, **fx_map}
    points_by_ticker = fetch_intraday(list(ticker_by_series.values()))

    with connect() as conn:
        for series_id, ticker in ticker_by_series.items():
            points = points_by_ticker.get(ticker, [])
            upsert_points(conn, series_id, "yfinance", points, ingested_at)
            status = f"{len(points)} points" if points else "failed: no data returned"
            print(f"{series_id} <- {ticker}: {status}")
        conn.commit()

    print("Intraday ingestion complete.")


if __name__ == "__main__":
    main()
