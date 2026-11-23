"""Single source of truth for the market series the app ingests and displays."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Series:
    series_id: str  # FRED series id; also the key for intraday points
    name: str
    unit: str
    fred_frequency: str | None  # None: not fetched from FRED
    yahoo_ticker: str | None  # None: no intraday data. Several tickers are ETF/ETN proxies.
    in_reactions: bool  # shown in the per-statement market reaction panel


# Order here is the order of the reaction panel.
SERIES: list[Series] = [
    Series("SP500", "S&P 500", "index", "daily", "SPY", True),
    Series("NASDAQCOM", "Nasdaq Composite", "index", "daily", "QQQ", True),
    Series("DJIA", "Dow Jones Industrial Average", "index", "daily", "DIA", True),
    Series("DTWEXBGS", "Broad Dollar Index", "index", "daily", "UUP", True),
    Series("DEXUSEU", "USD to Euro", "usd_per_eur", "daily", "EURUSD=X", True),
    Series("DEXJPUS", "Japanese Yen to USD", "yen_per_usd", "daily", "JPY=X", True),
    Series("DCOILWTICO", "WTI Crude Oil", "usd_per_barrel", "daily", "USO", True),
    Series("DCOILBRENTEU", "Brent Crude Oil", "usd_per_barrel", "daily", "BNO", True),
    Series("DHHNGSP", "Natural Gas (Henry Hub)", "usd_per_mmbtu", "daily", "UNG", True),
    Series("GASREGCOVW", "Gasoline (US Regular Retail)", "usd_per_gallon", "weekly", "UGA", True),
    Series("BTCUSD", "Bitcoin", "usd", None, "BTC-USD", True),
    Series("ETHUSD", "Ethereum", "usd", None, "ETH-USD", True),
    # FRED's daily VIXCLS is authoritative but published a day late, so the VIX
    # index itself (^VIX) is ingested intraday to keep the chart's newest value current.
    Series("VIXCLS", "VIX", "index", "daily", "^VIX", False),
    Series("DGS2", "US 2Y Treasury", "percent", "daily", None, False),
    Series("DGS10", "US 10Y Treasury", "percent", "daily", None, False),
    Series("PCOPPUSDM", "Copper", "usd_per_metric_ton", "monthly", None, False),
]

FRED_SERIES = [s for s in SERIES if s.fred_frequency]
INTRADAY_SERIES = [s for s in SERIES if s.yahoo_ticker]
REACTION_SERIES = [s.series_id for s in SERIES if s.in_reactions]


def fred_indicator_id(series_id: str) -> str:
    return f"fred:{series_id}"


def intraday_indicator_id(series_id: str) -> str:
    return f"intraday:{series_id}"
