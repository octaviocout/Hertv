"""
Data feed module — fetches OHLCV for NQ and ES futures.
Uses yfinance for backtesting; swap get_live_bar() for a broker adapter in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import yfinance as yf


# Continuous futures tickers on Yahoo Finance
NQ_TICKER = "NQ=F"
ES_TICKER = "ES=F"

VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "1h", "4h", "1d"}


@dataclass
class BarData:
    ticker: str
    interval: str
    df: pd.DataFrame  # columns: open, high, low, close, volume (lowercase)


def fetch_bars(
    ticker: str,
    interval: str,
    period: str = "5d",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BarData:
    """
    Download OHLCV bars from Yahoo Finance.

    For intraday intervals (1m, 5m, etc.) Yahoo limits history to ~7-60 days.
    For live/broker use, replace this function with your broker's streaming API.
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Interval '{interval}' not supported. Use one of {VALID_INTERVALS}")

    kwargs: dict = {"ticker": ticker, "interval": interval, "auto_adjust": True}
    if start and end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period

    raw = yf.download(
        tickers=ticker,
        interval=interval,
        period=period if not (start and end) else None,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )

    if raw.empty:
        raise RuntimeError(f"No data returned for {ticker} @ {interval}")

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)

    return BarData(ticker=ticker, interval=interval, df=df)


def fetch_nq_es(
    interval: str,
    period: str = "5d",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> tuple[BarData, BarData]:
    """Convenience wrapper — fetches both NQ and ES at the same interval."""
    nq = fetch_bars(NQ_TICKER, interval, period, start, end)
    es = fetch_bars(ES_TICKER, interval, period, start, end)
    return nq, es


def align_bars(nq: BarData, es: BarData) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Align NQ and ES dataframes to the same timestamps.
    Required for SMT divergence comparison.
    """
    nq_df = nq.df.copy()
    es_df = es.df.copy()
    common_idx = nq_df.index.intersection(es_df.index)
    return nq_df.loc[common_idx], es_df.loc[common_idx]


def session_bars(df: pd.DataFrame, session_open: str = "09:30", session_close: str = "16:00") -> pd.DataFrame:
    """Filter bars to regular trading session (NYSE hours, US/Eastern)."""
    df_et = df.copy()
    df_et.index = df_et.index.tz_convert("America/New_York")
    mask = (df_et.index.time >= pd.Timestamp(session_open).time()) & \
           (df_et.index.time <= pd.Timestamp(session_close).time())
    return df_et.loc[mask]
