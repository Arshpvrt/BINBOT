"""Abstract base class every strategy implements. Standardized callbacks
mean the same strategy code runs unmodified against the live event bus or
the backtester's replay event bus.

Subclasses call `self.emit_signal(...)` to express trading intent; the
strategy layer never talks to the broker/risk engine directly — signals are
picked up by whatever is subscribed to `EventType.SIGNAL` (the live trading
loop wires that to the RiskEngine + OrderLifecycleManager, the backtester
wires it to the simulated fill engine).
"""
from __future__ import annotations

import abc

from config.logging_config import get_logger
from core.event_bus import EventBus
from core.events import BarEvent, ExecutionEvent, Event, EventType, OrderBookEvent, SignalEvent, TickEvent

logger = get_logger(__name__)


class BaseStrategy(abc.ABC):
    def __init__(self, strategy_id: str, symbols: list[str], event_bus: EventBus) -> None:
        self.strategy_id = strategy_id
        self.symbols = symbols
        self._event_bus = event_bus
        self._active = True

    def attach(self) -> None:
        """Wire this strategy's callbacks into the shared event bus."""
        self._event_bus.subscribe(EventType.TICK, self._dispatch_tick)
        self._event_bus.subscribe(EventType.BAR, self._dispatch_bar)
        self._event_bus.subscribe(EventType.ORDER_BOOK, self._dispatch_order_book)
        self._event_bus.subscribe(EventType.EXECUTION, self._dispatch_execution)

    def pause(self) -> None:
        self._active = False

    def resume(self) -> None:
        self._active = True

    @property
    def is_paused(self) -> bool:
        return not self._active

    async def _dispatch_tick(self, event: Event) -> None:
        assert isinstance(event, TickEvent)
        if self._active and event.symbol in self.symbols:
            await self.on_tick(event)

    async def _dispatch_bar(self, event: Event) -> None:
        assert isinstance(event, BarEvent)
        if self._active and event.symbol in self.symbols:
            await self.on_bar(event)

    async def _dispatch_order_book(self, event: Event) -> None:
        assert isinstance(event, OrderBookEvent)
        if self._active and event.symbol in self.symbols:
            await self.on_order_book(event)

    async def _dispatch_execution(self, event: Event) -> None:
        assert isinstance(event, ExecutionEvent)
        if event.symbol in self.symbols:
            await self.on_execution(event)

    async def emit_signal(self, signal: SignalEvent) -> None:
        logger.info(
            "strategy.signal",
            strategy_id=self.strategy_id,
            symbol=signal.symbol,
            side=signal.side.value,
            quantity=signal.target_quantity,
            strength=signal.strength,
            correlation_id=signal.correlation_id,
        )
        await self._event_bus.publish(signal)

    async def on_tick(self, tick: TickEvent) -> None:
        """Default no-op; override in strategies driven by tick data."""

    @abc.abstractmethod
    async def on_bar(self, bar: BarEvent) -> None: ...

    async def on_order_book(self, book: OrderBookEvent) -> None:
        """Default no-op; override in strategies that need book depth."""

    async def on_execution(self, execution: ExecutionEvent) -> None:
        """Default no-op; override to react to own fills (e.g. update
        internal inventory/hedge state)."""
