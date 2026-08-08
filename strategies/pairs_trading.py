"""Kalman-filtered statistical arbitrage pairs strategy (e.g. ES vs. NQ).

The Kalman filter (see `strategies.kalman`) maintains an adaptive hedge
ratio between the two legs and produces a z-scored, mean-reverting spread
on every bar. Trading logic is a simple three-state machine per pair:

    FLAT --z <= -entry_z--> LONG_SPREAD   (buy A, sell beta*B)
    FLAT --z >= +entry_z--> SHORT_SPREAD  (sell A, buy beta*B)
    LONG_SPREAD/SHORT_SPREAD -> FLAT when |z| <= exit_z (reverted) or
                                        |z| >= stop_z (blown through stop)

Cointegration must be validated OUT-OF-BAND via `strategies.cointegration`
before instantiating this strategy on a pair — this class assumes the pair
is already known to be cointegrated and only manages the live/backtest
trading state machine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto

from config.logging_config import get_logger
from core.enums import OrderSide, OrderType, TimeInForce
from core.event_bus import EventBus
from core.events import BarEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
from strategies.kalman import KalmanHedgeRatioFilter

logger = get_logger(__name__)


class PairState(Enum):
    FLAT = auto()
    LONG_SPREAD = auto()  # long symbol_a, short symbol_b
    SHORT_SPREAD = auto()  # short symbol_a, long symbol_b


@dataclass(frozen=True, slots=True)
class PairsTradingParams:
    symbol_a: str
    symbol_b: str
    base_quantity_a: int = 1
    zscore_entry: float = 2.0
    zscore_exit: float = 0.5
    zscore_stop: float = 4.0
    kalman_delta: float = 1e-4
    kalman_obs_covariance: float = 1e-3
    min_beta: float = 0.05  # guard against degenerate/near-zero hedge ratios
    warmup_bars: int = 30  # no trading until the hedge-ratio filter has seen this many bars


class KalmanPairsStrategy(BaseStrategy):
    def __init__(self, strategy_id: str, event_bus: EventBus, params: PairsTradingParams) -> None:
        super().__init__(strategy_id, [params.symbol_a, params.symbol_b], event_bus)
        self.params = params
        self._filter = KalmanHedgeRatioFilter(
            delta=params.kalman_delta, obs_covariance=params.kalman_obs_covariance
        )
        self._latest_close: dict[str, float] = {}
        self._state = PairState.FLAT
        self._last_beta: float = 1.0
        self._bars_seen: int = 0

    async def on_bar(self, bar: BarEvent) -> None:
        self._latest_close[bar.symbol] = bar.close

        price_a = self._latest_close.get(self.params.symbol_a)
        price_b = self._latest_close.get(self.params.symbol_b)
        if price_a is None or price_b is None:
            return  # wait until both legs have at least one observed price

        result = self._filter.update(y=price_a, x=price_b)
        self._last_beta = result.beta
        self._bars_seen += 1

        logger.debug(
            "pairs.kalman_update",
            symbol_a=self.params.symbol_a,
            symbol_b=self.params.symbol_b,
            beta=result.beta,
            z_score=result.z_score,
            spread=result.spread,
            state=self._state.name,
            bars_seen=self._bars_seen,
        )

        if self._bars_seen <= self.params.warmup_bars:
            if self._bars_seen == self.params.warmup_bars:
                logger.info(
                    "pairs.warmup_complete",
                    bars_seen=self._bars_seen,
                    beta=result.beta,
                    msg="hedge-ratio filter has converged enough to begin trading",
                )
            return  # filter hasn't seen enough data yet — do not trade on it

        if abs(result.beta) < self.params.min_beta:
            logger.warning("pairs.degenerate_beta_skipping", beta=result.beta)
            return

        await self._evaluate_state_machine(result.z_score, price_a, price_b, result.beta)

    async def _evaluate_state_machine(
        self, z: float, price_a: float, price_b: float, beta: float
    ) -> None:
        if self._state is PairState.FLAT:
            if z <= -self.params.zscore_entry:
                await self._enter(PairState.LONG_SPREAD, price_a, price_b, beta, z)
            elif z >= self.params.zscore_entry:
                await self._enter(PairState.SHORT_SPREAD, price_a, price_b, beta, z)
            return

        should_exit = abs(z) <= self.params.zscore_exit
        should_stop = abs(z) >= self.params.zscore_stop
        if should_exit or should_stop:
            await self._close(price_a, price_b, beta, z, stopped=should_stop)

    async def _enter(
        self, new_state: PairState, price_a: float, price_b: float, beta: float, z: float
    ) -> None:
        qty_a = self.params.base_quantity_a
        qty_b = max(round(beta * qty_a), 1)

        if new_state is PairState.LONG_SPREAD:
            side_a, side_b = OrderSide.BUY, OrderSide.SELL
        else:
            side_a, side_b = OrderSide.SELL, OrderSide.BUY

        await self._emit_leg(self.params.symbol_a, side_a, qty_a, z, "entry")
        await self._emit_leg(self.params.symbol_b, side_b, qty_b, z, "entry")
        self._state = new_state
        logger.info(
            "pairs.entered",
            state=new_state.name,
            z_score=z,
            beta=beta,
            qty_a=qty_a,
            qty_b=qty_b,
        )

    async def _close(self, price_a: float, price_b: float, beta: float, z: float, *, stopped: bool) -> None:
        qty_a = self.params.base_quantity_a
        qty_b = max(round(beta * qty_a), 1)

        # closing reverses the entry sides
        if self._state is PairState.LONG_SPREAD:
            side_a, side_b = OrderSide.SELL, OrderSide.BUY
        else:
            side_a, side_b = OrderSide.BUY, OrderSide.SELL

        reason = "stop" if stopped else "exit"
        await self._emit_leg(self.params.symbol_a, side_a, qty_a, z, reason)
        await self._emit_leg(self.params.symbol_b, side_b, qty_b, z, reason)
        logger.info("pairs.closed", prior_state=self._state.name, z_score=z, reason=reason)
        self._state = PairState.FLAT

    async def _emit_leg(self, symbol: str, side: OrderSide, quantity: int, z: float, reason: str) -> None:
        signal = SignalEvent(
            ts=datetime.now(timezone.utc),
            symbol=symbol,
            strategy_id=self.strategy_id,
            side=side,
            strength=math.tanh(z / self.params.zscore_stop),
            target_quantity=quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            metadata={"z_score": z, "reason": reason, "pair_state": self._state.name},
        )
        await self.emit_signal(signal)
