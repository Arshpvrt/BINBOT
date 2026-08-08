"""Tracks realized slippage (fill price vs. decision-time price) per symbol
and strategy, so execution quality can be monitored live and fed back into
the backtester's market-impact calibration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from core.events import ExecutionEvent


@dataclass(frozen=True, slots=True)
class SlippageStats:
    symbol: str
    fill_count: int
    total_quantity: int
    mean_slippage: float
    worst_slippage: float
    total_slippage_cost: float


@dataclass
class SlippageTracker:
    _records: dict[str, list[tuple[int, float]]] = field(default_factory=lambda: defaultdict(list))

    def record(self, execution: ExecutionEvent) -> None:
        slippage = execution.slippage
        if slippage is None:
            return
        self._records[execution.symbol].append((execution.fill_quantity, slippage))

    def stats(self, symbol: str) -> SlippageStats | None:
        records = self._records.get(symbol)
        if not records:
            return None
        quantities = [q for q, _ in records]
        slippages = [s for _, s in records]
        total_qty = sum(quantities)
        weighted_cost = sum(q * s for q, s in records)
        return SlippageStats(
            symbol=symbol,
            fill_count=len(records),
            total_quantity=total_qty,
            mean_slippage=sum(slippages) / len(slippages),
            worst_slippage=max(slippages, key=abs),
            total_slippage_cost=weighted_cost,
        )

    def all_stats(self) -> dict[str, SlippageStats]:
        return {sym: stats for sym in self._records if (stats := self.stats(sym)) is not None}
