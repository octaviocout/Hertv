"""
Fair Value Gap (FVG) and Inverted FVG (IFVG) detector.

Bullish FVG: 3-candle pattern where candle[i-2].high < candle[i].low
             Gap zone = [candle[i-2].high, candle[i].low]

Bearish FVG: 3-candle pattern where candle[i-2].low > candle[i].high
             Gap zone = [candle[i].high, candle[i-2].low]

IFVG: An FVG that price has fully closed through — it now acts as opposing S/R.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
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


def detect_fvgs(df: pd.DataFrame, timeframe: str = "5m", min_size: float = 0.0) -> list[FVG]:
    """
    Scan a OHLCV DataFrame and return all FVGs found.

    Args:
        df: DataFrame with columns open/high/low/close
        timeframe: label stored on each FVG for reference
        min_size: minimum gap size in price units to filter noise
    """
    fvgs: list[FVG] = []
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index

    for i in range(2, len(df)):
        # Bullish FVG
        if highs[i - 2] < lows[i]:
            size = lows[i] - highs[i - 2]
            if size >= min_size:
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
            if size >= min_size:
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
