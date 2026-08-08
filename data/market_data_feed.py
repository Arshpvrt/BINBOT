"""Abstract market data feed. The IBKR adapter and the backtest replay feed
both implement this so strategies subscribe identically in sim and live.
"""
from __future__ import annotations

import abc


class MarketDataFeed(abc.ABC):
    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def subscribe(self, symbol: str) -> None: ...

    @abc.abstractmethod
    async def unsubscribe(self, symbol: str) -> None: ...

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...
