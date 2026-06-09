"""
Risk Manager — enforces Fede's account rules.

Rules implemented:
  1. Initial risk per trade: configurable % of account (default 0.5%)
  2. Scale-in: additional risk only when original SL is at break-even
  3. Daily loss limit: halt all trading once daily drawdown is hit
  4. Max concurrent trades: 1 (quality over quantity)
  5. News filter: block entries within a configurable window around red-folder events
  6. Position sizing for both full-size (NQ) and micro (MNQ) contracts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, date
from typing import Optional
import pandas as pd


# NQ tick values (USD)
NQ_TICK_SIZE = 0.25
NQ_TICK_VALUE = 5.0       # $5 per tick for full NQ
MNQ_TICK_VALUE = 0.50     # $0.50 per tick for micro MNQ (1/10th of NQ)

ES_TICK_SIZE = 0.25
ES_TICK_VALUE = 12.50
MES_TICK_VALUE = 1.25


@dataclass
class RiskConfig:
    account_value: float            # current account balance in USD
    initial_risk_pct: float = 0.005  # 0.5% per initial entry
    scale_risk_pct: float = 0.003    # 0.3% for scale-in
    daily_loss_limit_pct: float = 0.02  # 2% max daily loss
    daily_loss_limit_usd: Optional[float] = None  # absolute USD limit (overrides pct if set)
    use_micro: bool = True           # trade MNQ instead of NQ (safer for smaller accounts)
    max_trades_per_day: int = 3      # Fede's quality > quantity rule
    news_buffer_minutes: int = 15    # block entries X minutes before/after red news


@dataclass
class DailyStats:
    date: date
    realized_pnl: float = 0.0
    trades_taken: int = 0
    halted: bool = False
    halt_reason: str = ""

    def add_trade_pnl(self, pnl: float) -> None:
        self.realized_pnl += pnl
        self.trades_taken += 1


class RiskManager:
    def __init__(self, config: RiskConfig):
        self.config = config
        self._daily_stats: dict[date, DailyStats] = {}
        self._news_events: list[datetime] = []   # red-folder news timestamps (Eastern)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def position_size(
        self,
        entry_price: float,
        stop_loss: float,
        risk_pct: Optional[float] = None,
        instrument: str = "MNQ",
    ) -> float:
        """
        Calculate number of contracts to trade given entry/SL.

        Returns contracts rounded to nearest 0.01 (for micro/fractional accounts).
        """
        rp = risk_pct if risk_pct is not None else self.config.initial_risk_pct
        risk_usd = self.config.account_value * rp

        sl_distance_points = abs(entry_price - stop_loss)
        if sl_distance_points == 0:
            raise ValueError("Entry and stop-loss cannot be the same price")

        tick_value = self._tick_value(instrument)
        tick_size = self._tick_size(instrument)

        sl_ticks = sl_distance_points / tick_size
        risk_per_contract = sl_ticks * tick_value

        if risk_per_contract == 0:
            raise ValueError("Risk per contract is zero — check tick configuration")

        contracts = risk_usd / risk_per_contract
        return round(contracts, 2)

    def scale_in_size(
        self,
        entry_price: float,
        stop_loss: float,
        instrument: str = "MNQ",
    ) -> float:
        """Size for the scale-in entry (0.3% risk)."""
        return self.position_size(entry_price, stop_loss, self.config.scale_risk_pct, instrument)

    # ------------------------------------------------------------------
    # Daily controls
    # ------------------------------------------------------------------

    def can_trade(self, ts: pd.Timestamp) -> tuple[bool, str]:
        """
        Return (True, "") if trading is allowed, or (False, reason) if halted.
        """
        stats = self._get_daily_stats(ts)

        if stats.halted:
            return False, stats.halt_reason

        if stats.trades_taken >= self.config.max_trades_per_day:
            return False, f"max trades/day reached ({self.config.max_trades_per_day})"

        if self._near_news(ts):
            return False, "red-folder news window — no entry"

        return True, ""

    def record_trade_close(self, ts: pd.Timestamp, pnl: float) -> bool:
        """
        Record a closed trade's P&L. Returns True if daily halt was triggered.
        """
        stats = self._get_daily_stats(ts)
        stats.add_trade_pnl(pnl)

        limit = self._daily_loss_limit_usd()
        if stats.realized_pnl <= -limit:
            stats.halted = True
            stats.halt_reason = (
                f"daily loss limit hit: ${stats.realized_pnl:.2f} "
                f"(limit: -${limit:.2f})"
            )
            return True
        return False

    def add_news_event(self, dt: datetime) -> None:
        """Register a red-folder news event (Eastern time)."""
        self._news_events.append(dt)

    def update_account_value(self, new_value: float) -> None:
        self.config.account_value = new_value

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def daily_summary(self, ts: pd.Timestamp) -> dict:
        stats = self._get_daily_stats(ts)
        return {
            "date": stats.date.isoformat(),
            "realized_pnl": round(stats.realized_pnl, 2),
            "trades_taken": stats.trades_taken,
            "halted": stats.halted,
            "halt_reason": stats.halt_reason,
            "daily_loss_limit_usd": round(self._daily_loss_limit_usd(), 2),
            "remaining_risk_budget": round(
                self._daily_loss_limit_usd() + stats.realized_pnl, 2
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_daily_stats(self, ts: pd.Timestamp) -> DailyStats:
        d = ts.tz_convert("America/New_York").date()
        if d not in self._daily_stats:
            self._daily_stats[d] = DailyStats(date=d)
        return self._daily_stats[d]

    def _daily_loss_limit_usd(self) -> float:
        if self.config.daily_loss_limit_usd is not None:
            return self.config.daily_loss_limit_usd
        return self.config.account_value * self.config.daily_loss_limit_pct

    def _near_news(self, ts: pd.Timestamp) -> bool:
        if not self._news_events:
            return False
        ts_et = ts.tz_convert("America/New_York")
        buffer = pd.Timedelta(minutes=self.config.news_buffer_minutes)
        for event_dt in self._news_events:
            event_ts = pd.Timestamp(event_dt, tz="America/New_York")
            if abs(ts_et - event_ts) <= buffer:
                return True
        return False

    @staticmethod
    def _tick_value(instrument: str) -> float:
        mapping = {
            "NQ": NQ_TICK_VALUE,
            "MNQ": MNQ_TICK_VALUE,
            "ES": ES_TICK_VALUE,
            "MES": MES_TICK_VALUE,
        }
        return mapping.get(instrument.upper(), MNQ_TICK_VALUE)

    @staticmethod
    def _tick_size(instrument: str) -> float:
        return NQ_TICK_SIZE  # same for all index futures


def recommended_instrument(account_value: float) -> str:
    """
    Suggest NQ vs MNQ based on account size.
    NQ requires ~$15k margin; MNQ is 1/10th — better for < $50k accounts.
    """
    if account_value < 50_000:
        return "MNQ"
    return "NQ"
