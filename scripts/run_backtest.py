"""Entry point: run the event-driven backtester against historical bar data.

Usage:
    python scripts/run_backtest.py --db ./data_store/market_data.duckdb \
        --symbol-a ES --symbol-b NQ --start 2024-01-01 --end 2024-06-30

With no --db given, runs against synthetically generated cointegrated
random-walk data so the full pipeline (strategy -> risk -> sim broker ->
performance report) can be exercised without a market data source.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from backtest.engine import BacktestEngine
from backtest.market_simulator import MarketImpactParams, MarketSimulatorBroker
from config.logging_config import configure_logging, get_logger
from config.settings import get_settings
from core.events import BarEvent
from data.storage import MarketDataStore
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.margin import MarginCalculator, MarginRequirement
from risk.risk_engine import RiskEngine
from strategies.pairs_trading import KalmanPairsStrategy, PairsTradingParams

logger = get_logger(__name__)

CONTRACT_MULTIPLIERS = {"ES": 50.0, "NQ": 20.0}


def _synthetic_bars(symbol_a: str, symbol_b: str, *, n_bars: int = 2000, seed: int = 42) -> list[BarEvent]:
    """Generates two cointegrated price series (asset B = beta*asset_A plus
    a mean-reverting spread) purely for demonstrating the pipeline end to
    end when no real market data is supplied."""
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)

    common_walk = np.cumsum(rng.normal(0, 1.0, n_bars))
    price_a = 4500 + common_walk * 2.0

    beta = 3.6
    spread = np.zeros(n_bars)
    theta, mu, sigma = 0.05, 0.0, 3.0
    for i in range(1, n_bars):
        spread[i] = spread[i - 1] + theta * (mu - spread[i - 1]) + rng.normal(0, sigma)
    price_b = (price_a - spread) / beta

    bars: list[BarEvent] = []
    for i in range(n_bars):
        ts = start + timedelta(minutes=i)
        volume = float(rng.integers(500, 5000))
        for symbol, price in ((symbol_a, price_a[i]), (symbol_b, price_b[i])):
            noise = rng.normal(0, 0.5)
            open_p = price + noise
            close_p = price + rng.normal(0, 0.5)
            high_p = max(open_p, close_p) + abs(rng.normal(0, 0.3))
            low_p = min(open_p, close_p) - abs(rng.normal(0, 0.3))
            bars.append(
                BarEvent(
                    ts=ts,
                    symbol=symbol,
                    timeframe_s=60,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume,
                    vwap=(open_p + close_p) / 2,
                )
            )
    return bars


def _load_bars_from_db(db_path: str, symbol: str, start: str, end: str) -> list[BarEvent]:
    store = MarketDataStore(db_path)
    rows = store.read_bars(symbol, timeframe_s=60, start_ts=start, end_ts=end)
    store.close()
    return [
        BarEvent(ts=r[0], symbol=symbol, timeframe_s=60, open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5], vwap=r[6])
        for r in rows
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the event-driven futures pairs-trading backtest")
    parser.add_argument("--db", default=None, help="DuckDB path with pre-loaded bars; omit for synthetic demo data")
    parser.add_argument("--symbol-a", default="ES")
    parser.add_argument("--symbol-b", default="NQ")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--starting-equity", type=float, default=1_000_000.0)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=False)

    if args.db:
        bars = _load_bars_from_db(args.db, args.symbol_a, args.start, args.end) + _load_bars_from_db(
            args.db, args.symbol_b, args.start, args.end
        )
        logger.info("backtest.loaded_bars_from_db", db=args.db, count=len(bars))
    else:
        bars = _synthetic_bars(args.symbol_a, args.symbol_b)
        logger.info("backtest.using_synthetic_demo_data", count=len(bars))

    circuit_breaker = DrawdownCircuitBreaker(
        max_daily_drawdown_pct=settings.risk.max_daily_drawdown_pct,
        starting_equity=args.starting_equity,
    )
    margin_calc = MarginCalculator(
        initial_margin_buffer_pct=settings.risk.initial_margin_buffer_pct,
        maintenance_margin_buffer_pct=settings.risk.maintenance_margin_buffer_pct,
    )
    for symbol in (args.symbol_a, args.symbol_b):
        margin_calc.set_requirement(
            MarginRequirement(symbol=symbol, initial_margin_per_contract=12000.0, maintenance_margin_per_contract=11000.0)
        )

    risk_engine = RiskEngine(
        max_contracts_per_order=settings.risk.max_contracts_per_order,
        max_contracts_per_symbol=settings.risk.max_contracts_per_symbol,
        max_position_notional_usd=settings.risk.max_position_notional_usd,
        max_orders_per_second=settings.risk.max_orders_per_second,
        max_orders_per_minute=settings.risk.max_orders_per_minute,
        max_gross_leverage=settings.risk.max_gross_leverage,
        circuit_breaker=circuit_breaker,
        margin_calculator=margin_calc,
    )

    broker = MarketSimulatorBroker(
        starting_equity=args.starting_equity,
        contract_multipliers=CONTRACT_MULTIPLIERS,
        impact_params=MarketImpactParams(),
    )

    engine = BacktestEngine(
        risk_engine=risk_engine,
        broker=broker,
        contract_multipliers=CONTRACT_MULTIPLIERS,
        bars_per_year=252 * 6.5 * 60,  # minute bars
    )

    strategy = KalmanPairsStrategy(
        strategy_id="kalman-pairs-1",
        event_bus=engine.event_bus,
        params=PairsTradingParams(
            symbol_a=args.symbol_a,
            symbol_b=args.symbol_b,
            base_quantity_a=1,
            zscore_entry=settings.strategy.zscore_entry,
            zscore_exit=settings.strategy.zscore_exit,
            zscore_stop=settings.strategy.zscore_stop,
            kalman_delta=settings.strategy.kalman_delta,
            kalman_obs_covariance=settings.strategy.kalman_obs_covariance,
        ),
    )
    strategy.attach()

    result = await engine.run(bars)

    perf = result.performance
    print("\n=== Backtest Performance Report ===")
    print(f"Total Return:        {perf.total_return_pct:>10.2f}%")
    print(f"Annualized Return:    {perf.annualized_return_pct:>10.2f}%")
    print(f"Annualized Vol:       {perf.annualized_volatility_pct:>10.2f}%")
    print(f"Sharpe Ratio:         {perf.sharpe_ratio:>10.2f}")
    print(f"Sortino Ratio:        {perf.sortino_ratio:>10.2f}")
    print(f"Calmar Ratio:         {perf.calmar_ratio:>10.2f}")
    print(f"Max Drawdown:         {perf.max_drawdown_pct:>10.2f}%")
    print(f"Win Rate:             {perf.win_rate_pct:>10.2f}%")
    print(f"Profit Factor:        {perf.profit_factor:>10.2f}")
    print(f"Expectancy:           {perf.expectancy:>10.2f}")
    print(f"# Trades:             {perf.num_trades:>10d}")
    print(f"Avg Trade Duration:   {perf.avg_trade_duration_s / 60:>10.1f} min")
    print(f"Halted:               {result.halted} ({result.halt_reason})")


if __name__ == "__main__":
    asyncio.run(main())
