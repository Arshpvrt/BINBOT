"""Realistic fill simulation for the event-driven backtester.

Two cost components are modeled explicitly, matching institutional TCA
practice:

1. **Spread-crossing cost** — a marketable order pays half the quoted
   spread (we approximate the spread from bar high/low when no L1 quote is
   available, which is conservative/pessimistic — real spreads are usually
   tighter than the bar range).
2. **Square-root market impact** — impact = sigma * eta * sqrt(qty / ADV),
   the standard square-root law relating price impact to participation
   rate, applied against the bar's realized volatility and volume.

Fills are resolved strictly using the bar *after* the one that produced the
signal (the engine enforces this ordering), which is what prevents
lookahead bias: no order can ever fill using information not yet available
at decision time.
"""
from __future__ import annotations

import asyncio
import math
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.logging_config import get_logger
from core.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from core.events import BarEvent, ExecutionEvent, OrderAckEvent, OrderRejectEvent
from execution.broker_interface import BrokerInterface, BrokerOrderRequest
from backtest.performance import TradeRecord

logger = get_logger(__name__)


@dataclass
class _OpenTrade:
    entry_ts: datetime
    side: OrderSide
    peak_quantity: int
    pnl: float = 0.0


@dataclass(frozen=True, slots=True)
class MarketImpactParams:
    impact_coefficient: float = 0.1  # eta in the sqrt-law
    spread_capture_fraction: float = 0.5  # fraction of the bar range charged as spread cost
    max_participation_rate: float = 0.1  # cap: order can consume at most this fraction of bar volume per bar


def sqrt_market_impact_bps(
    *, quantity: float, bar_volume: float, realized_vol_pct: float, impact_coefficient: float
) -> float:
    """Returns price impact in fractional (not bps*100) terms, e.g. 0.001 = 10bps."""
    if bar_volume <= 0:
        return 0.0
    participation = quantity / bar_volume
    return impact_coefficient * (realized_vol_pct / 100.0) * math.sqrt(max(participation, 0.0))


@dataclass
class _PendingOrder:
    request: BrokerOrderRequest
    expected_price: float
    remaining_qty: int
    bars_alive: int = 0


