"""Binance USDT-M Futures market data adapter.

Streams top-of-book (best bid/ask) for subscribed symbols via Binance's
`bookTicker` websocket and publishes `TickEvent`s onto the shared
EventBus — from there the existing `BarAggregator` and strategies consume
it exactly as they do for the IBKR feed, with zero changes to that code.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config.logging_config import get_logger
from core.event_bus import EventBus
from core.events import TickEvent
from data.market_data_feed import MarketDataFeed

if TYPE_CHECKING:
    from binance.ws.streams import BinanceSocketManager

logger = get_logger(__name__)


class BinanceFuturesMarketDataFeed(MarketDataFeed):
    def __init__(
        self,
        socket_manager: "BinanceSocketManager",
        event_bus: EventBus,
        *,
        reconnect_backoff_s: float = 2.0,
        reconnect_backoff_max_s: float = 60.0,
    ) -> None:
        self._bsm = socket_manager
        self._event_bus = event_bus
        self._reconnect_backoff_s = reconnect_backoff_s
        self._reconnect_backoff_max_s = reconnect_backoff_max_s
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        logger.info("binance_feed.ready")

    async def disconnect(self) -> None:
        self._connected = False
        for symbol in list(self._tasks):
            await self.unsubscribe(symbol)

    async def subscribe(self, symbol: str) -> None:
        if symbol in self._tasks:
            return
        task = asyncio.create_task(self._stream_symbol(symbol), name=f"binance-feed-{symbol}")
        self._tasks[symbol] = task
        logger.info("binance_feed.subscribed", symbol=symbol)

    async def unsubscribe(self, symbol: str) -> None:
        task = self._tasks.pop(symbol, None)
        if task is not None:
            task.cancel()
            logger.info("binance_feed.unsubscribed", symbol=symbol)

    async def _stream_symbol(self, symbol: str) -> None:
        backoff = self._reconnect_backoff_s
        while self._connected:
            try:
                async with self._bsm.symbol_ticker_futures_socket(symbol=symbol) as stream:
                    backoff = self._reconnect_backoff_s
                    while self._connected:
                        msg = await stream.recv()
                        await self._handle_book_ticker(symbol, msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "binance_feed.stream_error", symbol=symbol, error=str(exc), retry_in_s=backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_backoff_max_s)

    async def _handle_book_ticker(self, symbol: str, msg: dict) -> None:
        # python-binance wraps every socket message in a {"stream", "data"}
        # envelope, even for a single (non-multiplexed) stream — unwrap it.
        payload = msg.get("data", msg)
        try:
            bid = float(payload["b"])
            ask = float(payload["a"])
            bid_size = float(payload["B"])
            ask_size = float(payload["A"])
        except (KeyError, ValueError, TypeError):
            logger.warning("binance_feed.malformed_message", symbol=symbol, msg=msg)
            return

        event = TickEvent(
            ts=datetime.now(timezone.utc),
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_size=round(bid_size),
            ask_size=round(ask_size),
            last=(bid + ask) / 2.0,
            last_size=0,
        )
        await self._event_bus.publish(event)
