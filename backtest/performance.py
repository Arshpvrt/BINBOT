"""Performance analytics: Sharpe, Sortino, Calmar, Max Drawdown, Win Rate,
Expectancy, and trade duration distribution statistics, computed from an
equity curve and a list of round-trip `TradeRecord`s produced by the
backtest engine / market simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from core.enums import OrderSide


@dataclass(frozen=True, slots=True)
class TradeRecord:
    symbol: str
    side: OrderSide  # direction of the opening leg
    entry_ts: datetime
    exit_ts: datetime
    quantity: int
    pnl: float  # net of commission

    @property
    def duration_seconds(self) -> float:
        return (self.exit_ts - self.entry_ts).total_seconds()

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    total_return_pct: float
    annualized_return_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    win_rate_pct: float
    expectancy: float
    profit_factor: float
    num_trades: int
    avg_trade_duration_s: float
    median_trade_duration_s: float
    trade_duration_p90_s: float
    gross_profit: float
    gross_loss: float


def _drawdown_series(equity: np.ndarray) -> tuple[np.ndarray, float, int]:
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    # longest stretch (in bars) spent at or below the eventual trough's
    # drawdown regime, measured as time-to-recovery from the running peak
    duration = 0
    longest = 0
    for i in range(len(equity)):
        if equity[i] < running_max[i]:
            duration += 1
            longest = max(longest, duration)
        else:
            duration = 0
    return drawdown, max_dd, longest


def compute_performance_report(
    *,
    equity_curve: list[float],
    trades: list[TradeRecord],
    bars_per_year: float = 252.0,
    risk_free_rate_pct: float = 0.0,
) -> PerformanceReport:
    if len(equity_curve) < 2:
        raise ValueError("equity_curve needs at least 2 points to compute returns")

    equity = np.asarray(equity_curve, dtype=float)
    period_returns = np.diff(equity) / equity[:-1]

    total_return_pct = (equity[-1] / equity[0] - 1.0) * 100.0
    n_periods = len(period_returns)
    years = n_periods / bars_per_year if bars_per_year > 0 else 1.0
    annualized_return_pct = (
        ((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and equity[0] > 0 else 0.0
    )

    vol = float(period_returns.std(ddof=1)) if n_periods > 1 else 0.0
    annualized_vol_pct = vol * (bars_per_year**0.5) * 100.0

    rf_per_period = (risk_free_rate_pct / 100.0) / bars_per_year
    excess_returns = period_returns - rf_per_period
    sharpe = (
        float(excess_returns.mean() / excess_returns.std(ddof=1) * (bars_per_year**0.5))
        if n_periods > 1 and excess_returns.std(ddof=1) > 0
        else 0.0
    )

    downside_returns = excess_returns[excess_returns < 0]
    downside_std = float(downside_returns.std(ddof=1)) if len(downside_returns) > 1 else 0.0
    sortino = (
        float(excess_returns.mean() / downside_std * (bars_per_year**0.5)) if downside_std > 0 else 0.0
    )

    _, max_dd, dd_duration = _drawdown_series(equity)
    max_dd_pct = abs(max_dd) * 100.0
    calmar = annualized_return_pct / max_dd_pct if max_dd_pct > 0 else 0.0

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    win_rate_pct = (len(wins) / len(trades) * 100.0) if trades else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    p_win = len(wins) / len(trades) if trades else 0.0
    p_loss = 1.0 - p_win
    expectancy = p_win * avg_win - p_loss * avg_loss

    durations = np.array([t.duration_seconds for t in trades], dtype=float) if trades else np.array([])
    avg_duration = float(durations.mean()) if len(durations) else 0.0
    median_duration = float(np.median(durations)) if len(durations) else 0.0
    p90_duration = float(np.percentile(durations, 90)) if len(durations) else 0.0

    return PerformanceReport(
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        annualized_volatility_pct=annualized_vol_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=dd_duration,
        win_rate_pct=win_rate_pct,
        expectancy=expectancy,
        profit_factor=profit_factor,
        num_trades=len(trades),
        avg_trade_duration_s=avg_duration,
        median_trade_duration_s=median_duration,
        trade_duration_p90_s=p90_duration,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
    )
