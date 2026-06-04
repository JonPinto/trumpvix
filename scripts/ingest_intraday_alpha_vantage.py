from __future__ import annotations

import time

import yfinance as yf

from db import connect, now_iso, iso_utc


def upsert_points(series_id: str, source: str, points: list[tuple[str, float]], ingested_at: str) -> int:
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO intraday_points (series_id, ts_utc, value, source, ingested_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(series_id, ts, value, source, ingested_at) for ts, value in points],
        )
        conn.commit()
    return len(points)


def fetch_intraday(ticker: str) -> list[tuple[str, float]]:
    """Fetch 1h intraday data for the last 5 trading days."""
    df = yf.download(ticker, period="5d", interval="1h", progress=False, auto_adjust=True)
    if df.empty:
        return []
    # yfinance returns multi-level columns like ("Close", "SPY"). Flatten.
    df.columns = df.columns.get_level_values(0)
    points: list[tuple[str, float]] = []
    for ts, row in df.iterrows():
        close = float(row["Close"])
        if close == 0:
            continue
        utc_dt = ts.to_pydatetime().astimezone(timezone.utc)
        points.append((iso_utc(utc_dt), close))
    return points


def main() -> None:
    ingested_at = now_iso()

    # Equities/ETFs — Yahoo tickers for our proxy mapping.
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

    # FX — Yahoo uses "CUR1CUR2=X" format.
    fx_map = {
        "DEXUSEU": "EURUSD=X",
        "DEXJPUS": "JPY=X",
    }

    for series_id, ticker in equity_map.items():
        try:
            points = fetch_intraday(ticker)
            upsert_points(series_id, "yfinance", points, ingested_at)
            print(f"{series_id} <- {ticker}: {len(points)} points")
        except Exception as err:
            print(f"{series_id} <- {ticker}: failed: {err}")
        time.sleep(1)

    for series_id, ticker in fx_map.items():
        try:
            points = fetch_intraday(ticker)
            upsert_points(series_id, "yfinance", points, ingested_at)
            print(f"{series_id} <- {ticker}: {len(points)} points")
        except Exception as err:
            print(f"{series_id} <- {ticker}: failed: {err}")
        time.sleep(1)

    print("Intraday ingestion complete.")


if __name__ == "__main__":
    main()
