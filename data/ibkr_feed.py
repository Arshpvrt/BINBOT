"""IBKR real-time market data adapter. Streams top-of-book ticks for
subscribed futures contracts and publishes `TickEvent`s onto the shared
EventBus, from which the bar aggregator and strategies both consume.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config.logging_config import get_logger
from core.event_bus import EventBus
from core.events import TickEvent

if TYPE_CHECKING:
    from ib_async import IB, Contract, Ticker

from data.market_data_feed import MarketDataFeed

logger = get_logger(__name__)


class IBKRMarketDataFeed(MarketDataFeed):
    def __init__(
        self,
        ib: "IB",
        event_bus: EventBus,
        *,
        futures_exchange: str = "CME",
        futures_currency: str = "USD",
    ) -> None:
        self._ib = ib
        self._event_bus = event_bus
        self._futures_exchange = futures_exchange
        self._futures_currency = futures_currency
        self._contracts: dict[str, "Contract"] = {}
        self._tickers: dict[str, "Ticker"] = {}

    @property
    def is_connected(self) -> bool:
        return self._ib.isConnected()

    async def connect(self) -> None:
        if not self._ib.isConnected():
            raise ConnectionError("underlying IB client is not connected; connect IBKRBroker first")
        logger.info("ibkr_feed.ready")

    async def disconnect(self) -> None:
        for symbol in list(self._tickers):
            await self.unsubscribe(symbol)

    async def subscribe(self, symbol: str) -> None:
        from ib_async import Future

        if symbol in self._tickers:
            return
        contract = Future(
            symbol=symbol, exchange=self._futures_exchange, currency=self._futures_currency
        )
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"could not qualify futures contract for symbol={symbol}")
        contract = qualified[0]
        self._contracts[symbol] = contract

        ticker = self._ib.reqMktData(contract, "", False, False)
        ticker.updateEvent += self._make_handler(symbol)
        self._tickers[symbol] = ticker
        logger.info("ibkr_feed.subscribed", symbol=symbol)

    async def unsubscribe(self, symbol: str) -> None:
        contract = self._contracts.pop(symbol, None)
        self._tickers.pop(symbol, None)
        if contract is not None:
            self._ib.cancelMktData(contract)
            logger.info("ibkr_feed.unsubscribed", symbol=symbol)

    def _make_handler(self, symbol: str):
        def _on_update(ticker: "Ticker") -> None:
            event = TickEvent(
                ts=datetime.now(timezone.utc),
                symbol=symbol,
                bid=ticker.bid or 0.0,
                ask=ticker.ask or 0.0,
                bid_size=int(ticker.bidSize or 0),
                ask_size=int(ticker.askSize or 0),
                last=ticker.last or 0.0,
                last_size=int(ticker.lastSize or 0),
            )
            # ib_async invokes this callback synchronously from its own event
            # loop step; hop back through the bus's async publish via a task
            # so ordering/backpressure semantics stay identical to every
            # other publisher (including the backtest replay feed).
            asyncio.create_task(self._event_bus.publish(event))

        return _on_update
