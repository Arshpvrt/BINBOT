"""Resamples a stream of TickEvents into fixed-timeframe OHLCV BarEvents.

Aggregation is wall-clock-bucket based (ticks are assigned to the bucket
`floor(ts / timeframe_s) * timeframe_s`) so it behaves identically whether
driven by live ticks or historical tick replay in the backtester.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.events import BarEvent, TickEvent

BarCallback = Callable[[BarEvent], Awaitable[None]]


@dataclass
class _OpenBar:
    bucket_start: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    dollar_volume: float = 0.0

    def update(self, price: float, size: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.dollar_volume += price * size

    @property
    def vwap(self) -> float:
        return self.dollar_volume / self.volume if self.volume > 0 else self.close


@dataclass
class BarAggregator:
    timeframe_s: int
    _open_bars: dict[str, _OpenBar] = field(default_factory=dict)

    def _bucket_start(self, ts: datetime) -> datetime:
        epoch = ts.timestamp()
        bucket_epoch = (epoch // self.timeframe_s) * self.timeframe_s
        return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)

    def on_tick(self, tick: TickEvent) -> BarEvent | None:
        """Feed a tick in. Returns a completed BarEvent if this tick closed
        the previous bucket, else None."""
        bucket_start = self._bucket_start(tick.ts)
        price = tick.last if tick.last > 0 else tick.mid
        size = tick.last_size if tick.last_size > 0 else 0

        open_bar = self._open_bars.get(tick.symbol)
        completed: BarEvent | None = None

        if open_bar is not None and open_bar.bucket_start != bucket_start:
            completed = self._finalize(open_bar)
            open_bar = None

        if open_bar is None:
            open_bar = _OpenBar(
                bucket_start=bucket_start, symbol=tick.symbol, open=price, high=price, low=price, close=price
            )
            self._open_bars[tick.symbol] = open_bar

        open_bar.update(price, size)
        return completed

    def flush(self, symbol: str) -> BarEvent | None:
        """Force-close the current open bar (e.g. at session end)."""
        open_bar = self._open_bars.pop(symbol, None)
        if open_bar is None:
            return None
        return self._finalize(open_bar)

    def _finalize(self, open_bar: _OpenBar) -> BarEvent:
        return BarEvent(
            ts=open_bar.bucket_start,
            symbol=open_bar.symbol,
            timeframe_s=self.timeframe_s,
            open=open_bar.open,
            high=open_bar.high,
            low=open_bar.low,
            close=open_bar.close,
            volume=open_bar.volume,
            vwap=open_bar.vwap,
        )
