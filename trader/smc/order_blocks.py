"""
Order Block (OB) detector — SMC/ICT methodology.

Bullish OB: the last bearish candle before a strong bullish impulse that breaks structure.
Bearish OB: the last bullish candle before a strong bearish impulse that breaks structure.

The OB zone acts as a demand/supply area where institutional orders were placed.
Price retracing into the OB is a high-probability entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd


class OBType(Enum):
    BULLISH = "bullish"   # demand zone
    BEARISH = "bearish"   # supply zone


@dataclass
class OrderBlock:
    type: OBType
    top: float
    bottom: float
    formed_at: pd.Timestamp
    timeframe: str
    broken: bool = False       # True if price closes through the OB (invalidated)
    tested: bool = False       # True if price has entered the OB zone at least once
    impulse_size: float = 0.0  # size of the impulse that created this OB

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    def contains_price(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def __repr__(self) -> str:
        return f"OB({self.type.value} | {self.bottom:.2f}-{self.top:.2f} @ {self.formed_at})"


def detect_order_blocks(
    df: pd.DataFrame,
    timeframe: str = "5m",
    swing_lookback: int = 3,
    min_impulse_candles: int = 2,
) -> list[OrderBlock]:
    """
    Detect Order Blocks by finding the last opposing candle before a strong impulse
    that creates a Break of Structure (BOS).

    Args:
        df: OHLCV DataFrame
        timeframe: label for reference
        swing_lookback: candles to look back when identifying swing points
        min_impulse_candles: minimum consecutive candles in the same direction
                             to qualify as an impulse
    """
    obs: list[OrderBlock] = []
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df.index

    for i in range(swing_lookback + min_impulse_candles, len(df) - 1):
        # --- Bullish OB: last bearish candle before bullish impulse ---
        # Identify a bullish impulse starting at i
        if _is_bullish_impulse(closes, highs, i, min_impulse_candles):
            # Find the last bearish candle before the impulse
            ob_idx = _last_bearish_candle(opens, closes, i)
            if ob_idx is not None:
                # Validate: the impulse must break above a recent swing high
                swing_high = max(highs[max(0, ob_idx - swing_lookback): ob_idx + 1])
                impulse_top = max(highs[i: i + min_impulse_candles + 1])
                if impulse_top > swing_high:
                    impulse_size = impulse_top - lows[ob_idx]
                    obs.append(OrderBlock(
                        type=OBType.BULLISH,
                        top=max(opens[ob_idx], closes[ob_idx]),
                        bottom=min(opens[ob_idx], closes[ob_idx]),
                        formed_at=timestamps[ob_idx],
                        timeframe=timeframe,
                        impulse_size=impulse_size,
                    ))

        # --- Bearish OB: last bullish candle before bearish impulse ---
        if _is_bearish_impulse(closes, lows, i, min_impulse_candles):
            ob_idx = _last_bullish_candle(opens, closes, i)
            if ob_idx is not None:
                swing_low = min(lows[max(0, ob_idx - swing_lookback): ob_idx + 1])
                impulse_bottom = min(lows[i: i + min_impulse_candles + 1])
                if impulse_bottom < swing_low:
                    impulse_size = highs[ob_idx] - impulse_bottom
                    obs.append(OrderBlock(
                        type=OBType.BEARISH,
                        top=max(opens[ob_idx], closes[ob_idx]),
                        bottom=min(opens[ob_idx], closes[ob_idx]),
                        formed_at=timestamps[ob_idx],
                        timeframe=timeframe,
                        impulse_size=impulse_size,
                    ))

    return obs


def update_ob_status(obs: list[OrderBlock], df: pd.DataFrame) -> list[OrderBlock]:
    """
    Invalidate OBs where price has closed through them,
    and mark as tested when price enters the zone.
    """
    for bar_ts, row in df.iterrows():
        for ob in obs:
            if ob.broken:
                continue
            if bar_ts <= ob.formed_at:
                continue

            if ob.contains_price(row["close"]):
                ob.tested = True

            # Invalidated when close is fully beyond the OB
            if ob.type == OBType.BULLISH and row["close"] < ob.bottom:
                ob.broken = True
            elif ob.type == OBType.BEARISH and row["close"] > ob.top:
                ob.broken = True

    return obs


def active_obs(obs: list[OrderBlock]) -> list[OrderBlock]:
    return [ob for ob in obs if not ob.broken]


def nearest_ob(price: float, obs: list[OrderBlock], direction: OBType) -> Optional[OrderBlock]:
    """Find the nearest valid OB in the given direction relative to price."""
    candidates = [ob for ob in obs if ob.type == direction and not ob.broken]
    if not candidates:
        return None
    if direction == OBType.BULLISH:
        below = [ob for ob in candidates if ob.top < price]
        return max(below, key=lambda ob: ob.top) if below else None
    else:
        above = [ob for ob in candidates if ob.bottom > price]
        return min(above, key=lambda ob: ob.bottom) if above else None


# --- helpers ---

def _is_bullish_impulse(closes, highs, start_idx: int, n: int) -> bool:
    if start_idx + n >= len(closes):
        return False
    return all(closes[start_idx + k] > closes[start_idx + k - 1] for k in range(1, n + 1))


def _is_bearish_impulse(closes, lows, start_idx: int, n: int) -> bool:
    if start_idx + n >= len(closes):
        return False
    return all(closes[start_idx + k] < closes[start_idx + k - 1] for k in range(1, n + 1))


def _last_bearish_candle(opens, closes, before_idx: int) -> Optional[int]:
    for i in range(before_idx - 1, max(0, before_idx - 10), -1):
        if closes[i] < opens[i]:
            return i
    return None


def _last_bullish_candle(opens, closes, before_idx: int) -> Optional[int]:
    for i in range(before_idx - 1, max(0, before_idx - 10), -1):
        if closes[i] > opens[i]:
            return i
    return None
