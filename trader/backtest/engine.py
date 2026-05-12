"""
Backtest engine — replay historical NQ/ES bars through the OBM strategy.

Usage:
    from trader.backtest.engine import run_backtest
    results = run_backtest(account_value=10_000, period="30d")
    print(results.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from trader.data.feed import fetch_nq_es, align_bars, session_bars
from trader.risk.manager import RiskConfig, RiskManager, recommended_instrument
from trader.strategy.obm import OBMStrategy, TradeDirection

logger = logging.getLogger(__name__)


@dataclass
class BacktestResults:
    trades: list[dict] = field(default_factory=list)
    account_start: float = 0.0
    account_end: float = 0.0
    period: str = ""

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> list[dict]:
        return [t for t in self.trades if t.get("pnl", 0) > 0]

    @property
    def losing_trades(self) -> list[dict]:
        return [t for t in self.trades if t.get("pnl", 0) <= 0]

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return len(self.winning_trades) / len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.get("pnl", 0) for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t["pnl"] for t in self.winning_trades)
        gross_loss = abs(sum(t["pnl"] for t in self.losing_trades))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        if not self.trades:
            return 0.0
        equity = self.account_start
        peak = equity
        max_dd = 0.0
        for t in self.trades:
            equity += t.get("pnl", 0)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def avg_win(self) -> float:
        wins = self.winning_trades
        return sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = self.losing_trades
        return sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0

    @property
    def rr_ratio(self) -> float:
        if self.avg_loss == 0:
            return 0.0
        return abs(self.avg_win / self.avg_loss)

    def summary(self) -> str:
        lines = [
            "=" * 50,
            f"  OBM Backtest Results ({self.period})",
            "=" * 50,
            f"  Account:       ${self.account_start:,.2f} → ${self.account_end:,.2f}",
            f"  Total P&L:     ${self.total_pnl:,.2f}",
            f"  Total trades:  {self.total_trades}",
            f"  Win rate:      {self.win_rate:.1%}",
            f"  Profit factor: {self.profit_factor:.2f}",
            f"  Avg win:       ${self.avg_win:,.2f}",
            f"  Avg loss:      ${self.avg_loss:,.2f}",
            f"  R:R ratio:     {self.rr_ratio:.2f}",
            f"  Max drawdown:  {self.max_drawdown:.1%}",
            "=" * 50,
        ]
        return "\n".join(lines)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.trades)


def run_backtest(
    account_value: float = 10_000.0,
    period: str = "30d",
    initial_risk_pct: float = 0.005,
    scale_risk_pct: float = 0.003,
    daily_loss_limit_pct: float = 0.02,
    swing_lookback: int = 5,
    smt_tolerance: float = 0.002,
    execution_tf: str = "1m",
    context_tf: str = "5m",
) -> BacktestResults:
    """
    Run a full backtest over historical data.

    Args:
        account_value: starting account size in USD
        period: yfinance period string e.g. "30d", "60d" (max ~60d for 1m data)
        execution_tf: bar interval for strategy execution (default 1m)
        context_tf: bar interval for SMC context (FVG/OB detection, default 5m)
    """
    logger.info("Fetching data: NQ + ES @ %s for %s", execution_tf, period)
    nq_data, es_data = fetch_nq_es(execution_tf, period=period)

    # Align timestamps
    nq_df, es_df = align_bars(nq_data, es_data)

    # Filter to session hours
    nq_session = session_bars(nq_df)
    es_session = session_bars(es_df)
    common_idx = nq_session.index.intersection(es_session.index)
    nq_session = nq_session.loc[common_idx]
    es_session = es_session.loc[common_idx]

    logger.info("Bars loaded: %d", len(nq_session))

    # Build context from first half of data (walk-forward seed)
    seed_size = min(len(nq_session) // 4, 500)
    nq_seed = nq_session.iloc[:seed_size].copy()
    nq_seed.index = nq_seed.index.tz_convert("UTC")
    es_seed = es_session.iloc[:seed_size].copy()
    es_seed.index = es_seed.index.tz_convert("UTC")

    instrument = recommended_instrument(account_value)
    risk_config = RiskConfig(
        account_value=account_value,
        initial_risk_pct=initial_risk_pct,
        scale_risk_pct=scale_risk_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        use_micro=(instrument == "MNQ"),
    )
    risk_manager = RiskManager(risk_config)

    strategy = OBMStrategy(
        initial_risk_pct=initial_risk_pct,
        scale_risk_pct=scale_risk_pct,
        daily_loss_limit=daily_loss_limit_pct,
        swing_lookback=swing_lookback,
        smt_tolerance=smt_tolerance,
    )
    strategy.initialize(nq_seed, es_seed, execution_tf)

    results = BacktestResults(account_start=account_value, period=period)
    current_account = account_value

    for i in range(seed_size, len(nq_session)):
        ts = nq_session.index[i]
        nq_bar = nq_session.iloc[i]
        es_bar = es_session.iloc[i]

        can_trade, reason = risk_manager.can_trade(ts)
        if not can_trade:
            continue

        order = strategy.update(ts, nq_bar, es_bar, current_account)

        if order is None:
            continue

        action = order.get("action", "")

        if action == "close":
            pnl = order.get("pnl", 0.0)
            current_account += pnl
            risk_manager.update_account_value(current_account)
            halted = risk_manager.record_trade_close(ts, pnl)
            results.trades.append({
                "timestamp": ts,
                "action": action,
                "price": order.get("price"),
                "size": order.get("size"),
                "pnl": pnl,
                "reason": order.get("reason"),
                "account_after": current_account,
                "halted": halted,
            })
            if halted:
                logger.warning("Daily halt triggered at %s | account: $%.2f", ts, current_account)

        elif action in ("open_long", "open_short"):
            results.trades.append({
                "timestamp": ts,
                "action": action,
                "price": order.get("price"),
                "size": order.get("size"),
                "sl": order.get("sl"),
                "tp": order.get("tp"),
                "reason": order.get("reason"),
                "pnl": None,
            })

    results.account_end = current_account
    logger.info("Backtest complete. Final account: $%.2f", current_account)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = run_backtest(account_value=10_000, period="5d")
    print(results.summary())
    closed = [t for t in results.trades if t["action"] == "close"]
    if closed:
        df = pd.DataFrame(closed)
        print(df[["timestamp", "price", "pnl", "reason"]].to_string())
