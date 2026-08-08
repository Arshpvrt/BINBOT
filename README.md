# BIN BOT — Event-Driven Futures ATS

An event-driven Automated Trading System for financial futures, built around
IBKR (`ib_async`), an institutional-style pre-trade risk engine, and a
Kalman-filtered statistical-arbitrage pairs strategy. The same `EventBus` /
`BaseStrategy` / `RiskEngine` / `OrderLifecycleManager` stack drives both the
live trading loop and the event-driven backtester, so strategy code is
identical in both.

## Layout

```
config/       settings (pydantic-settings) + structlog JSON logging
core/         event model (Tick/Bar/Signal/Execution/...) + asyncio EventBus
data/         IBKR market data feed, DuckDB tick/bar storage, bar aggregator
strategies/   BaseStrategy ABC, polars feature calcs, Kalman-filter pairs strategy
execution/    broker interface, IBKR adapter, order lifecycle manager (OCA/OCO), slippage
risk/         pre-trade risk engine, drawdown circuit breaker, margin checks, position sizing
backtest/     event-driven backtest engine, market-impact fill simulator, performance analytics
utils/        idempotent state recovery (position reconciliation + circuit-breaker snapshot)
scripts/      run_live.py, run_backtest.py
tests/        pre-trade risk checks, backtest no-lookahead / event-routing tests
```

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # edit as needed
```

`ib_async` is only imported at call time inside `execution/ibkr_broker.py`
and `data/ibkr_feed.py`, so the risk engine, backtester, and strategies run
with zero dependency on a live TWS/Gateway connection.

## Run the backtest

```bash
python scripts/run_backtest.py
```

With no `--db` flag this runs against synthetically generated cointegrated
price data so the full pipeline (strategy → risk → simulated fills →
performance report) can be exercised without a market data source. Point it
at a DuckDB store populated via `data.storage.MarketDataStore` for real
historical data:

```bash
python scripts/run_backtest.py --db ./data_store/market_data.duckdb --symbol-a ES --symbol-b NQ
```

## Run live / paper trading

Requires a running TWS or IB Gateway instance (defaults to paper port 7497):

```bash
python scripts/run_live.py
```

On startup this reconciles the position book from IBKR's own records (never
from local cache) and restores same-day circuit-breaker state from
`data_store/session_state.json` if present — a halted system comes back up
halted.

## Tests

```bash
python -m pytest tests/ -v
```

Covers pre-trade risk checks (fat-finger, position/notional/leverage/margin
limits, order-rate limiting, circuit-breaker gating, Kelly/target-vol
position sizing) and backtest event routing, including an explicit
no-lookahead test asserting a signal generated reacting to bar T can only
ever fill using bar T+1 data.

## Design notes

- **No lookahead by construction**: the backtest engine resolves any orders
  pending from bar T-1 using bar T's OHLC *before* bar T is published to
  strategies, so a decision can never be filled on the same or earlier
  information that produced it (see `backtest/engine.py`).
- **Risk is a hard gate, not advice**: every `SignalEvent` passes through
  `RiskEngine.check_order()` before `OrderLifecycleManager` ever sees it.
  Circuit-breaker trips persist until an explicit session reset.
- **Idempotent recovery**: position inventory is always rebuilt from the
  broker's execution log on startup, never trusted from a local cache.
