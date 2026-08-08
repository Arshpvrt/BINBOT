from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtest.engine import BacktestEngine
from backtest.market_simulator import MarketSimulatorBroker
from core.enums import OrderSide, OrderType, TimeInForce
from core.events import BarEvent, SignalEvent
from strategies.base_strategy import BaseStrategy

SYMBOL = "ES"
MULTIPLIER = 50.0


class BuyOnFirstBarStrategy(BaseStrategy):
    """Deterministic test strategy: emits exactly one BUY signal reacting to
    the very first bar it observes, then goes silent. Used to pin down the
    engine's no-lookahead fill semantics precisely."""

    def __init__(self, event_bus):
        super().__init__("buy-once", [SYMBOL], event_bus)
        self.bars_seen: list[BarEvent] = []
        self._fired = False

    async def on_bar(self, bar: BarEvent) -> None:
        self.bars_seen.append(bar)
        if not self._fired:
            self._fired = True
            await self.emit_signal(
                SignalEvent(
                    ts=bar.ts,
                    symbol=SYMBOL,
                    strategy_id=self.strategy_id,
                    side=OrderSide.BUY,
                    target_quantity=1,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.DAY,
                )
            )


def make_bars(closes: list[float], *, opens: list[float] | None = None) -> list[BarEvent]:
    start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    opens = opens or closes
    bars = []
    for i, (o, c) in enumerate(zip(opens, closes)):
        ts = start + timedelta(minutes=i)
        bars.append(
            BarEvent(
                ts=ts,
                symbol=SYMBOL,
                timeframe_s=60,
                open=o,
                high=max(o, c) + 0.5,
                low=min(o, c) - 0.5,
                close=c,
                volume=1000.0,
                vwap=(o + c) / 2,
            )
        )
    return bars


@pytest.mark.asyncio
async def test_signal_from_bar_t_fills_on_bar_t_plus_1_not_bar_t(risk_engine):
    """The core no-lookahead guarantee: a signal generated reacting to bar T
    must be filled using bar T+1's price data, never bar T's."""
    bars = make_bars(closes=[100.0, 200.0, 300.0], opens=[99.0, 205.0, 305.0])

    broker = MarketSimulatorBroker(starting_equity=1_000_000.0, contract_multipliers={SYMBOL: MULTIPLIER})
    engine = BacktestEngine(risk_engine=risk_engine, broker=broker, contract_multipliers={SYMBOL: MULTIPLIER})
    strategy = BuyOnFirstBarStrategy(engine.event_bus)
    strategy.attach()

    result = await engine.run(bars)

    assert len(broker.fills) == 1
    fill = broker.fills[0]
    # a market order fills near the NEXT bar's open (205.0) plus modeled
    # costs, never anywhere near bar 0's close (100.0) or open (99.0) —
    # landing near bar 0's prices would indicate lookahead.
    assert 195.0 < fill.fill_price < 215.0
    assert result.equity_curve[-1] != result.equity_curve[0]


@pytest.mark.asyncio
async def test_bar_events_are_only_routed_to_subscribed_symbols(risk_engine):
    es_bars = make_bars(closes=[100.0, 101.0])
    nq_bar = BarEvent(
        ts=datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc),
        symbol="NQ",
        timeframe_s=60,
        open=15000,
        high=15010,
        low=14990,
        close=15005,
        volume=500,
        vwap=15000,
    )
    bars = es_bars + [nq_bar]

    broker = MarketSimulatorBroker(starting_equity=1_000_000.0, contract_multipliers={SYMBOL: MULTIPLIER})
    engine = BacktestEngine(risk_engine=risk_engine, broker=broker, contract_multipliers={SYMBOL: MULTIPLIER})
    strategy = BuyOnFirstBarStrategy(engine.event_bus)
    strategy.attach()

    await engine.run(bars)

    assert all(b.symbol == SYMBOL for b in strategy.bars_seen)


@pytest.mark.asyncio
async def test_no_signal_means_no_fills_and_flat_equity(risk_engine):
    bars = make_bars(closes=[100.0, 101.0, 99.0, 102.0])
    broker = MarketSimulatorBroker(starting_equity=500_000.0, contract_multipliers={SYMBOL: MULTIPLIER})
    engine = BacktestEngine(risk_engine=risk_engine, broker=broker, contract_multipliers={SYMBOL: MULTIPLIER})

    result = await engine.run(bars)

    assert broker.fills == []
    assert all(e == pytest.approx(500_000.0) for e in result.equity_curve)
    assert result.performance.num_trades == 0


@pytest.mark.asyncio
async def test_circuit_breaker_halts_backtest_and_stops_new_fills(risk_engine, circuit_breaker):
    # force an immediate, unmissable drawdown breach on the very first bar
    circuit_breaker.max_daily_drawdown_pct = 0.0001
    bars = make_bars(closes=[100.0, 90.0, 80.0])

    broker = MarketSimulatorBroker(starting_equity=1_000_000.0, contract_multipliers={SYMBOL: MULTIPLIER})
    engine = BacktestEngine(risk_engine=risk_engine, broker=broker, contract_multipliers={SYMBOL: MULTIPLIER})
    strategy = BuyOnFirstBarStrategy(engine.event_bus)
    strategy.attach()

    result = await engine.run(bars)

    assert result.halted
    assert "daily drawdown" in result.halt_reason
