# BIN BOT — Event-Driven Futures ATS

An event-driven Automated Trading System, built around an institutional-style
pre-trade risk engine and a Binance Futures funding-momentum scanner strategy
(watches every USDT-M perpetual for a large price move confirmed by a
funding-rate trend). The same `EventBus` / `BaseStrategy` / `RiskEngine` /
`OrderLifecycleManager` stack drives both the live trading loop and the
event-driven backtester, so strategy code is identical in both. A companion
Next.js dashboard (`dashboard/`) gives it a live control panel.

## Layout

```
config/       settings (pydantic-settings) + structlog JSON logging
core/         event model (Tick/Bar/Signal/Execution/...) + asyncio EventBus
data/         Binance exchange-wide market data feed, DuckDB tick/bar storage, bar aggregator
strategies/   BaseStrategy ABC, funding-momentum scanner strategy
execution/    broker interface, Binance Futures adapter, order lifecycle manager, slippage
risk/         pre-trade risk engine, drawdown circuit breaker, margin checks, position sizing
backtest/     event-driven backtest engine, market-impact fill simulator, performance analytics
utils/        idempotent state recovery (position reconciliation + circuit-breaker snapshot)
scripts/      run_live_binance_scanner.py
dashboard/    Next.js live dashboard (telemetry, order blotter, risk controls, audit stream)
tests/        pre-trade risk checks, backtest no-lookahead / event-routing tests, scanner logic
```

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # edit as needed
```

`ib_async` is only imported at call time inside `execution/ibkr_broker.py`
and `data/ibkr_feed.py` (kept as generic broker infrastructure, currently
without an active entry-point script), so the risk engine, backtester, and
strategies run with zero dependency on a live TWS/Gateway connection.

## Run live (Binance Futures testnet or live)

```bash
python scripts/run_live_binance_scanner.py
```

`BINANCE_TESTNET` defaults to `true` (see `.env.example`) — practice funds
only until deliberately flipped to `false`. On startup this backfills
recent price/funding history from historical klines, reconciles the
position book from Binance's own records (never from local cache), and
restores same-day circuit-breaker state from `data_store/session_state.json`
if present — a halted system comes back up halted.

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
