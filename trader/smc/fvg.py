"""
Fair Value Gap (FVG) and Inverted FVG (IFVG) detector.

Bullish FVG: 3-candle pattern where candle[i-2].high < candle[i].low
             Gap zone = [candle[i-2].high, candle[i].low]

Bearish FVG: 3-candle pattern where candle[i-2].low > candle[i].high
             Gap zone = [candle[i].high, candle[i-2].low]

IFVG: An FVG that price has fully closed through — it now acts as opposing S/R.

Noise filter (Fede's discretion translated to code):
  No fixed tick minimum was given in the video — he filters by "feel".
  We approximate this with an ATR-based filter: an FVG is only accepted if
  its gap size >= ATR(14) * atr_multiplier. This removes micro-gaps on
  1m/30s bars that are just normal tick noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import numpy as np
import pandas as pd


class FVGType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class FVG:
    type: FVGType
    top: float        # upper boundary of the gap
    bottom: float     # lower boundary of the gap
    formed_at: pd.Timestamp
    timeframe: str
    filled: bool = False
    inverted: bool = False   # True once it becomes an IFVG
    fill_at: Optional[pd.Timestamp] = None

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains_price(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def __repr__(self) -> str:
        kind = "IFVG" if self.inverted else "FVG"
        return f"{kind}({self.type.value} | {self.bottom:.2f}-{self.top:.2f} @ {self.formed_at})"


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing (standard)."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def detect_fvgs(
    df: pd.DataFrame,
    timeframe: str = "5m",
    min_size: float = 0.0,
    atr_multiplier: float = 0.25,
    atr_period: int = 14,
) -> list[FVG]:
    """
    Scan a OHLCV DataFrame and return all FVGs found.

    Args:
        df: DataFrame with columns open/high/low/close
        timeframe: label stored on each FVG for reference
        min_size: hard minimum gap size in price points (fallback if ATR unavailable)
        atr_multiplier: FVG gap must be >= ATR * this value to pass noise filter.
                        0.25 works well for 1m NQ bars (~2-3 points on average).
                        Increase to 0.5 for fewer but higher-quality setups.
        atr_period: ATR calculation period
    """
    fvgs: list[FVG] = []

    if len(df) < atr_period + 2:
        return fvgs

    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index
    atr_values = compute_atr(df, atr_period).values

    for i in range(2, len(df)):
        atr = atr_values[i]
        dynamic_min = max(min_size, atr * atr_multiplier) if not np.isnan(atr) else min_size

        # Bullish FVG
        if highs[i - 2] < lows[i]:
            size = lows[i] - highs[i - 2]
            if size >= dynamic_min:
                fvgs.append(FVG(
                    type=FVGType.BULLISH,
                    top=lows[i],
                    bottom=highs[i - 2],
                    formed_at=timestamps[i],
                    timeframe=timeframe,
                ))

        # Bearish FVG
        if lows[i - 2] > highs[i]:
            size = lows[i - 2] - highs[i]
            if size >= dynamic_min:
                fvgs.append(FVG(
                    type=FVGType.BEARISH,
                    top=lows[i - 2],
                    bottom=highs[i],
                    formed_at=timestamps[i],
                    timeframe=timeframe,
                ))

    return fvgs


def update_fvg_status(fvgs: list[FVG], df: pd.DataFrame) -> list[FVG]:
    """
    Update fill/inversion status for existing FVGs against new price data.
    Call this on each new bar to maintain live FVG state.

    Fill logic:
    - Bullish FVG is filled when close drops below its bottom
    - Bearish FVG is filled when close rises above its top
    - A filled FVG becomes an IFVG (inverted — flips role)
    """
    for bar_ts, row in df.iterrows():
        for fvg in fvgs:
            if fvg.filled:
                continue
            if bar_ts <= fvg.formed_at:
                continue

            if fvg.type == FVGType.BULLISH and row["close"] < fvg.bottom:
                fvg.filled = True
                fvg.inverted = True
                fvg.fill_at = bar_ts

            elif fvg.type == FVGType.BEARISH and row["close"] > fvg.top:
                fvg.filled = True
                fvg.inverted = True
                fvg.fill_at = bar_ts

    return fvgs


def active_fvgs(fvgs: list[FVG], invert_only: bool = False) -> list[FVG]:
    """Return FVGs that have not been filled, or only IFVGs."""
    if invert_only:
        return [f for f in fvgs if f.inverted]
    return [f for f in fvgs if not f.filled]


def nearest_fvg(price: float, fvgs: list[FVG], direction: FVGType) -> Optional[FVG]:
    """
    Find the nearest unfilled FVG above (bearish) or below (bullish) current price.
    Used to find the closest retest target.
    """
    candidates = [f for f in fvgs if f.type == direction and not f.filled]
    if not candidates:
        return None
    if direction == FVGType.BULLISH:
        # nearest bullish FVG below price
        below = [f for f in candidates if f.top < price]
        return max(below, key=lambda f: f.top) if below else None
    else:
        # nearest bearish FVG above price
        above = [f for f in candidates if f.bottom > price]
        return min(above, key=lambda f: f.bottom) if above else None
