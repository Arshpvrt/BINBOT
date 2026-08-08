"""Event-driven (not vectorized) backtest engine.

Bars are replayed in strict chronological order through the SAME EventBus /
BaseStrategy / RiskEngine / OrderLifecycleManager stack used live, so
strategy code is byte-for-byte identical between backtest and production.

Lookahead is prevented structurally, not by convention: for each bar T (in
timestamp order, across all symbols), the engine first resolves any orders
still pending from decisions made at bar T-1 using bar T's OHLC
(`broker.process_bar`), and only THEN publishes bar T to strategies. A
strategy reacting to bar T can therefore only ever get filled on bar T+1 or
later — it is structurally impossible for a decision to be filled using the
same or earlier information that produced it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from config.logging_config import get_logger
from core.event_bus import EventBus
from core.events import BarEvent, Event, EventType, RiskHaltEvent, SignalEvent
from execution.order_manager import OrderLifecycleManager
from execution.slippage import SlippageStats, SlippageTracker
from backtest.market_simulator import MarketSimulatorBroker
from backtest.performance import PerformanceReport, TradeRecord, compute_performance_report
from risk.risk_engine import RiskEngine

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: list[float]
    equity_timestamps: list[datetime]
    trades: list[TradeRecord]
    performance: PerformanceReport
    slippage_stats: dict[str, SlippageStats]
    halted: bool
    halt_reason: str


class BacktestEngine:
    def __init__(
        self,
        *,
        risk_engine: RiskEngine,
        broker: MarketSimulatorBroker,
        contract_multipliers: dict[str, float] | None = None,
        bars_per_year: float = 252.0,
    ) -> None:
        self.risk_engine = risk_engine
        self.broker = broker
        self.contract_multipliers = contract_multipliers or {}
        self.bars_per_year = bars_per_year

        self.event_bus = EventBus()
        self._slippage_tracker = SlippageTracker()
        self.order_manager = OrderLifecycleManager(broker, self.event_bus, self._slippage_tracker)
        self._last_price: dict[str, float] = {}

        self.event_bus.subscribe(EventType.SIGNAL, self._on_signal)

    async def run(self, bars: list[BarEvent]) -> BacktestResult:
        if not bars:
            raise ValueError("bars must be non-empty")

        sorted_bars = sorted(bars, key=lambda b: (b.ts, b.symbol))

        await self.broker.connect()
        await self.event_bus.start()
        self.order_manager.start()

        starting_equity = await self.broker.get_account_equity()
        equity_curve: list[float] = [starting_equity]
        timestamps: list[datetime] = [sorted_bars[0].ts]
        halted = False
        halt_reason = ""
        current_session_date = sorted_bars[0].ts.date()
        self.risk_engine.circuit_breaker.reset_session(starting_equity, session_date=current_session_date)

        try:
            for bar in sorted_bars:
                # the "daily" drawdown circuit breaker resets on the
                # SIMULATED calendar day boundary, not wall-clock time —
                # otherwise every historical bar would be flagged as
                # belonging to a stale session
                if bar.ts.date() != current_session_date:
                    current_session_date = bar.ts.date()
                    day_start_equity = await self.broker.get_account_equity()
                    self.risk_engine.circuit_breaker.reset_session(
                        day_start_equity, session_date=current_session_date
                    )

                self._last_price[bar.symbol] = bar.close
                self.risk_engine.update_market_state(bar.symbol, bar.close)
                self.risk_engine.set_contract_multiplier(
                    bar.symbol, self.contract_multipliers.get(bar.symbol, 1.0)
                )

                # resolve fills for orders placed reacting to prior bars,
                # strictly BEFORE this bar's close is revealed to strategies
                self.broker.process_bar(bar)
                await asyncio.sleep(0)  # let the order-manager drain broker acks/fills

                await self.event_bus.publish(bar)
                await self.event_bus.join()

                positions = await self.broker.get_positions()
                for symbol, quantity in positions.items():
                    self.risk_engine.update_position(symbol, quantity)

                equity = await self.broker.get_account_equity()
                equity_curve.append(equity)
                timestamps.append(bar.ts)

                if self.risk_engine.circuit_breaker.update(equity, now=bar.ts):
                    halted = True
                    halt_reason = self.risk_engine.circuit_breaker.halt_reason
                    await self.event_bus.publish(
                        RiskHaltEvent(ts=bar.ts, reason=halt_reason, triggered_by="drawdown_circuit_breaker")
                    )
                    await self.event_bus.join()
                    logger.error("backtest.halted", reason=halt_reason, ts=str(bar.ts))
                    break
        finally:
            await self.order_manager.stop()
            await self.event_bus.stop()
            await self.broker.disconnect()

        performance = compute_performance_report(
            equity_curve=equity_curve, trades=self.broker.trades, bars_per_year=self.bars_per_year
        )

        return BacktestResult(
            equity_curve=equity_curve,
            equity_timestamps=timestamps,
            trades=self.broker.trades,
            performance=performance,
            slippage_stats=self._slippage_tracker.all_stats(),
            halted=halted,
            halt_reason=halt_reason,
        )

    async def _on_signal(self, event: Event) -> None:
        assert isinstance(event, SignalEvent)

        account_equity = await self.broker.get_account_equity()
        positions = await self.broker.get_positions()
        gross_exposure_usd = sum(
            abs(qty) * self._last_price.get(symbol, 0.0) * self.contract_multipliers.get(symbol, 1.0)
            for symbol, qty in positions.items()
        )
        initial_margin_used, maintenance_margin_used = await self.broker.get_margin_usage()

        result = self.risk_engine.check_order(
            event,
            account_equity=account_equity,
            gross_exposure_usd=gross_exposure_usd,
            current_initial_margin_used=initial_margin_used,
            current_maintenance_margin_used=maintenance_margin_used,
        )
        if not result.passed:
            return

        expected_price = self._last_price.get(event.symbol)
        await self.order_manager.submit(event, expected_price=expected_price)