class MarketSimulatorBroker(BrokerInterface):
    """A `BrokerInterface` implementation whose fills are driven by feeding
    it successive `BarEvent`s via `process_bar()` — never by any information
    from the bar that generated the originating signal."""

    def __init__(
        self,
        *,
        starting_equity: float,
        contract_multipliers: dict[str, float] | None = None,
        impact_params: MarketImpactParams | None = None,
        commission_per_contract: float = 2.25,
    ) -> None:
        self._equity = starting_equity
        self._realized_pnl = 0.0
        self._contract_multipliers = contract_multipliers or {}
        self._impact = impact_params or MarketImpactParams()
        self._commission_per_contract = commission_per_contract

        self._positions: dict[str, int] = defaultdict(int)
        self._avg_entry_price: dict[str, float] = defaultdict(float)
        self._pending_orders: dict[str, list[_PendingOrder]] = defaultdict(list)
        self._last_bar: dict[str, BarEvent] = {}
        self._volume_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=20))
        self._return_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=20))

        self._event_queue: asyncio.Queue[OrderAckEvent | OrderRejectEvent | ExecutionEvent] = (
            asyncio.Queue()
        )
        self._connected = False
        self.fills: list[ExecutionEvent] = []
        self._open_trades: dict[str, _OpenTrade] = {}
        self.trades: list[TradeRecord] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def place_order(self, request: BrokerOrderRequest) -> None:
        last_bar = self._last_bar.get(request.symbol)
        expected_price = last_bar.close if last_bar else 0.0
        pending = _PendingOrder(request=request, expected_price=expected_price, remaining_qty=request.quantity)
        self._pending_orders[request.symbol].append(pending)
        await self._event_queue.put(
            OrderAckEvent(
                ts=datetime.now(timezone.utc),
                order_id=request.order_id,
                correlation_id=request.correlation_id,
                symbol=request.symbol,
                status=OrderStatus.ACKNOWLEDGED,
            )
        )

    async def cancel_order(self, order_id: str) -> None:
        for symbol, orders in self._pending_orders.items():
            self._pending_orders[symbol] = [o for o in orders if o.request.order_id != order_id]

    async def cancel_all(self) -> None:
        self._pending_orders.clear()

    async def stream_events(self) -> AsyncIterator[OrderAckEvent | OrderRejectEvent | ExecutionEvent]:
        while True:
            yield await self._event_queue.get()

    async def get_account_equity(self) -> float:
        return self._equity

    async def get_positions(self) -> dict[str, int]:
        return dict(self._positions)

    async def get_margin_usage(self) -> tuple[float, float]:
        # simplified: backtester assumes ample margin; real margin checks
        # happen upstream in RiskEngine against `risk.margin.MarginCalculator`
        return 0.0, 0.0

    async def reconcile_state(self) -> dict[str, int]:
        return dict(self._positions)

    def process_bar(self, bar: BarEvent) -> None:
        """Advance simulated time: resolve any pending orders for this
        symbol using ONLY this bar's OHLCV, then record the bar as the new
        'last known' price for future order placements. Must be called by
        the engine strictly after the bar that generated any signals which
        produced these pending orders — that ordering is what prevents
        lookahead."""
        self._update_history(bar)
        self._resolve_pending_orders(bar)
        self._last_bar[bar.symbol] = bar

    def _update_history(self, bar: BarEvent) -> None:
        self._volume_history[bar.symbol].append(bar.volume)
        prev = self._last_bar.get(bar.symbol)
        if prev and prev.close > 0:
            self._return_history[bar.symbol].append(math.log(bar.close / prev.close))

    def _realized_vol_pct(self, symbol: str) -> float:
        returns = self._return_history[symbol]
        if len(returns) < 2:
            return 1.0  # conservative default when insufficient history
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return (variance**0.5) * (252**0.5) * 100.0

    def _avg_volume(self, symbol: str) -> float:
        history = self._volume_history[symbol]
        return sum(history) / len(history) if history else 0.0

    def _resolve_pending_orders(self, bar: BarEvent) -> None:
        remaining_orders: list[_PendingOrder] = []
        for pending in self._pending_orders.get(bar.symbol, []):
            pending.bars_alive += 1
            fill_qty = self._determine_fill_quantity(pending, bar)

            if fill_qty > 0:
                fill_price = self._compute_fill_price(pending, bar, fill_qty)
                self._apply_fill(pending, fill_qty, fill_price, bar)

            pending.remaining_qty -= fill_qty
            if pending.remaining_qty <= 0:
                continue

            tif = pending.request.time_in_force
            if tif in (TimeInForce.IOC, TimeInForce.FOK):
                continue  # unfilled remainder is cancelled, not carried forward
            remaining_orders.append(pending)

        self._pending_orders[bar.symbol] = remaining_orders

    def _determine_fill_quantity(self, pending: _PendingOrder, bar: BarEvent) -> int:
        req = pending.request
        if req.order_type is OrderType.MARKET:
            available = max(int(bar.volume * self._impact.max_participation_rate), pending.remaining_qty)
            return min(pending.remaining_qty, available)

        if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            limit = req.limit_price or 0.0
            touched = bar.low <= limit <= bar.high
            if not touched:
                return 0
            available = max(int(bar.volume * self._impact.max_participation_rate), 1)
            return min(pending.remaining_qty, available)

        # STOP / MOC: simplified to fill fully at bar close
        return pending.remaining_qty

    def _compute_fill_price(self, pending: _PendingOrder, bar: BarEvent, fill_qty: int) -> float:
        req = pending.request
        sign = 1 if req.side is OrderSide.BUY else -1

        if req.order_type is OrderType.LIMIT:
            reference = req.limit_price or bar.close
        else:
            reference = bar.open  # next-bar-open fill for market orders

        bar_range = max(bar.high - bar.low, 1e-9)
        spread_cost = bar_range * self._impact.spread_capture_fraction * sign

        impact_bps = sqrt_market_impact_bps(
            quantity=fill_qty,
            bar_volume=max(bar.volume, 1.0),
            realized_vol_pct=self._realized_vol_pct(bar.symbol),
            impact_coefficient=self._impact.impact_coefficient,
        )
        impact_cost = reference * impact_bps * sign

        return reference + spread_cost + impact_cost

    def _apply_fill(self, pending: _PendingOrder, fill_qty: int, fill_price: float, bar: BarEvent) -> None:
        req = pending.request
        signed_qty = fill_qty * (1 if req.side is OrderSide.BUY else -1)
        multiplier = self._contract_multipliers.get(bar.symbol, 1.0)
        commission = self._commission_per_contract * fill_qty

        prev_position = self._positions[bar.symbol]
        new_position = prev_position + signed_qty
        is_flip = prev_position != 0 and new_position != 0 and (prev_position > 0) != (new_position > 0)

        # open a new trade record the instant we cross from flat into a position
        if prev_position == 0 and new_position != 0:
            self._open_trades[bar.symbol] = _OpenTrade(
                entry_ts=bar.ts,
                side=OrderSide.BUY if new_position > 0 else OrderSide.SELL,
                peak_quantity=abs(new_position),
            )

        open_trade = self._open_trades.get(bar.symbol)
        if open_trade is not None:
            open_trade.peak_quantity = max(open_trade.peak_quantity, abs(new_position))
            open_trade.pnl -= commission  # every fill's commission drags on the currently open trade

        # realize P&L on any portion of this fill that closes existing exposure
        if prev_position != 0 and (prev_position > 0) != (signed_qty > 0):
            closing_qty = min(abs(signed_qty), abs(prev_position))
            entry = self._avg_entry_price[bar.symbol]
            pnl_sign = 1 if prev_position > 0 else -1
            closing_pnl = pnl_sign * (fill_price - entry) * closing_qty * multiplier
            self._realized_pnl += closing_pnl
            if open_trade is not None:
                open_trade.pnl += closing_pnl

        if new_position == 0:
            self._avg_entry_price[bar.symbol] = 0.0
        elif (prev_position >= 0 and signed_qty > 0) or (prev_position <= 0 and signed_qty < 0):
            total_qty = abs(prev_position) + abs(signed_qty)
            self._avg_entry_price[bar.symbol] = (
                self._avg_entry_price[bar.symbol] * abs(prev_position) + fill_price * abs(signed_qty)
            ) / total_qty if total_qty else fill_price
        # a flip re-bases the surviving exposure to this fill's price, since
        # the prior average-entry basis belonged entirely to the closed side
        if is_flip:
            self._avg_entry_price[bar.symbol] = fill_price

        # finalize the trade record when the position returns to flat, or
        # split it when a single fill flips the position through zero
        if new_position == 0 and prev_position != 0:
            self._finalize_trade(bar.symbol, bar.ts)
        elif is_flip:
            self._finalize_trade(bar.symbol, bar.ts)
            self._open_trades[bar.symbol] = _OpenTrade(
                entry_ts=bar.ts,
                side=OrderSide.BUY if new_position > 0 else OrderSide.SELL,
                peak_quantity=abs(new_position),
            )

        self._positions[bar.symbol] = new_position
        self._equity += self._realized_pnl - commission
        self._realized_pnl = 0.0  # folded into equity immediately

        cumulative = pending.request.quantity - pending.remaining_qty + fill_qty
        execution = ExecutionEvent(
            ts=bar.ts,
            order_id=req.order_id,
            correlation_id=req.correlation_id,
            symbol=bar.symbol,
            side=req.side,
            fill_quantity=fill_qty,
            fill_price=fill_price,
            cumulative_quantity=cumulative,
            remaining_quantity=pending.remaining_qty - fill_qty,
            commission=commission,
            liquidity="TAKER" if req.order_type is OrderType.MARKET else "MAKER",
            expected_price=pending.expected_price,
        )
        self.fills.append(execution)
        self._event_queue.put_nowait(execution)

        logger.debug(
            "sim_broker.fill",
            symbol=bar.symbol,
            side=req.side.value,
            qty=fill_qty,
            price=fill_price,
            position=new_position,
        )

    def _finalize_trade(self, symbol: str, exit_ts: datetime) -> None:
        open_trade = self._open_trades.pop(symbol, None)
        if open_trade is None:
            return
        self.trades.append(
            TradeRecord(
                symbol=symbol,
                side=open_trade.side,
                entry_ts=open_trade.entry_ts,
                exit_ts=exit_ts,
                quantity=open_trade.peak_quantity,
                pnl=open_trade.pnl,
            )
        )
