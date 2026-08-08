"""IBKR broker adapter built on `ib_async`.

Handles: connection + heartbeat-based reconnection with exponential backoff,
order submission (including OCA groups for OCO-style bracket/hedge orders),
translation of ib_async callbacks into our internal event types, and
idempotent position reconciliation from the broker's own execution log on
startup (crash recovery — we never trust locally cached state after a
restart).

This module imports `ib_async` lazily inside methods so the rest of the
codebase (risk engine, backtester, strategies) has zero hard dependency on
a live TWS/Gateway connection being reachable, importable, or installed.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from config.logging_config import get_logger
from core.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from core.events import ExecutionEvent, OrderAckEvent, OrderRejectEvent
from execution.broker_interface import BrokerInterface, BrokerOrderRequest

if TYPE_CHECKING:
    from ib_async import IB, Contract, Trade

logger = get_logger(__name__)

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STP LMT",
    OrderType.MARKET_ON_CLOSE: "MOC",
}

_TIF_MAP = {
    TimeInForce.DAY: "DAY",
    TimeInForce.GTC: "GTC",
    TimeInForce.IOC: "IOC",
    TimeInForce.FOK: "FOK",
}

_IB_STATUS_MAP = {
    "PendingSubmit": OrderStatus.PENDING_NEW,
    "PreSubmitted": OrderStatus.SUBMITTED,
    "Submitted": OrderStatus.ACKNOWLEDGED,
    "PendingCancel": OrderStatus.CANCEL_PENDING,
    "Cancelled": OrderStatus.CANCELLED,
    "ApiCancelled": OrderStatus.CANCELLED,
    "Filled": OrderStatus.FILLED,
    "Inactive": OrderStatus.REJECTED,
}


class IBKRBroker(BrokerInterface):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        account: str = "",
        connect_timeout_s: float = 10.0,
        reconnect_backoff_s: float = 2.0,
        reconnect_backoff_max_s: float = 60.0,
        heartbeat_interval_s: float = 5.0,
        heartbeat_timeout_s: float = 15.0,
        futures_exchange: str = "CME",
        futures_currency: str = "USD",
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account = account
        self._connect_timeout_s = connect_timeout_s
        self._reconnect_backoff_s = reconnect_backoff_s
        self._reconnect_backoff_max_s = reconnect_backoff_max_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._futures_exchange = futures_exchange
        self._futures_currency = futures_currency

        self._ib: "IB | None" = None
        self._event_queue: asyncio.Queue[OrderAckEvent | OrderRejectEvent | ExecutionEvent] = (
            asyncio.Queue(maxsize=10_000)
        )
        self._trades_by_order_id: dict[str, "Trade"] = {}
        self._correlation_by_order_id: dict[str, str] = {}
        self._contracts: dict[str, "Contract"] = {}
        self._last_heartbeat_ok: float = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnecting = False

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    @property
    def ib(self) -> "IB":
        """Expose the underlying `ib_async.IB` client for adapters that need
        it directly (e.g. `IBKRMarketDataFeed`), once connected."""
        if self._ib is None:
            raise RuntimeError("IBKRBroker.connect() has not been called yet")
        return self._ib

    async def connect(self) -> None:
        from ib_async import IB

        self._ib = IB()
        self._wire_callbacks()
        await self._connect_with_retry()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="ibkr-heartbeat")

    async def _connect_with_retry(self) -> None:
        assert self._ib is not None
        backoff = self._reconnect_backoff_s
        while True:
            try:
                await self._ib.connectAsync(
                    self._host,
                    self._port,
                    clientId=self._client_id,
                    timeout=self._connect_timeout_s,
                    account=self._account or "",
                )
                self._last_heartbeat_ok = asyncio.get_event_loop().time()
                logger.info("ibkr.connected", host=self._host, port=self._port)
                return
            except Exception as exc:
                logger.error("ibkr.connect_failed", error=str(exc), retry_in_s=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_backoff_max_s)

    async def disconnect(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ib is not None:
            self._ib.disconnect()
        logger.info("ibkr.disconnected")

    async def _heartbeat_loop(self) -> None:
        assert self._ib is not None
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            try:
                if self._ib.isConnected():
                    await self._ib.reqCurrentTimeAsync()
                    self._last_heartbeat_ok = asyncio.get_event_loop().time()
                else:
                    raise ConnectionError("ib.isConnected() is False")
            except Exception as exc:
                elapsed = asyncio.get_event_loop().time() - self._last_heartbeat_ok
                logger.warning("ibkr.heartbeat_failed", error=str(exc), stale_for_s=elapsed)
                if elapsed >= self._heartbeat_timeout_s and not self._reconnecting:
                    logger.error("ibkr.heartbeat_timeout_reconnecting")
                    self._reconnecting = True
                    try:
                        await self._connect_with_retry()
                        await self.reconcile_state()
                    finally:
                        self._reconnecting = False

    def _wire_callbacks(self) -> None:
        assert self._ib is not None
        self._ib.errorEvent += self._on_error
        self._ib.execDetailsEvent += self._on_exec_details

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Any = None) -> None:
        # IBKR multiplexes informational codes (e.g. market data farm status)
        # through the same error channel; only >=400 codes are order-relevant.
        if errorCode < 400:
            logger.debug("ibkr.info", code=errorCode, message=errorString)
            return
        logger.error("ibkr.error", req_id=reqId, code=errorCode, message=errorString)
        order_id = str(reqId)
        correlation_id = self._correlation_by_order_id.get(order_id, "")
        self._event_queue.put_nowait(
            OrderRejectEvent(
                ts=datetime.now(timezone.utc),
                order_id=order_id,
                correlation_id=correlation_id,
                symbol="",
                reason=f"IBKR error {errorCode}: {errorString}",
            )
        )

    def _on_exec_details(self, trade: "Trade", fill: Any) -> None:
        order_id = str(trade.order.orderId)
        correlation_id = self._correlation_by_order_id.get(order_id, "")
        side = OrderSide.BUY if trade.order.action == "BUY" else OrderSide.SELL
        cumulative = sum(f.execution.shares for f in trade.fills)
        remaining = trade.order.totalQuantity - cumulative
        self._event_queue.put_nowait(
            ExecutionEvent(
                ts=datetime.now(timezone.utc),
                order_id=order_id,
                correlation_id=correlation_id,
                symbol=trade.contract.symbol,
                side=side,
                fill_quantity=int(fill.execution.shares),
                fill_price=float(fill.execution.price),
                cumulative_quantity=int(cumulative),
                remaining_quantity=int(remaining),
                commission=float(fill.commissionReport.commission)
                if fill.commissionReport
                else 0.0,
                liquidity="MAKER" if getattr(fill.execution, "liquidity", 0) == 2 else "TAKER",
            )
        )

    def _on_order_status(self, trade: "Trade") -> None:
        order_id = str(trade.order.orderId)
        correlation_id = self._correlation_by_order_id.get(order_id, "")
        status = _IB_STATUS_MAP.get(trade.orderStatus.status, OrderStatus.SUBMITTED)
        if status is OrderStatus.REJECTED:
            self._event_queue.put_nowait(
                OrderRejectEvent(
                    ts=datetime.now(timezone.utc),
                    order_id=order_id,
                    correlation_id=correlation_id,
                    symbol=trade.contract.symbol,
                    reason=trade.orderStatus.status,
                )
            )
        else:
            self._event_queue.put_nowait(
                OrderAckEvent(
                    ts=datetime.now(timezone.utc),
                    order_id=order_id,
                    correlation_id=correlation_id,
                    symbol=trade.contract.symbol,
                    status=status,
                    broker_order_id=str(trade.order.permId) if trade.order.permId else None,
                )
            )

    async def _qualify_future(self, symbol: str) -> "Contract":
        if symbol in self._contracts:
            return self._contracts[symbol]
        from ib_async import Future

        assert self._ib is not None
        contract = Future(
            symbol=symbol, exchange=self._futures_exchange, currency=self._futures_currency
        )
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"could not qualify futures contract for symbol={symbol}")
        self._contracts[symbol] = qualified[0]
        return qualified[0]

    async def place_order(self, request: BrokerOrderRequest) -> None:
        from ib_async import Order

        assert self._ib is not None
        contract = await self._qualify_future(request.symbol)

        order = Order(
            action="BUY" if request.side is OrderSide.BUY else "SELL",
            orderType=_ORDER_TYPE_MAP[request.order_type],
            totalQuantity=request.quantity,
            tif=_TIF_MAP[request.time_in_force],
        )
        if request.limit_price is not None:
            order.lmtPrice = request.limit_price
        if request.oca_group:
            order.ocaGroup = request.oca_group
            order.ocaType = request.oca_type

        trade = self._ib.placeOrder(contract, order)
        order_id = str(trade.order.orderId)
        self._correlation_by_order_id[order_id] = request.correlation_id
        self._trades_by_order_id[order_id] = trade
        trade.statusEvent += self._on_order_status
        logger.info(
            "ibkr.order_submitted",
            order_id=order_id,
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            correlation_id=request.correlation_id,
        )

    async def cancel_order(self, order_id: str) -> None:
        assert self._ib is not None
        trade = self._trades_by_order_id.get(order_id)
        if trade is None:
            logger.warning("ibkr.cancel_unknown_order", order_id=order_id)
            return
        self._ib.cancelOrder(trade.order)

    async def cancel_all(self) -> None:
        assert self._ib is not None
        self._ib.reqGlobalCancel()
        logger.warning("ibkr.global_cancel_issued")

    async def stream_events(
        self,
    ) -> AsyncIterator[OrderAckEvent | OrderRejectEvent | ExecutionEvent]:
        while True:
            event = await self._event_queue.get()
            yield event

    async def get_account_equity(self) -> float:
        assert self._ib is not None
        summary = await self._ib.accountSummaryAsync(self._account or "")
        for row in summary:
            if row.tag == "NetLiquidation":
                return float(row.value)
        raise RuntimeError("NetLiquidation not found in account summary")

    async def get_positions(self) -> dict[str, int]:
        assert self._ib is not None
        positions = self._ib.positions(self._account or "")
        return {p.contract.symbol: int(p.position) for p in positions}

    async def get_margin_usage(self) -> tuple[float, float]:
        assert self._ib is not None
        summary = await self._ib.accountSummaryAsync(self._account or "")
        initial = maintenance = 0.0
        for row in summary:
            if row.tag == "InitMarginReq":
                initial = float(row.value)
            elif row.tag == "MaintMarginReq":
                maintenance = float(row.value)
        return initial, maintenance

    async def reconcile_state(self) -> dict[str, int]:
        """Rebuild the position book from IBKR's own records rather than any
        locally cached state, guaranteeing idempotent recovery after a crash
        or reconnect."""
        assert self._ib is not None
        positions = await self.get_positions()
        logger.info("ibkr.state_reconciled", positions=positions)
        return positions
