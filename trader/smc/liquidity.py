"""
Liquidity module — Equal Highs/Lows (EQH/EQL) and Draw on Liquidity (DOL).

Fede's pre-session routine (before 9:30 AM ET):
  "Marcamos máximos, marcamos mínimos y luego esperamos a la apertura."

  1. Scan 15m/5m bars from the overnight/pre-session for swing pivots.
  2. Identify EQH/EQL — two or more swings at the same price level.
     These are the PRIMARY liquidity targets because stop-losses cluster there.
  3. The DOL for a given trade is the nearest unmitigated EQH (for longs)
     or EQL (for shorts) above/below the entry.

EQH (Equal Highs): two+ swing highs within `tolerance` of each other.
EQL (Equal Lows):  two+ swing lows within `tolerance` of each other.

Mitigation: a liquidity level is "swept" (mitigated) once price closes
beyond it, taking the stops that were resting there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional
import pandas as pd


@dataclass
class LiquidityLevel:
    price: float
    level_type: str          # "EQH", "EQL", "swing_high", "swing_low"
    formed_at: pd.Timestamp
    timeframe: str
    touch_count: int = 1     # how many times price has returned to this level
    swept: bool = False
    swept_at: Optional[pd.Timestamp] = None

    def __repr__(self) -> str:
        status = "swept" if self.swept else "active"
        return f"Liq({self.level_type} @ {self.price:.2f} [{status}] touches={self.touch_count})"


@dataclass
class PreSessionLevels:
    """Holds all key levels marked before market open."""
    date: pd.Timestamp
    highs: list[LiquidityLevel] = field(default_factory=list)
    lows: list[LiquidityLevel] = field(default_factory=list)

    @property
    def all_levels(self) -> list[LiquidityLevel]:
        return self.highs + self.lows

    def active_highs(self) -> list[LiquidityLevel]:
        return [l for l in self.highs if not l.swept]

    def active_lows(self) -> list[LiquidityLevel]:
        return [l for l in self.lows if not l.swept]


# ---------------------------------------------------------------------------
# Pre-session level builder
# ---------------------------------------------------------------------------

def build_presession_levels(
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    session_date: pd.Timestamp,
    lookback_days: int = 2,
    swing_lookback: int = 3,
    eq_tolerance_pct: float = 0.0015,
) -> PreSessionLevels:
    """
    Mark swing highs/lows from the 15m and 5m charts before today's open.
    Then identify EQH/EQL clusters among those swings.

    Args:
        df_15m: 15-minute OHLCV (Eastern timezone index)
        df_5m:  5-minute OHLCV (Eastern timezone index)
        session_date: the trading date (used to filter pre-session bars)
        lookback_days: how many prior days of 15m bars to scan
        swing_lookback: pivot confirmation bars each side
        eq_tolerance_pct: two highs/lows are "equal" if within this % of each other
    """
    result = PreSessionLevels(date=session_date)
    open_time = pd.Timestamp(session_date.date()).tz_localize("America/New_York").replace(hour=9, minute=30)

    # Filter: only bars before today's open, within lookback window
    cutoff_start = open_time - pd.Timedelta(days=lookback_days)

    def pre_session(df: pd.DataFrame) -> pd.DataFrame:
        idx = df.index.tz_convert("America/New_York")
        mask = (idx >= cutoff_start) & (idx < open_time)
        return df[mask]

    bars_15m = pre_session(df_15m)
    bars_5m = pre_session(df_5m)

    swings_15m = _find_swings(bars_15m, "15m", swing_lookback)
    swings_5m = _find_swings(bars_5m, "5m", swing_lookback)
    all_swings = swings_15m + swings_5m

    highs = [s for s in all_swings if s.level_type == "swing_high"]
    lows = [s for s in all_swings if s.level_type == "swing_low"]

    # Cluster into EQH/EQL
    result.highs = _cluster_levels(highs, "EQH", eq_tolerance_pct)
    result.lows = _cluster_levels(lows, "EQL", eq_tolerance_pct)

    return result


def mark_swept_levels(levels: PreSessionLevels, df: pd.DataFrame) -> None:
    """
    Update mitigation state: a level is swept when a bar closes beyond it.
    Call on each new 1m/5m bar during the session.
    """
    for bar_ts, row in df.iterrows():
        for level in levels.highs:
            if not level.swept and row["close"] > level.price:
                level.swept = True
                level.swept_at = bar_ts

        for level in levels.lows:
            if not level.swept and row["close"] < level.price:
                level.swept = True
                level.swept_at = bar_ts


# ---------------------------------------------------------------------------
# DOL selection
# ---------------------------------------------------------------------------

def get_dol_target(
    direction: str,          # "long" or "short"
    entry_price: float,
    levels: PreSessionLevels,
    fallback_pct: float = 0.008,
) -> float:
    """
    Select the Draw on Liquidity target for a trade.

    Logic (mirrors Fede's routine):
      Long  → nearest unswept EQH or swing_high ABOVE entry
      Short → nearest unswept EQL or swing_low BELOW entry

    Priority: EQH/EQL first (higher probability sweep), then plain swing H/L.
    Falls back to a % move if no levels exist.
    """
    if direction == "long":
        candidates = [
            l for l in levels.active_highs()
            if l.price > entry_price
        ]
        if not candidates:
            return entry_price * (1 + fallback_pct)
        # Prefer EQH over plain swing_high; among ties, nearest price
        eqh = [l for l in candidates if l.level_type == "EQH"]
        pool = eqh if eqh else candidates
        return min(pool, key=lambda l: l.price).price

    else:  # short
        candidates = [
            l for l in levels.active_lows()
            if l.price < entry_price
        ]
        if not candidates:
            return entry_price * (1 - fallback_pct)
        eql = [l for l in candidates if l.level_type == "EQL"]
        pool = eql if eql else candidates
        return max(pool, key=lambda l: l.price).price


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_swings(df: pd.DataFrame, tf: str, lookback: int) -> list[LiquidityLevel]:
    levels: list[LiquidityLevel] = []
    if len(df) < lookback * 2 + 1:
        return levels

    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index

    for i in range(lookback, len(df) - lookback):
        window_h = highs[i - lookback: i + lookback + 1]
        window_l = lows[i - lookback: i + lookback + 1]

        if highs[i] == max(window_h):
            levels.append(LiquidityLevel(
                price=highs[i],
                level_type="swing_high",
                formed_at=timestamps[i],
                timeframe=tf,
            ))
        if lows[i] == min(window_l):
            levels.append(LiquidityLevel(
                price=lows[i],
                level_type="swing_low",
                formed_at=timestamps[i],
                timeframe=tf,
            ))

    return levels


def _cluster_levels(
    levels: list[LiquidityLevel],
    cluster_type: str,
    tolerance_pct: float,
) -> list[LiquidityLevel]:
    """
    Group nearby levels into EQH/EQL clusters.
    Each cluster is represented by one level whose touch_count reflects
    how many individual swings are within the tolerance band.
    """
    if not levels:
        return []

    sorted_levels = sorted(levels, key=lambda l: l.price)
    clusters: list[LiquidityLevel] = []
    used = [False] * len(sorted_levels)

    for i, base in enumerate(sorted_levels):
        if used[i]:
            continue
        group = [base]
        for j in range(i + 1, len(sorted_levels)):
            if used[j]:
                continue
            if abs(sorted_levels[j].price - base.price) / base.price <= tolerance_pct:
                group.append(sorted_levels[j])
                used[j] = True
        used[i] = True

        avg_price = sum(l.price for l in group) / len(group)
        latest_ts = max(l.formed_at for l in group)
        tfs = "/".join(sorted({l.timeframe for l in group}))

        rep = LiquidityLevel(
            price=round(avg_price, 2),
            level_type=cluster_type if len(group) > 1 else group[0].level_type,
            formed_at=latest_ts,
            timeframe=tfs,
            touch_count=len(group),
        )
        clusters.append(rep)

    return clusters
