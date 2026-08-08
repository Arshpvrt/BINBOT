"""Entry point: run the live (or paper) trading loop against IBKR.

    python scripts/run_live.py

Configuration is entirely environment-driven — see config/settings.py and
.env.example. Defaults point at a local TWS/Gateway paper-trading session
(port 7497).
"""
from __future__ import annotations

import asyncio
import dataclasses
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import configure_logging, get_logger
from config.settings import get_settings
from core.event_bus import EventBus
from core.events import Event, EventType, SignalEvent, TickEvent
from data.bar_aggregator import BarAggregator
from data.ibkr_feed import IBKRMarketDataFeed
from data.storage import MarketDataStore
from execution.ibkr_broker import IBKRBroker
from execution.order_manager import OrderLifecycleManager
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.margin import MarginCalculator, MarginRequirement
from risk.risk_engine import RiskEngine
from strategies.pairs_trading import KalmanPairsStrategy, PairsTradingParams
from utils.state_recovery import StateRecoveryService

logger = get_logger(__name__)

CONTRACT_MULTIPLIERS = {"ES": 50.0, "NQ": 20.0}


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger.info("startup", env=settings.env)

    broker = IBKRBroker(
        host=settings.ibkr.host,
        port=settings.ibkr.port,
        client_id=settings.ibkr.client_id,
        account=settings.ibkr.account,
        connect_timeout_s=settings.ibkr.connect_timeout_s,
        reconnect_backoff_s=settings.ibkr.reconnect_backoff_s,
        reconnect_backoff_max_s=settings.ibkr.reconnect_backoff_max_s,
        heartbeat_interval_s=settings.ibkr.heartbeat_interval_s,
        heartbeat_timeout_s=settings.ibkr.heartbeat_timeout_s,
    )
    await broker.connect()

    # Redis fan-out is optional cross-process infrastructure (e.g. a
    # separate dashboard process) — a single trading process works fine
    # without it, so we only enable it if explicitly configured.
    event_bus = EventBus()
    store = MarketDataStore(settings.data.duckdb_path)

    circuit_breaker = DrawdownCircuitBreaker(
        max_daily_drawdown_pct=settings.risk.max_daily_drawdown_pct, starting_equity=1.0
    )
    margin_calc = MarginCalculator(
        initial_margin_buffer_pct=settings.risk.initial_margin_buffer_pct,
        maintenance_margin_buffer_pct=settings.risk.maintenance_margin_buffer_pct,
    )
    margin_calc.set_requirement(
        MarginRequirement(
            symbol=settings.strategy.pairs_symbol_a,
            initial_margin_per_contract=12000.0,
            maintenance_margin_per_contract=11000.0,
        )
    )
    margin_calc.set_requirement(
        MarginRequirement(
            symbol=settings.strategy.pairs_symbol_b,
            initial_margin_per_contract=12000.0,
            maintenance_margin_per_contract=11000.0,
        )
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
    for symbol, mult in CONTRACT_MULTIPLIERS.items():
        risk_engine.set_contract_multiplier(symbol, mult)

    recovery = StateRecoveryService(broker=broker, risk_engine=risk_engine, circuit_breaker=circuit_breaker)
    recovery_result = await recovery.recover()
    logger.info("state_recovery.result", **dataclasses.asdict(recovery_result))

    order_manager = OrderLifecycleManager(broker, event_bus)
    order_manager.start()

    feed = IBKRMarketDataFeed(broker.ib, event_bus)
    await feed.connect()

    bar_aggregator = BarAggregator(timeframe_s=60)

    async def on_tick(event: Event) -> None:
        assert isinstance(event, TickEvent)
        store.write_tick(event)
        risk_engine.update_market_state(event.symbol, event.mid)
        completed_bar = bar_aggregator.on_tick(event)
        if completed_bar is not None:
            store.write_bar(completed_bar)
            await event_bus.publish(completed_bar)

    event_bus.subscribe(EventType.TICK, on_tick)

    last_price: dict[str, float] = {}

    async def on_signal(event: Event) -> None:
        assert isinstance(event, SignalEvent)
        account_equity = await broker.get_account_equity()
        positions = await broker.get_positions()
        gross_exposure_usd = sum(
            abs(qty) * last_price.get(sym, 0.0) * CONTRACT_MULTIPLIERS.get(sym, 1.0)
            for sym, qty in positions.items()
        )
        initial_margin, maintenance_margin = await broker.get_margin_usage()
        result = risk_engine.check_order(
            event,
            account_equity=account_equity,
            gross_exposure_usd=gross_exposure_usd,
            current_initial_margin_used=initial_margin,
            current_maintenance_margin_used=maintenance_margin,
        )
        if not result.passed:
            return
        await order_manager.submit(event, expected_price=last_price.get(event.symbol))

    event_bus.subscribe(EventType.SIGNAL, on_signal)

    strategy = KalmanPairsStrategy(
        strategy_id="kalman-pairs-live",
        event_bus=event_bus,
        params=PairsTradingParams(
            symbol_a=settings.strategy.pairs_symbol_a,
            symbol_b=settings.strategy.pairs_symbol_b,
            zscore_entry=settings.strategy.zscore_entry,
            zscore_exit=settings.strategy.zscore_exit,
            zscore_stop=settings.strategy.zscore_stop,
            kalman_delta=settings.strategy.kalman_delta,
            kalman_obs_covariance=settings.strategy.kalman_obs_covariance,
        ),
    )
    strategy.attach()

    for symbol in (settings.strategy.pairs_symbol_a, settings.strategy.pairs_symbol_b):
        await feed.subscribe(symbol)

    await event_bus.start()

    shutdown_event = asyncio.Event()

    def _handle_shutdown_signal() -> None:
        logger.warning("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _handle_shutdown_signal)
            except NotImplementedError:
                pass  # signal handlers are not supported on Windows event loops

    async def monitor_loop() -> None:
        while not shutdown_event.is_set():
            await asyncio.sleep(settings.ibkr.heartbeat_interval_s)
            equity = await broker.get_account_equity()
            for symbol, qty in (await broker.get_positions()).items():
                risk_engine.update_position(symbol, qty)
            if circuit_breaker.update(equity):
                logger.critical("circuit_breaker.tripped_cancelling_all_orders")
                await order_manager.cancel_all()
            recovery.persist()

    try:
        await asyncio.wait(
            [asyncio.create_task(monitor_loop()), asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        logger.info("shutdown.initiating")
        await order_manager.stop()
        await event_bus.stop()
        await feed.disconnect()
        await broker.disconnect()
        store.close()
        logger.info("shutdown.complete")


if __name__ == "__main__":
    asyncio.run(main())
