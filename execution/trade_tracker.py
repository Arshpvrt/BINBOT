"""Reconstructs round-trip trades (entry -> exit) from the live
`ExecutionEvent` stream — the same fills already flowing through
`OrderLifecycleManager`/`DashboardBridge` — so the dashboard can show an
open/closed trade history without any extra API calls.

Deliberately simpler than `backtest/market_simulator.py`'s trade tracking:
it handles the shapes these strategies actually produce (open fully, then
close fully — possibly across several partial fills), not exotic
same-fill position flips, which none of our live strategies generate
(the position monitor always closes a position to flat before any new
entry is considered for that symbol).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from core.enums import OrderSide
from core.events import ExecutionEvent


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    symbol: str
    side: OrderSide  # direction of the opening leg
    entry_ts: datetime
    exit_ts: datetime
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float  # net of commission

    @property
    def duration_seconds(self) -> float:
        return (self.exit_ts - self.entry_ts).total_seconds()


@dataclass
class _OpenTrade:
    entry_ts: datetime
    side: OrderSide
    quantity: int  # current abs quantity held
    avg_entry_price: float
    exit_notional: float = 0.0  # running (price * qty) of closing fills, for a qty-weighted exit price
    exit_quantity: float = 0.0
    realized_pnl: float = 0.0


class TradeTracker:
    def __init__(self, contract_multiplier_lookup: Callable[[str], float]) -> None:
        self._multiplier_lookup = contract_multiplier_lookup
        self._open: dict[str, _OpenTrade] = {}
        self.closed_trades: list[ClosedTrade] = []

    def open_trades(self) -> dict[str, _OpenTrade]:
        return dict(self._open)

    def on_execution(self, event: ExecutionEvent) -> ClosedTrade | None:
        multiplier = self._multiplier_lookup(event.symbol)
        signed_fill = event.fill_quantity * (1 if event.side is OrderSide.BUY else -1)

        trade = self._open.get(event.symbol)
        if trade is None:
            if signed_fill == 0:
                return None
            self._open[event.symbol] = _OpenTrade(
                entry_ts=event.ts,
                side=OrderSide.BUY if signed_fill > 0 else OrderSide.SELL,
                quantity=abs(signed_fill),
                avg_entry_price=event.fill_price,
                realized_pnl=-event.commission,
            )
            return None

        prev_signed = trade.quantity * (1 if trade.side is OrderSide.BUY else -1)
        is_closing_direction = (signed_fill > 0) != (prev_signed > 0)

        if not is_closing_direction:
            # scaling into the same side: extend the average entry price
            total_qty = trade.quantity + abs(signed_fill)
            trade.avg_entry_price = (
                trade.avg_entry_price * trade.quantity + event.fill_price * abs(signed_fill)
            ) / total_qty
            trade.quantity = total_qty
            trade.realized_pnl -= event.commission
            return None

        # closing fill (full or partial)
        closing_qty = min(abs(signed_fill), trade.quantity)
        pnl_sign = 1 if trade.side is OrderSide.BUY else -1
        trade.realized_pnl += pnl_sign * (event.fill_price - trade.avg_entry_price) * closing_qty * multiplier
        trade.realized_pnl -= event.commission
        trade.exit_notional += event.fill_price * closing_qty
        trade.exit_quantity += closing_qty
        trade.quantity -= closing_qty

        if trade.quantity > 0:
            return None  # partially closed, still open

        del self._open[event.symbol]
        record = ClosedTrade(
            symbol=event.symbol,
            side=trade.side,
            entry_ts=trade.entry_ts,
            exit_ts=event.ts,
            quantity=int(trade.exit_quantity),
            entry_price=trade.avg_entry_price,
            exit_price=trade.exit_notional / trade.exit_quantity if trade.exit_quantity else event.fill_price,
            pnl=trade.realized_pnl,
        )
        self.closed_trades.append(record)
        return record
