"""
SMT (Smart Money Tool) Divergence Engine — NQ vs ES.

Core concept: NQ and ES are highly correlated. When one instrument breaks a
swing high/low and the other doesn't, it signals institutional manipulation
and a likely price reversal (liquidity sweep without confirmation).

Bullish SMT: ES breaks a recent swing LOW but NQ does NOT (or vice versa)
             → manipulation to the downside, expect reversal upward.

Bearish SMT: ES breaks a recent swing HIGH but NQ does NOT (or vice versa)
             → manipulation to the upside, expect reversal downward.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd


class SMTType(Enum):
    BULLISH = "bullish"   # divergence at a low — expect upward reversal
    BEARISH = "bearish"   # divergence at a high — expect downward reversal


@dataclass
class SMTSignal:
    type: SMTType
    timestamp: pd.Timestamp
    nq_price: float
    es_price: float
    # which instrument swept and which held
    swept_instrument: str    # "NQ" or "ES"
    held_instrument: str     # "NQ" or "ES"
    swept_level: float       # the swing level that was broken
    held_level: float        # the swing level that held

    def __repr__(self) -> str:
        return (
            f"SMT({self.type.value} | {self.swept_instrument} swept {self.swept_level:.2f}, "
            f"{self.held_instrument} held {self.held_level:.2f} @ {self.timestamp})"
        )


def find_swing_lows(lows: pd.Series, lookback: int = 5) -> pd.Series:
    """Return boolean mask where True = confirmed swing low."""
    mask = pd.Series(False, index=lows.index)
    arr = lows.values
    for i in range(lookback, len(arr) - lookback):
        if arr[i] == min(arr[i - lookback: i + lookback + 1]):
            mask.iloc[i] = True
    return mask


def find_swing_highs(highs: pd.Series, lookback: int = 5) -> pd.Series:
    """Return boolean mask where True = confirmed swing high."""
    mask = pd.Series(False, index=highs.index)
    arr = highs.values
    for i in range(lookback, len(arr) - lookback):
        if arr[i] == max(arr[i - lookback: i + lookback + 1]):
            mask.iloc[i] = True
    return mask


def detect_smt_divergence(
    nq_df: pd.DataFrame,
    es_df: pd.DataFrame,
    swing_lookback: int = 5,
    tolerance_pct: float = 0.002,
) -> list[SMTSignal]:
    """
    Scan aligned NQ and ES DataFrames for SMT divergences.

    Args:
        nq_df: OHLCV DataFrame for NQ (aligned timestamps)
        es_df: OHLCV DataFrame for ES (aligned timestamps)
        swing_lookback: bars each side to confirm a swing point
        tolerance_pct: how close prices must be to a prior swing to count
                       as "sweeping" it (0.002 = 0.2%)

    Returns:
        List of SMTSignal instances sorted by timestamp.
    """
    signals: list[SMTSignal] = []

    nq_swing_lows = find_swing_lows(nq_df["low"], swing_lookback)
    es_swing_lows = find_swing_lows(es_df["low"], swing_lookback)
    nq_swing_highs = find_swing_highs(nq_df["high"], swing_lookback)
    es_swing_highs = find_swing_highs(es_df["high"], swing_lookback)

    timestamps = nq_df.index

    for i in range(swing_lookback * 2, len(timestamps)):
        ts = timestamps[i]

        nq_low = nq_df["low"].iloc[i]
        es_low = es_df["low"].iloc[i]
        nq_high = nq_df["high"].iloc[i]
        es_high = es_df["high"].iloc[i]

        # Prior swing lows in the lookback window
        window = slice(max(0, i - swing_lookback * 3), i)
        prior_nq_lows = nq_df["low"].iloc[window][nq_swing_lows.iloc[window]]
        prior_es_lows = es_df["low"].iloc[window][es_swing_lows.iloc[window]]
        prior_nq_highs = nq_df["high"].iloc[window][nq_swing_highs.iloc[window]]
        prior_es_highs = es_df["high"].iloc[window][es_swing_highs.iloc[window]]

        # --- Bullish SMT at lows ---
        if len(prior_nq_lows) > 0 and len(prior_es_lows) > 0:
            ref_nq_low = prior_nq_lows.min()
            ref_es_low = prior_es_lows.min()

            es_swept_low = es_low < ref_es_low * (1 - tolerance_pct)
            nq_held_low = nq_low > ref_nq_low * (1 - tolerance_pct)
            if es_swept_low and nq_held_low:
                signals.append(SMTSignal(
                    type=SMTType.BULLISH,
                    timestamp=ts,
                    nq_price=nq_low,
                    es_price=es_low,
                    swept_instrument="ES",
                    held_instrument="NQ",
                    swept_level=ref_es_low,
                    held_level=ref_nq_low,
                ))

            nq_swept_low = nq_low < ref_nq_low * (1 - tolerance_pct)
            es_held_low = es_low > ref_es_low * (1 - tolerance_pct)
            if nq_swept_low and es_held_low:
                signals.append(SMTSignal(
                    type=SMTType.BULLISH,
                    timestamp=ts,
                    nq_price=nq_low,
                    es_price=es_low,
                    swept_instrument="NQ",
                    held_instrument="ES",
                    swept_level=ref_nq_low,
                    held_level=ref_es_low,
                ))

        # --- Bearish SMT at highs ---
        if len(prior_nq_highs) > 0 and len(prior_es_highs) > 0:
            ref_nq_high = prior_nq_highs.max()
            ref_es_high = prior_es_highs.max()

            es_swept_high = es_high > ref_es_high * (1 + tolerance_pct)
            nq_held_high = nq_high < ref_nq_high * (1 + tolerance_pct)
            if es_swept_high and nq_held_high:
                signals.append(SMTSignal(
                    type=SMTType.BEARISH,
                    timestamp=ts,
                    nq_price=nq_high,
                    es_price=es_high,
                    swept_instrument="ES",
                    held_instrument="NQ",
                    swept_level=ref_es_high,
                    held_level=ref_nq_high,
                ))

            nq_swept_high = nq_high > ref_nq_high * (1 + tolerance_pct)
            es_held_high = es_high < ref_es_high * (1 + tolerance_pct)
            if nq_swept_high and es_held_high:
                signals.append(SMTSignal(
                    type=SMTType.BEARISH,
                    timestamp=ts,
                    nq_price=nq_high,
                    es_price=es_high,
                    swept_instrument="NQ",
                    held_instrument="ES",
                    swept_level=ref_nq_high,
                    held_level=ref_es_high,
                ))

    # Deduplicate signals on the same timestamp
    seen: set[tuple] = set()
    unique: list[SMTSignal] = []
    for s in signals:
        key = (s.timestamp, s.type, s.swept_instrument)
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return sorted(unique, key=lambda s: s.timestamp)


def latest_smt(signals: list[SMTSignal], before: pd.Timestamp, max_bars_ago: int = 10,
               bar_interval_minutes: int = 1) -> Optional[SMTSignal]:
    """
    Return the most recent SMT signal before a given timestamp,
    within max_bars_ago bars.
    """
    cutoff = before - pd.Timedelta(minutes=bar_interval_minutes * max_bars_ago)
    recent = [s for s in signals if cutoff <= s.timestamp < before]
    return recent[-1] if recent else None
