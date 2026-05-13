"""
Market Structure analyzer — BOS and ChoCh detection.

BOS (Break of Structure): price breaks a prior swing in the direction of the
    existing trend. Confirms trend continuation.

ChoCh (Change of Character): first break of structure AGAINST the current trend.
    Signals a potential reversal — the key trigger in Fede's OBM entry model.

Flow:
  Uptrend  → price breaks above a swing high → BOS bullish
  Uptrend  → price breaks below a swing low  → ChoCh (possible reversal)
  Downtrend → price breaks below a swing low  → BOS bearish
  Downtrend → price breaks above a swing high → ChoCh (possible reversal)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd


class StructureType(Enum):
    BOS_BULLISH = "bos_bullish"
    BOS_BEARISH = "bos_bearish"
    CHOCH_BULLISH = "choch_bullish"   # ChoCh against downtrend → potential reversal up
    CHOCH_BEARISH = "choch_bearish"   # ChoCh against uptrend  → potential reversal down


class Trend(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class StructureEvent:
    type: StructureType
    timestamp: pd.Timestamp
    broken_level: float     # the swing level that was broken
    close_price: float      # close that confirmed the break
    timeframe: str

    @property
    def is_choch(self) -> bool:
        return self.type in (StructureType.CHOCH_BULLISH, StructureType.CHOCH_BEARISH)

    @property
    def is_bos(self) -> bool:
        return self.type in (StructureType.BOS_BULLISH, StructureType.BOS_BEARISH)

    def __repr__(self) -> str:
        return f"Structure({self.type.value} | broke {self.broken_level:.2f} @ {self.timestamp})"


def detect_structure(
    df: pd.DataFrame,
    timeframe: str = "5m",
    swing_lookback: int = 5,
) -> list[StructureEvent]:
    """
    Detect all BOS and ChoCh events in the DataFrame.

    Args:
        df: OHLCV DataFrame
        timeframe: label for reference
        swing_lookback: bars each side to confirm a swing point
    """
    events: list[StructureEvent] = []
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df.index

    # Build swing high/low arrays
    swing_highs: list[tuple[int, float]] = []  # (index, price)
    swing_lows: list[tuple[int, float]] = []

    for i in range(swing_lookback, len(df) - swing_lookback):
        if highs[i] == max(highs[i - swing_lookback: i + swing_lookback + 1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - swing_lookback: i + swing_lookback + 1]):
            swing_lows.append((i, lows[i]))

    # Determine initial trend from first few swings
    current_trend = _initial_trend(swing_highs, swing_lows)

    sh_ptr = 0  # pointer into swing_highs
    sl_ptr = 0  # pointer into swing_lows

    for i in range(swing_lookback * 2, len(df)):
        close = closes[i]
        ts = timestamps[i]

        # Advance swing pointers to only consider swings formed before bar i
        while sh_ptr < len(swing_highs) and swing_highs[sh_ptr][0] >= i:
            sh_ptr += 1
        while sl_ptr < len(swing_lows) and swing_lows[sl_ptr][0] >= i:
            sl_ptr += 1

        if sh_ptr == 0 or sl_ptr == 0:
            continue

        last_sh_idx, last_sh_price = swing_highs[sh_ptr - 1]
        last_sl_idx, last_sl_price = swing_lows[sl_ptr - 1]

        if current_trend == Trend.BULLISH:
            # BOS: close above last swing high → continuation
            if close > last_sh_price:
                events.append(StructureEvent(
                    type=StructureType.BOS_BULLISH,
                    timestamp=ts,
                    broken_level=last_sh_price,
                    close_price=close,
                    timeframe=timeframe,
                ))
            # ChoCh: close below last swing low → potential reversal
            elif close < last_sl_price:
                events.append(StructureEvent(
                    type=StructureType.CHOCH_BEARISH,
                    timestamp=ts,
                    broken_level=last_sl_price,
                    close_price=close,
                    timeframe=timeframe,
                ))
                current_trend = Trend.BEARISH

        elif current_trend == Trend.BEARISH:
            # BOS: close below last swing low → continuation
            if close < last_sl_price:
                events.append(StructureEvent(
                    type=StructureType.BOS_BEARISH,
                    timestamp=ts,
                    broken_level=last_sl_price,
                    close_price=close,
                    timeframe=timeframe,
                ))
            # ChoCh: close above last swing high → potential reversal
            elif close > last_sh_price:
                events.append(StructureEvent(
                    type=StructureType.CHOCH_BULLISH,
                    timestamp=ts,
                    broken_level=last_sh_price,
                    close_price=close,
                    timeframe=timeframe,
                ))
                current_trend = Trend.BULLISH

    return events


def latest_choch(
    events: list[StructureEvent],
    before: pd.Timestamp,
    max_bars_ago: int = 5,
    bar_interval_minutes: int = 1,
) -> Optional[StructureEvent]:
    """Return the most recent ChoCh event before a given timestamp."""
    cutoff = before - pd.Timedelta(minutes=bar_interval_minutes * max_bars_ago)
    recent = [
        e for e in events
        if e.is_choch and cutoff <= e.timestamp < before
    ]
    return recent[-1] if recent else None


def detect_local_choch(
    df: pd.DataFrame,
    timeframe: str = "1m",
    window: int = 15,
    swing_lookback: int = 2,
) -> list[StructureEvent]:
    """
    Detect ChoCh within a rolling local window — designed for micro-timeframe
    entry confirmation after a liquidity sweep.

    This is what Fede actually looks for: after a sweep at 9:30, price makes a
    short-term reversal breaking above/below the most recent local swing within
    the last `window` bars. This is NOT a global trend change — it's a local
    structural break that signals the sweep has failed and price is reversing.

    Generates many more signals than detect_structure() which tracks global trend.
    """
    from trader.smc.structure import find_swing_lows_arr, find_swing_highs_arr

    events: list[StructureEvent] = []
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index

    for i in range(window + swing_lookback, len(df)):
        sub_highs = highs[max(0, i - window): i]
        sub_lows = lows[max(0, i - window): i]
        sub_ts = timestamps[max(0, i - window): i]

        close = closes[i]
        ts = timestamps[i]

        # Local swing high = highest high in the window that has lower highs on both sides
        local_sh = _local_swing_high(sub_highs, swing_lookback)
        local_sl = _local_swing_low(sub_lows, swing_lookback)

        # Bullish ChoCh: close breaks above local swing high
        if local_sh is not None and close > local_sh:
            events.append(StructureEvent(
                type=StructureType.CHOCH_BULLISH,
                timestamp=ts,
                broken_level=local_sh,
                close_price=close,
                timeframe=timeframe,
            ))

        # Bearish ChoCh: close breaks below local swing low
        if local_sl is not None and close < local_sl:
            events.append(StructureEvent(
                type=StructureType.CHOCH_BEARISH,
                timestamp=ts,
                broken_level=local_sl,
                close_price=close,
                timeframe=timeframe,
            ))

    return events


def _local_swing_high(highs: "np.ndarray", lookback: int) -> Optional[float]:
    """Return the most recent confirmed swing high in the array."""
    import numpy as np
    if len(highs) < lookback * 2 + 1:
        return None
    for i in range(len(highs) - lookback - 1, lookback - 1, -1):
        window = highs[max(0, i - lookback): i + lookback + 1]
        if highs[i] == window.max():
            return float(highs[i])
    return None


def _local_swing_low(lows: "np.ndarray", lookback: int) -> Optional[float]:
    """Return the most recent confirmed swing low in the array."""
    import numpy as np
    if len(lows) < lookback * 2 + 1:
        return None
    for i in range(len(lows) - lookback - 1, lookback - 1, -1):
        window = lows[max(0, i - lookback): i + lookback + 1]
        if lows[i] == window.min():
            return float(lows[i])
    return None


def current_trend(events: list[StructureEvent]) -> Trend:
    """Infer current trend from the last structure event."""
    if not events:
        return Trend.NEUTRAL
    last = events[-1]
    if last.type in (StructureType.BOS_BULLISH, StructureType.CHOCH_BULLISH):
        return Trend.BULLISH
    if last.type in (StructureType.BOS_BEARISH, StructureType.CHOCH_BEARISH):
        return Trend.BEARISH
    return Trend.NEUTRAL


def _initial_trend(
    swing_highs: list[tuple[int, float]],
    swing_lows: list[tuple[int, float]],
) -> Trend:
    if len(swing_highs) >= 2:
        if swing_highs[-1][1] > swing_highs[-2][1]:
            return Trend.BULLISH
        return Trend.BEARISH
    return Trend.NEUTRAL
