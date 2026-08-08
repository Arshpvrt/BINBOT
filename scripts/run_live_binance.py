"""Entry point: run the live (or testnet) trading loop against Binance USDT-M Futures.

    python scripts/run_live_binance.py

Reads BINANCE_API_KEY / BINANCE_API_SECRET from the environment ONLY —
never hardcode them here or anywhere else. `BINANCE_TESTNET` defaults to
true (see config/settings.py); it must be flipped to false deliberately,
never as a side effect of some other change, before this script will ever
touch a real account.

Quantity note: signals/orders in this whole system are counted in whole
"lots," where one lot = one exchange step-size increment for that symbol
(see execution/binance_broker.py's module docstring). `PairsTradingParams
.base_quantity_a` below is therefore a lot count, not a raw BTC/ETH amount
— check the printed `step_size` at startup to know what one lot means in
real terms before sizing it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import configure_logging, get_logger
from config.settings import get_settings
from core.enums import OrderSide, OrderType, TimeInForce
from core.event_bus import EventBus
from core.events import Event, EventType, SignalEvent, TickEvent
from data.bar_aggregator import BarAggregator
from data.binance_feed import BinanceFuturesMarketDataFeed
from data.storage import MarketDataStore
from execution.binance_broker import BinanceFuturesBroker
from execution.order_manager import OrderLifecycleManager
from execution.trade_tracker import TradeTracker
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.margin import MarginCalculator, MarginRequirement
from risk.risk_engine import RiskEngine
from server.dashboard_bridge import DashboardBridge, load_or_create_control_token
from strategies.pairs_trading import KalmanPairsStrategy, PairsTradingParams
from utils.state_recovery import StateRecoveryService

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    bset = settings.binance
    if len(bset.symbols) != 2:
        raise ValueError(
            "BINANCE_SYMBOLS must list exactly 2 symbols for the pairs strategy, "
            f"got {bset.symbols}"
        )
    symbol_a, symbol_b = bset.symbols

    if bset.testnet:
        logger.warning("startup.TESTNET_MODE", msg="Trading on Binance TESTNET (practice funds only)")
    else:
        logger.warning(
            "startup.LIVE_MODE_REAL_MONEY",
            msg="BINANCE_TESTNET=false — this will place REAL orders with REAL funds",
        )

    api_key = bset.api_key.get_secret_value()
    api_secret = bset.api_secret.get_secret_value()
    if not api_key or not api_secret:
        raise RuntimeError(
            "BINANCE_API_KEY / BINANCE_API_SECRET are not set. Put them in your .env file "
            "(never in code, never committed to git) before running this script."
        )

    broker = BinanceFuturesBroker(
        api_key=api_key,
        api_secret=api_secret,
        testnet=bset.testnet,
        symbols=bset.symbols,
        leverage=bset.leverage,
        margin_type=bset.margin_type,
        recv_window_ms=bset.recv_window_ms,
        reconnect_backoff_s=bset.reconnect_backoff_s,
        reconnect_backoff_max_s=bset.reconnect_backoff_max_s,
    )
    await broker.connect()

    step_sizes = {s: broker.get_step_size(s) for s in bset.symbols}
    logger.info("startup.step_sizes", step_sizes=step_sizes, msg="1 lot = this many units of the asset")

    event_bus = EventBus()
    store = MarketDataStore(settings.data.duckdb_path)

    circuit_breaker = DrawdownCircuitBreaker(
        max_daily_drawdown_pct=settings.risk.max_daily_drawdown_pct, starting_equity=1.0
    )
    margin_calc = MarginCalculator(
        initial_margin_buffer_pct=settings.risk.initial_margin_buffer_pct,
        maintenance_margin_buffer_pct=settings.risk.maintenance_margin_buffer_pct,
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
    for symbol, step_size in step_sizes.items():
        # multiplier = "real asset units per lot", so notional math
        # (lots * price * multiplier) comes out correct in USD terms.
        risk_engine.set_contract_multiplier(symbol, step_size)

    recovery = StateRecoveryService(broker=broker, risk_engine=risk_engine, circuit_breaker=circuit_breaker)
    recovery_result = await recovery.recover()
    logger.info("state_recovery.result", **dataclasses.asdict(recovery_result))

    order_manager = OrderLifecycleManager(broker, event_bus)
    order_manager.start()

    last_price: dict[str, float] = {}

    strategy = KalmanPairsStrategy(
        strategy_id="kalman-pairs-binance",
        event_bus=event_bus,
        params=PairsTradingParams(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            base_quantity_a=1,  # 1 LOT of symbol_a; see step_sizes printed above for real size
            # NOTE: shortened from the default 30 bars for a supervised testnet
            # test run only. Raise this back to something conservative (30+)
            # before any unattended run, and especially before real money.
            warmup_bars=5,
            zscore_entry=settings.strategy.zscore_entry,
            zscore_exit=settings.strategy.zscore_exit,
            zscore_stop=settings.strategy.zscore_stop,
            kalman_delta=settings.strategy.kalman_delta,
            kalman_obs_covariance=settings.strategy.kalman_obs_covariance,
        ),
    )
    strategy.attach()

    control_token = load_or_create_control_token()
    logger.warning(
        "dashboard_bridge.control_token",
        msg="add this exact line to dashboard/.env.local to enable dashboard controls, then restart the dashboard",
        env_line=f"NEXT_PUBLIC_DASHBOARD_CONTROL_TOKEN={control_token}",
    )

    async def do_pause() -> None:
        strategy.pause()

    async def do_resume() -> None:
        strategy.resume()

    async def flatten_all_positions() -> None:
        positions = await broker.get_positions()
        open_positions = {sym: qty for sym, qty in positions.items() if qty != 0}
        if not open_positions:
            await dashboard_bridge.push_audit(
                level="risk", source="operator", message="Flatten requested: no open positions to close"
            )
            return
        await dashboard_bridge.push_audit(
            level="risk",
            source="operator",
            message=f"FLATTEN: closing {len(open_positions)} position(s) at market",
        )
        # Deliberately bypasses RiskEngine.check_order(): flatten only ever
        # REDUCES exposure, and an emergency liquidation must not be
        # blockable by limits designed to stop taking on MORE risk (order
        # rate limits, fat-finger caps sized for incremental entries).
        for symbol, qty in open_positions.items():
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY
            close_signal = SignalEvent(
                ts=datetime.now(timezone.utc),
                symbol=symbol,
                strategy_id="operator-flatten",
                side=side,
                target_quantity=abs(qty),
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
            )
            await order_manager.submit(close_signal, expected_price=last_price.get(symbol))

    async def engage_kill_switch() -> None:
        strategy.pause()
        await order_manager.cancel_all()
        circuit_breaker.force_halt("Manual kill switch via dashboard")
        recovery.persist()
        await dashboard_bridge.push_audit(
            level="error",
            source="operator",
            message="KILL SWITCH engaged via dashboard: cancelled all working orders and halted the trading loop",
        )

    async def reset_kill_switch() -> None:
        equity = await broker.get_account_equity()
        circuit_breaker.reset_session(equity)
        strategy.resume()
        recovery.persist()
        await dashboard_bridge.push_audit(
            level="info", source="operator", message="Kill switch reset via dashboard: trading loop re-armed"
        )

    trade_tracker = TradeTracker(broker.get_step_size)

    dashboard_bridge = DashboardBridge(
        event_bus=event_bus,
        broker=broker,
        order_manager=order_manager,
        circuit_breaker=circuit_breaker,
        max_daily_loss_usd=settings.risk.max_daily_drawdown_pct / 100.0 * (await broker.get_account_equity()),
        max_position_contracts=settings.risk.max_contracts_per_symbol,
        chart_symbol=symbol_a,
        strategy_id="kalman-pairs-binance",
        control_token=control_token,
        on_pause=do_pause,
        on_resume=do_resume,
        on_flatten=flatten_all_positions,
        on_kill_switch=engage_kill_switch,
        on_reset_kill_switch=reset_kill_switch,
        is_strategy_paused=lambda: strategy.is_paused,
        trade_tracker=trade_tracker,
        position_details_provider=broker.get_position_details,
        port=8765,
    )
    await dashboard_bridge.start()
    logger.info(
        "dashboard_bridge.ready",
        msg="dashboard can now connect at ws://localhost:8765 (set NEXT_PUBLIC_LIVE_WS_URL)",
    )

    feed = BinanceFuturesMarketDataFeed(broker.socket_manager, event_bus)
    await feed.connect()

    bar_aggregator = BarAggregator(timeframe_s=60)

    async def refresh_margin_schedule(symbol: str, price: float) -> None:
        # Binance isolated-margin requirement for one lot is approximately
        # (price * step_size) / leverage; unlike IBKR's static per-contract
        # schedule this genuinely moves with price, so we refresh it on
        # every tick rather than setting it once.
        step_size = step_sizes[symbol]
        per_lot_notional = price * step_size
        per_lot_margin = per_lot_notional / max(bset.leverage, 1)
        margin_calc.set_requirement(
            MarginRequirement(
                symbol=symbol,
                initial_margin_per_contract=per_lot_margin,
                maintenance_margin_per_contract=per_lot_margin * 0.8,
            )
        )

    async def on_tick(event: Event) -> None:
        assert isinstance(event, TickEvent)
        store.write_tick(event)
        last_price[event.symbol] = event.mid
        risk_engine.update_market_state(event.symbol, event.mid)
        await refresh_margin_schedule(event.symbol, event.mid)
        completed_bar = bar_aggregator.on_tick(event)
        if completed_bar is not None:
            store.write_bar(completed_bar)
            await event_bus.publish(completed_bar)

    event_bus.subscribe(EventType.TICK, on_tick)

    async def on_signal(event: Event) -> None:
        assert isinstance(event, SignalEvent)
        account_equity = await broker.get_account_equity()
        positions = await broker.get_positions()
        gross_exposure_usd = sum(
            abs(qty) * last_price.get(sym, 0.0) * step_sizes.get(sym, 1.0) for sym, qty in positions.items()
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
            await dashboard_bridge.push_audit(
                level="risk", source="risk_engine", message=f"REJECTED [{result.code}] {result.message}"
            )
            return
        await dashboard_bridge.push_audit(
            level="risk", source="risk_engine", message="pre-trade checks passed"
        )
        await order_manager.submit(event, expected_price=last_price.get(event.symbol))

    event_bus.subscribe(EventType.SIGNAL, on_signal)

    for symbol in bset.symbols:
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
                pass  # not supported on Windows event loops

    async def monitor_loop() -> None:
        while not shutdown_event.is_set():
            await asyncio.sleep(5.0)
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
        await dashboard_bridge.stop()
        await event_bus.stop()
        await feed.disconnect()
        await broker.disconnect()
        store.close()
        logger.info("shutdown.complete")


if __name__ == "__main__":
    asyncio.run(main())
