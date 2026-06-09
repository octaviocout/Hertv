"""
SMT (Smart Money Tool) Divergence Engine — NQ vs ES.

Core concept: NQ and ES are highly correlated. When one instrument makes a
LOWER LOW while the other makes a HIGHER LOW (or vice versa), it signals
institutional manipulation and a likely price reversal.

Structural divergence (correct ICT definition):
  Bullish SMT: ES makes lower low AND NQ makes higher low (or vice versa)
               → manipulation to the downside, expect reversal upward
  Bearish SMT: ES makes higher high AND NQ makes lower high (or vice versa)
               → manipulation to the upside, expect reversal downward

Previous implementation compared raw price percentages which was too strict
for 1m bars. This version uses swing structure comparison instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd


class SMTType(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class SMTSignal:
    type: SMTType
    timestamp: pd.Timestamp
    nq_price: float
    es_price: float
    swept_instrument: str
    held_instrument: str
    swept_level: float
    held_level: float

    def __repr__(self) -> str:
        return (
            f"SMT({self.type.value} | {self.swept_instrument} swept {self.swept_level:.2f}, "
            f"{self.held_instrument} held {self.held_level:.2f} @ {self.timestamp})"
        )


def find_swing_lows(lows: pd.Series, lookback: int = 3) -> pd.Series:
    mask = pd.Series(False, index=lows.index)
    arr = lows.values
    for i in range(lookback, len(arr) - lookback):
        if arr[i] <= min(arr[i - lookback: i]) and arr[i] <= min(arr[i + 1: i + lookback + 1]):
            mask.iloc[i] = True
    return mask


def find_swing_highs(highs: pd.Series, lookback: int = 3) -> pd.Series:
    mask = pd.Series(False, index=highs.index)
    arr = highs.values
    for i in range(lookback, len(arr) - lookback):
        if arr[i] >= max(arr[i - lookback: i]) and arr[i] >= max(arr[i + 1: i + lookback + 1]):
            mask.iloc[i] = True
    return mask


def detect_smt_divergence(
    nq_df: pd.DataFrame,
    es_df: pd.DataFrame,
    swing_lookback: int = 3,
    tolerance_pct: float = 0.002,   # kept for API compatibility, not used in detection
) -> list[SMTSignal]:
    """
    Detect SMT divergence by comparing CONSECUTIVE swing lows/highs.

    Algorithm:
      1. Find all confirmed swing lows on NQ and ES independently
      2. Pair consecutive swings on each instrument
      3. Bullish SMT: one instrument makes a LOWER LOW while the other makes
         a HIGHER LOW (or flat) within a time proximity window
      4. Bearish SMT: same logic at highs
    """
    signals: list[SMTSignal] = []

    if len(nq_df) < swing_lookback * 4:
        return signals

    nq_sl_mask = find_swing_lows(nq_df["low"], swing_lookback)
    es_sl_mask = find_swing_lows(es_df["low"], swing_lookback)
    nq_sh_mask = find_swing_highs(nq_df["high"], swing_lookback)
    es_sh_mask = find_swing_highs(es_df["high"], swing_lookback)

    nq_sl = _swing_list(nq_df["low"], nq_sl_mask)
    es_sl = _swing_list(es_df["low"], es_sl_mask)
    nq_sh = _swing_list(nq_df["high"], nq_sh_mask)
    es_sh = _swing_list(es_df["high"], es_sh_mask)

    # Max time window to consider two swings as "concurrent"
    proximity = pd.Timedelta(minutes=swing_lookback * 4)

    # --- Bullish SMT at lows ---
    signals += _find_divergence_at_lows(nq_sl, es_sl, proximity)

    # --- Bearish SMT at highs ---
    signals += _find_divergence_at_highs(nq_sh, es_sh, proximity)

    # Deduplicate and sort
    seen: set = set()
    unique: list[SMTSignal] = []
    for s in signals:
        key = (s.timestamp, s.type, s.swept_instrument)
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return sorted(unique, key=lambda s: s.timestamp)


def latest_smt(
    signals: list[SMTSignal],
    before: pd.Timestamp,
    max_bars_ago: int = 30,
    bar_interval_minutes: int = 1,
) -> Optional[SMTSignal]:
    cutoff = before - pd.Timedelta(minutes=bar_interval_minutes * max_bars_ago)
    recent = [s for s in signals if cutoff <= s.timestamp < before]
    return recent[-1] if recent else None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _swing_list(series: pd.Series, mask: pd.Series) -> list[tuple[pd.Timestamp, float]]:
    """Return list of (timestamp, price) for confirmed swings."""
    return [(ts, series[ts]) for ts in series.index[mask]]


def _find_divergence_at_lows(
    nq_swings: list[tuple[pd.Timestamp, float]],
    es_swings: list[tuple[pd.Timestamp, float]],
    proximity: pd.Timedelta,
) -> list[SMTSignal]:
    """
    Bullish SMT: compare consecutive swing low PAIRS.
    One instrument makes lower low, the other makes higher low.
    """
    signals: list[SMTSignal] = []

    if len(nq_swings) < 2 or len(es_swings) < 2:
        return signals

    # For each consecutive NQ swing low pair
    for i in range(1, len(nq_swings)):
        nq_ts2, nq_low2 = nq_swings[i]
        nq_ts1, nq_low1 = nq_swings[i - 1]

        # Find ES swing lows concurrent with NQ's second swing (within proximity)
        es_near = [(ts, p) for ts, p in es_swings if abs(ts - nq_ts2) <= proximity]
        if not es_near:
            continue

        es_ts2, es_low2 = min(es_near, key=lambda x: abs(x[0] - nq_ts2))

        # Find the ES swing low before es_ts2
        es_prior = [(ts, p) for ts, p in es_swings if ts < es_ts2]
        if not es_prior:
            continue
        es_ts1, es_low1 = es_prior[-1]

        signal_ts = max(nq_ts2, es_ts2)

        # NQ lower low, ES higher low → NQ swept, ES held
        if nq_low2 < nq_low1 and es_low2 > es_low1:
            signals.append(SMTSignal(
                type=SMTType.BULLISH,
                timestamp=signal_ts,
                nq_price=nq_low2,
                es_price=es_low2,
                swept_instrument="NQ",
                held_instrument="ES",
                swept_level=nq_low1,
                held_level=es_low1,
            ))

        # ES lower low, NQ higher low → ES swept, NQ held
        elif es_low2 < es_low1 and nq_low2 > nq_low1:
            signals.append(SMTSignal(
                type=SMTType.BULLISH,
                timestamp=signal_ts,
                nq_price=nq_low2,
                es_price=es_low2,
                swept_instrument="ES",
                held_instrument="NQ",
                swept_level=es_low1,
                held_level=nq_low1,
            ))

    return signals


def _find_divergence_at_highs(
    nq_swings: list[tuple[pd.Timestamp, float]],
    es_swings: list[tuple[pd.Timestamp, float]],
    proximity: pd.Timedelta,
) -> list[SMTSignal]:
    """Bearish SMT: one instrument makes higher high, the other makes lower high."""
    signals: list[SMTSignal] = []

    if len(nq_swings) < 2 or len(es_swings) < 2:
        return signals

    for i in range(1, len(nq_swings)):
        nq_ts2, nq_high2 = nq_swings[i]
        nq_ts1, nq_high1 = nq_swings[i - 1]

        es_near = [(ts, p) for ts, p in es_swings if abs(ts - nq_ts2) <= proximity]
        if not es_near:
            continue

        es_ts2, es_high2 = min(es_near, key=lambda x: abs(x[0] - nq_ts2))

        es_prior = [(ts, p) for ts, p in es_swings if ts < es_ts2]
        if not es_prior:
            continue
        es_ts1, es_high1 = es_prior[-1]

        signal_ts = max(nq_ts2, es_ts2)

        # NQ higher high, ES lower high → NQ swept, ES held
        if nq_high2 > nq_high1 and es_high2 < es_high1:
            signals.append(SMTSignal(
                type=SMTType.BEARISH,
                timestamp=signal_ts,
                nq_price=nq_high2,
                es_price=es_high2,
                swept_instrument="NQ",
                held_instrument="ES",
                swept_level=nq_high1,
                held_level=es_high1,
            ))

        # ES higher high, NQ lower high → ES swept, NQ held
        elif es_high2 > es_high1 and nq_high2 < nq_high1:
            signals.append(SMTSignal(
                type=SMTType.BEARISH,
                timestamp=signal_ts,
                nq_price=nq_high2,
                es_price=es_high2,
                swept_instrument="ES",
                held_instrument="NQ",
                swept_level=es_high1,
                held_level=nq_high1,
            ))

    return signals
