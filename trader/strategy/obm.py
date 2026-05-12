"""
OBM — Opening Bell Model strategy engine.
Replicates Fede Esses' intraday NQ/ES futures strategy.

Entry flow:
  1. Pre-session (09:00–09:29): map key levels (swing highs/lows, equal H/L)
  2. Opening (09:30–09:45): wait for a liquidity sweep of a pre-session level
  3. Confirm SMT divergence between NQ and ES at the sweep
  4. Wait for ChoCh + FVG formation on 1m/30s after the sweep
  5. Enter on retest of that FVG/OB
  6. Scale-in (+0.3%) when initial SL is at break-even and a new IFVG forms

Exit flow:
  - SL: behind the OB or the swept level (technical)
  - Move SL to BE once price runs 1:1 or on scale-in
  - TP: Draw on Liquidity — next swing high/low, equal highs/lows
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from typing import Optional
import pandas as pd

from trader.smc.fvg import FVG, FVGType, detect_fvgs, update_fvg_status, nearest_fvg
from trader.smc.order_blocks import OrderBlock, OBType, detect_order_blocks, update_ob_status, nearest_ob
from trader.smc.smt import SMTSignal, SMTType, detect_smt_divergence, latest_smt
from trader.smc.structure import StructureEvent, StructureType, detect_structure, latest_choch

logger = logging.getLogger(__name__)


# NYSE session times (Eastern)
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
OBM_WINDOW_END = time(9, 45)    # stop looking for new OBM entries after this


class TradeDirection(Enum):
    LONG = "long"
    SHORT = "short"


class TradeState(Enum):
    WAITING = "waiting"             # no active setup
    SETUP_FOUND = "setup_found"     # liquidity sweep + SMT confirmed
    IN_TRADE = "in_trade"           # position open
    SCALED_IN = "scaled_in"         # added to position
    CLOSED = "closed"               # trade finished


@dataclass
class Trade:
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: pd.Timestamp
    size: float                     # contracts / units
    state: TradeState = TradeState.IN_TRADE

    # scale-in fields
    scale_entry: Optional[float] = None
    scale_size: float = 0.0
    scale_sl: Optional[float] = None

    # tracking
    be_activated: bool = False
    pnl: float = 0.0
    exit_price: Optional[float] = None
    exit_time: Optional[pd.Timestamp] = None
    exit_reason: str = ""

    @property
    def total_size(self) -> float:
        return self.size + self.scale_size

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    def __repr__(self) -> str:
        return (
            f"Trade({self.direction.value} | entry={self.entry_price:.2f} "
            f"SL={self.stop_loss:.2f} TP={self.take_profit:.2f} "
            f"size={self.total_size:.2f} state={self.state.value})"
        )


@dataclass
class OBMSetup:
    """Intermediate state — setup confirmed but entry not yet triggered."""
    direction: TradeDirection
    smt_signal: SMTSignal
    choch_event: StructureEvent
    entry_fvg: Optional[FVG]
    entry_ob: Optional[OrderBlock]
    sweep_level: float
    dol_target: float               # Draw on Liquidity target
    formed_at: pd.Timestamp
    valid: bool = True


class OBMStrategy:
    """
    Stateful OBM engine. Call update() on each new bar.
    Maintains one active trade at a time (Fede's quality-over-quantity rule).
    """

    def __init__(
        self,
        initial_risk_pct: float = 0.005,    # 0.5% of account per trade
        scale_risk_pct: float = 0.003,      # 0.3% added on scale-in
        daily_loss_limit: float = 0.02,     # 2% max daily loss → halt trading
        swing_lookback: int = 5,
        smt_tolerance: float = 0.002,
        min_fvg_size: float = 0.0,
    ):
        self.initial_risk_pct = initial_risk_pct
        self.scale_risk_pct = scale_risk_pct
        self.daily_loss_limit = daily_loss_limit
        self.swing_lookback = swing_lookback
        self.smt_tolerance = smt_tolerance
        self.min_fvg_size = min_fvg_size

        self.state = TradeState.WAITING
        self.active_trade: Optional[Trade] = None
        self.active_setup: Optional[OBMSetup] = None
        self.trade_history: list[Trade] = []

        # daily tracking
        self._daily_pnl: float = 0.0
        self._daily_halted: bool = False
        self._current_date: Optional[pd.Timestamp] = None

        # SMC state built from history
        self._nq_fvgs: list[FVG] = []
        self._es_fvgs: list[FVG] = []
        self._smt_signals: list[SMTSignal] = []
        self._structure_events: list[StructureEvent] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(
        self,
        nq_hist: pd.DataFrame,
        es_hist: pd.DataFrame,
        execution_tf: str = "1m",
    ) -> None:
        """
        Pre-populate SMC state from historical data before live trading starts.
        Call once with a few days of 1m bars.
        """
        nq_aligned, es_aligned = _align(nq_hist, es_hist)
        self._nq_fvgs = detect_fvgs(nq_aligned, execution_tf, self.min_fvg_size)
        self._smt_signals = detect_smt_divergence(
            nq_aligned, es_aligned, self.swing_lookback, self.smt_tolerance
        )
        self._structure_events = detect_structure(nq_aligned, execution_tf, self.swing_lookback)
        update_fvg_status(self._nq_fvgs, nq_aligned)
        logger.info(
            "Initialized: %d FVGs, %d SMT signals, %d structure events",
            len(self._nq_fvgs), len(self._smt_signals), len(self._structure_events),
        )

    def update(
        self,
        ts: pd.Timestamp,
        nq_bar: pd.Series,
        es_bar: pd.Series,
        account_value: float,
    ) -> Optional[dict]:
        """
        Process a single new bar (1m or 30s). Returns an order dict if an
        action is required, or None.

        Order dict schema:
            {
                "action": "open_long" | "open_short" | "close" | "scale_in" |
                          "move_sl" | "daily_halt",
                "price":  float,
                "size":   float,
                "sl":     float,
                "tp":     float,
                "reason": str,
            }
        """
        self._reset_daily_if_needed(ts)

        if self._daily_halted:
            return {"action": "daily_halt", "reason": "daily loss limit reached"}

        bar_time = ts.tz_convert("America/New_York").time()

        # Update FVG/structure state with the new bar
        bar_df = _bar_to_df(ts, nq_bar)
        update_fvg_status(self._nq_fvgs, bar_df)

        # --- Manage open trade ---
        if self.active_trade and self.active_trade.state == TradeState.IN_TRADE:
            order = self._manage_trade(ts, nq_bar, account_value)
            if order:
                return order

        # --- Look for new setups only during OBM window ---
        if self.active_trade is None and SESSION_OPEN <= bar_time <= OBM_WINDOW_END:
            order = self._scan_for_setup(ts, nq_bar, es_bar, account_value)
            if order:
                return order

        # --- Check pending setup for entry trigger ---
        if self.active_setup and self.active_setup.valid and self.active_trade is None:
            order = self._check_entry_trigger(ts, nq_bar, account_value)
            if order:
                return order

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_for_setup(
        self,
        ts: pd.Timestamp,
        nq_bar: pd.Series,
        es_bar: pd.Series,
        account_value: float,
    ) -> Optional[dict]:
        """Check for liquidity sweep + SMT confirmation → create a setup."""
        # Update SMT signals with latest bar
        nq_df = _bar_to_df(ts, nq_bar)
        es_df = _bar_to_df(ts, es_bar)
        new_smt = detect_smt_divergence(nq_df, es_df, self.swing_lookback, self.smt_tolerance)
        self._smt_signals.extend(new_smt)

        smt = latest_smt(self._smt_signals, before=ts, max_bars_ago=5, bar_interval_minutes=1)
        if smt is None:
            return None

        # Detect ChoCh after the SMT sweep
        new_structure = detect_structure(nq_df, "1m", self.swing_lookback)
        self._structure_events.extend(new_structure)
        choch = latest_choch(self._structure_events, before=ts, max_bars_ago=5, bar_interval_minutes=1)
        if choch is None:
            return None

        direction = (
            TradeDirection.LONG if smt.type == SMTType.BULLISH
            else TradeDirection.SHORT
        )

        # Confirm ChoCh direction matches SMT direction
        if direction == TradeDirection.LONG and choch.type != StructureType.CHOCH_BULLISH:
            return None
        if direction == TradeDirection.SHORT and choch.type != StructureType.CHOCH_BEARISH:
            return None

        # Find FVG created by the ChoCh impulse
        fvg_type = FVGType.BULLISH if direction == TradeDirection.LONG else FVGType.BEARISH
        new_fvgs = detect_fvgs(nq_df, "1m", self.min_fvg_size)
        self._nq_fvgs.extend(new_fvgs)
        entry_fvg = nearest_fvg(nq_bar["close"], self._nq_fvgs, fvg_type)

        # Draw on Liquidity: most recent opposing swing
        dol = self._estimate_dol(direction, nq_bar["close"])

        self.active_setup = OBMSetup(
            direction=direction,
            smt_signal=smt,
            choch_event=choch,
            entry_fvg=entry_fvg,
            entry_ob=None,
            sweep_level=smt.swept_level,
            dol_target=dol,
            formed_at=ts,
        )
        logger.info("Setup found: %s at %s", direction.value, ts)
        return None  # wait for entry trigger

    def _check_entry_trigger(
        self,
        ts: pd.Timestamp,
        nq_bar: pd.Series,
        account_value: float,
    ) -> Optional[dict]:
        """Enter when price retraces into the setup FVG/OB."""
        setup = self.active_setup
        if setup is None or not setup.valid:
            return None

        # Invalidate setup if it's too old (> 15 bars)
        if ts > setup.formed_at + pd.Timedelta(minutes=15):
            setup.valid = False
            self.active_setup = None
            return None

        price = nq_bar["close"]
        fvg = setup.entry_fvg

        triggered = False
        if fvg and fvg.contains_price(price):
            triggered = True

        if not triggered:
            return None

        # Calculate SL and size
        if setup.direction == TradeDirection.LONG:
            sl = setup.sweep_level - (setup.sweep_level * 0.001)  # just below sweep
            tp = setup.dol_target
        else:
            sl = setup.sweep_level + (setup.sweep_level * 0.001)
            tp = setup.dol_target

        risk_per_unit = abs(price - sl)
        if risk_per_unit == 0:
            return None

        size = (account_value * self.initial_risk_pct) / risk_per_unit

        trade = Trade(
            direction=setup.direction,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            entry_time=ts,
            size=size,
            state=TradeState.IN_TRADE,
        )
        self.active_trade = trade
        self.active_setup = None
        self.state = TradeState.IN_TRADE

        action = "open_long" if setup.direction == TradeDirection.LONG else "open_short"
        logger.info("ENTRY: %s @ %.2f SL=%.2f TP=%.2f size=%.4f", action, price, sl, tp, size)
        return {"action": action, "price": price, "size": size, "sl": sl, "tp": tp,
                "reason": "FVG retest after ChoCh+SMT"}

    def _manage_trade(
        self,
        ts: pd.Timestamp,
        nq_bar: pd.Series,
        account_value: float,
    ) -> Optional[dict]:
        trade = self.active_trade
        price = nq_bar["close"]
        high = nq_bar["high"]
        low = nq_bar["low"]

        # --- Check TP hit ---
        if trade.direction == TradeDirection.LONG and high >= trade.take_profit:
            return self._close_trade(ts, trade.take_profit, "TP hit")
        if trade.direction == TradeDirection.SHORT and low <= trade.take_profit:
            return self._close_trade(ts, trade.take_profit, "TP hit")

        # --- Check SL hit ---
        if trade.direction == TradeDirection.LONG and low <= trade.stop_loss:
            return self._close_trade(ts, trade.stop_loss, "SL hit")
        if trade.direction == TradeDirection.SHORT and high >= trade.stop_loss:
            return self._close_trade(ts, trade.stop_loss, "SL hit")

        # --- Move SL to Break-Even ---
        if not trade.be_activated:
            be_trigger_distance = abs(trade.take_profit - trade.entry_price) * 0.5
            if trade.direction == TradeDirection.LONG:
                if price >= trade.entry_price + be_trigger_distance:
                    trade.stop_loss = trade.entry_price
                    trade.be_activated = True
                    logger.info("SL moved to BE @ %.2f", trade.entry_price)
                    return {"action": "move_sl", "price": trade.stop_loss, "reason": "break-even"}
            else:
                if price <= trade.entry_price - be_trigger_distance:
                    trade.stop_loss = trade.entry_price
                    trade.be_activated = True
                    logger.info("SL moved to BE @ %.2f", trade.entry_price)
                    return {"action": "move_sl", "price": trade.stop_loss, "reason": "break-even"}

        # --- Scale-in: only when BE is active and no scale done yet ---
        if trade.be_activated and trade.scale_size == 0:
            scale_order = self._check_scale_in(ts, nq_bar, account_value, trade)
            if scale_order:
                return scale_order

        return None

    def _check_scale_in(
        self,
        ts: pd.Timestamp,
        nq_bar: pd.Series,
        account_value: float,
        trade: Trade,
    ) -> Optional[dict]:
        """
        Look for a new IFVG or FVG in the trade direction for scale-in entry.
        Scale-in only when the original SL is already at break-even.
        """
        fvg_type = FVGType.BULLISH if trade.direction == TradeDirection.LONG else FVGType.BEARISH
        price = nq_bar["close"]

        # Check for active IFVGs in trade direction for scale-in
        ifvg = nearest_fvg(price, [f for f in self._nq_fvgs if f.inverted], fvg_type)
        entry_fvg = nearest_fvg(price, self._nq_fvgs, fvg_type)
        trigger = ifvg or entry_fvg

        if trigger is None or not trigger.contains_price(price):
            return None

        # SL for scale: below/above the IFVG
        if trade.direction == TradeDirection.LONG:
            scale_sl = trigger.bottom * 0.9995
        else:
            scale_sl = trigger.top * 1.0005

        risk_per_unit = abs(price - scale_sl)
        if risk_per_unit == 0:
            return None

        scale_size = (account_value * self.scale_risk_pct) / risk_per_unit
        trade.scale_entry = price
        trade.scale_size = scale_size
        trade.scale_sl = scale_sl
        trade.state = TradeState.SCALED_IN

        logger.info("SCALE-IN: %.4f contracts @ %.2f SL=%.2f", scale_size, price, scale_sl)
        action = "open_long" if trade.direction == TradeDirection.LONG else "open_short"
        return {"action": action, "price": price, "size": scale_size, "sl": scale_sl,
                "tp": trade.take_profit, "reason": "scale-in on IFVG (BE active)"}

    def _close_trade(self, ts: pd.Timestamp, price: float, reason: str) -> dict:
        trade = self.active_trade
        if trade.direction == TradeDirection.LONG:
            trade.pnl = (price - trade.entry_price) * trade.size
            if trade.scale_size > 0 and trade.scale_entry:
                trade.pnl += (price - trade.scale_entry) * trade.scale_size
        else:
            trade.pnl = (trade.entry_price - price) * trade.size
            if trade.scale_size > 0 and trade.scale_entry:
                trade.pnl += (trade.scale_entry - price) * trade.scale_size

        trade.exit_price = price
        trade.exit_time = ts
        trade.exit_reason = reason
        trade.state = TradeState.CLOSED

        self._daily_pnl += trade.pnl
        self.trade_history.append(trade)
        self.active_trade = None
        self.state = TradeState.WAITING

        # Check daily halt
        if self._daily_pnl <= -(abs(self._daily_pnl) + 1) * self.daily_loss_limit:
            self._daily_halted = True

        logger.info("CLOSE: %s @ %.2f | PnL=%.2f | reason=%s", trade.direction.value, price, trade.pnl, reason)
        return {"action": "close", "price": price, "size": trade.total_size,
                "pnl": trade.pnl, "reason": reason}

    def _reset_daily_if_needed(self, ts: pd.Timestamp) -> None:
        date = ts.normalize()
        if self._current_date is None or date > self._current_date:
            self._current_date = date
            self._daily_pnl = 0.0
            self._daily_halted = False

    def _estimate_dol(self, direction: TradeDirection, current_price: float) -> float:
        """Rough DOL estimate: use recent swing H/L as target."""
        relevant = [
            e for e in self._structure_events
            if e.broken_level != 0
        ]
        if not relevant:
            # fallback: 1% target
            if direction == TradeDirection.LONG:
                return current_price * 1.01
            return current_price * 0.99

        if direction == TradeDirection.LONG:
            targets = [e.broken_level for e in relevant if e.broken_level > current_price]
            return min(targets) if targets else current_price * 1.01
        else:
            targets = [e.broken_level for e in relevant if e.broken_level < current_price]
            return max(targets) if targets else current_price * 0.99


# --- utils ---

def _align(nq: pd.DataFrame, es: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    common = nq.index.intersection(es.index)
    return nq.loc[common], es.loc[common]


def _bar_to_df(ts: pd.Timestamp, bar: pd.Series) -> pd.DataFrame:
    return pd.DataFrame([bar], index=[ts])
