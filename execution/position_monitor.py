"""Continuously watches every open position's real unrealized P&L (read
directly from Binance, not recomputed locally — see
`BinanceFuturesBroker.get_position_pnl`) and closes a position the instant
it crosses the stop-loss or take-profit threshold.

This is also the single shared source of truth for "how many positions are
open right now" and "which symbols" — the scanner strategy queries it
before allowing a new entry, so entry-gating and exit-watching can never
disagree about current state (both read the same broker snapshot, taken
here, rather than each polling the exchange independently).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from config.logging_config import get_logger
from core.enums import OrderSide, OrderType, TimeInForce
from core.events import SignalEvent
from execution.binance_broker import BinanceFuturesBroker
from execution.order_manager import OrderLifecycleManager

logger = get_logger(__name__)


class PositionMonitor:
    def __init__(
        self,
        broker: BinanceFuturesBroker,
        order_manager: OrderLifecycleManager,
        *,
        stop_loss_usdt: float,
        take_profit_usdt: float,
        poll_interval_s: float = 2.0,
        on_close_event: Callable[[str, str], "asyncio.Future[None] | None"] | None = None,
        price_lookup: Callable[[str], float | None] | None = None,
    ) -> None:
        self._broker = broker
        self._order_manager = order_manager
        self._stop_loss_usdt = stop_loss_usdt
        self._take_profit_usdt = take_profit_usdt
        self._poll_interval_s = poll_interval_s
        self._on_close_event = on_close_event
        self._price_lookup = price_lookup

        self._positions: dict[str, int] = {}
        self._closing: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def open_symbols(self) -> set[str]:
        return set(self._positions.keys())

    def open_count(self) -> int:
        return len(self._positions)

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="position-monitor")
        logger.info(
            "position_monitor.started",
            stop_loss_usdt=self._stop_loss_usdt,
            take_profit_usdt=self._take_profit_usdt,
            poll_interval_s=self._poll_interval_s,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_once()
            except Exception:
                logger.exception("position_monitor.check_failed")
            await asyncio.sleep(self._poll_interval_s)

    async def _check_once(self) -> None:
        positions = await self._broker.get_positions()
        current_symbols = {sym for sym, qty in positions.items() if qty != 0}
        self._closing &= current_symbols  # a symbol no longer open can be re-armed for future monitoring

        pnl = await self._broker.get_position_pnl()
        self._positions = {sym: qty for sym, qty in positions.items() if qty != 0}

        for symbol, qty in self._positions.items():
            if symbol in self._closing:
                continue
            unrealized = pnl.get(symbol, 0.0)
            if unrealized <= -self._stop_loss_usdt:
                await self._close_position(
                    symbol,
                    qty,
                    reason=f"STOP-LOSS: {unrealized:.2f} USDT <= -{self._stop_loss_usdt:.0f} USDT",
                )
            elif unrealized >= self._take_profit_usdt:
                await self._close_position(
                    symbol,
                    qty,
                    reason=f"TAKE-PROFIT: {unrealized:.2f} USDT >= {self._take_profit_usdt:.0f} USDT",
                )

    async def _close_position(self, symbol: str, qty: int, *, reason: str) -> None:
        self._closing.add(symbol)
        side = OrderSide.SELL if qty > 0 else OrderSide.BUY
        signal = SignalEvent(
            ts=datetime.now(timezone.utc),
            symbol=symbol,
            strategy_id="position-monitor",
            side=side,
            target_quantity=abs(qty),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        logger.warning("position_monitor.closing", symbol=symbol, reason=reason)
        if self._on_close_event is not None:
            maybe_awaitable = self._on_close_event(symbol, reason)
            if maybe_awaitable is not None:
                await maybe_awaitable
        expected_price = self._price_lookup(symbol) if self._price_lookup else None
        # Deliberately does not go through RiskEngine.check_order(): this
        # order only ever REDUCES exposure (it's a full close of an
        # existing position), and a stop-loss/take-profit exit must not be
        # blockable by limits designed to prevent taking on MORE risk.
        await self._order_manager.submit(signal, expected_price=expected_price)
