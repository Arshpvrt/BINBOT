"""Institutional pre-trade risk engine.

Every order-intent MUST pass through `RiskEngine.check_order()` before it is
allowed to reach the execution layer. Checks fail fast (first violation wins)
and are ordered cheapest/most-critical first so a halted system never pays
the cost of margin/position math. A single violation halts nothing by itself
(it just rejects that order) EXCEPT drawdown-breach and forced halts, which
flip the circuit breaker and reject all subsequent orders until reset.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from config.logging_config import get_logger
from core.enums import OrderSide
from core.events import SignalEvent
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.margin import MarginCalculator

logger = get_logger(__name__)


class RiskViolation(Exception):
    """Raised internally to short-circuit the check chain; never escapes
    `RiskEngine.check_order`, which converts it into a `RiskCheckResult`."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    passed: bool
    code: str
    message: str
    approved_quantity: int = 0


@dataclass
class _TokenRateLimiter:
    """Sliding-window order-rate limiter (fat-finger / runaway-loop guard)."""

    max_per_second: float
    max_per_minute: int
    _second_window: deque[float] = field(default_factory=deque)
    _minute_window: deque[float] = field(default_factory=deque)

    def try_acquire(self, *, now: float | None = None) -> tuple[bool, str]:
        now = now if now is not None else time.monotonic()

        while self._second_window and now - self._second_window[0] > 1.0:
            self._second_window.popleft()
        while self._minute_window and now - self._minute_window[0] > 60.0:
            self._minute_window.popleft()

        if len(self._second_window) >= self.max_per_second:
            return False, f"order rate exceeded: {len(self._second_window)}/s >= {self.max_per_second}/s"
        if len(self._minute_window) >= self.max_per_minute:
            return False, (
                f"order rate exceeded: {len(self._minute_window)}/min >= {self.max_per_minute}/min"
            )

        self._second_window.append(now)
        self._minute_window.append(now)
        return True, "ok"


@dataclass
class RiskEngine:
    max_contracts_per_order: int
    max_contracts_per_symbol: int
    max_position_notional_usd: float
    max_orders_per_second: float
    max_orders_per_minute: int
    max_gross_leverage: float
    circuit_breaker: DrawdownCircuitBreaker
    margin_calculator: MarginCalculator

    _rate_limiter: _TokenRateLimiter = field(init=False)
    _positions: dict[str, int] = field(default_factory=dict)
    _prices: dict[str, float] = field(default_factory=dict)
    _contract_multipliers: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rate_limiter = _TokenRateLimiter(
            max_per_second=self.max_orders_per_second,
            max_per_minute=self.max_orders_per_minute,
        )

    def update_market_state(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def update_position(self, symbol: str, quantity: int) -> None:
        self._positions[symbol] = quantity

    def set_contract_multiplier(self, symbol: str, multiplier: float) -> None:
        self._contract_multipliers[symbol] = multiplier

    def check_order(
        self,
        signal: SignalEvent,
        *,
        account_equity: float,
        gross_exposure_usd: float,
        current_initial_margin_used: float,
        current_maintenance_margin_used: float,
    ) -> RiskCheckResult:
        try:
            self._check_circuit_breaker()
            self._check_fat_finger(signal)
            self._check_rate_limit()
            self._check_position_limits(signal)
            notional = self._check_notional_limit(signal)
            self._check_leverage(gross_exposure_usd, notional, account_equity)
            self._check_margin(
                signal,
                account_equity=account_equity,
                current_initial_margin_used=current_initial_margin_used,
                current_maintenance_margin_used=current_maintenance_margin_used,
            )
        except RiskViolation as violation:
            logger.warning(
                "risk_engine.order_rejected",
                code=violation.code,
                reason=violation.message,
                symbol=signal.symbol,
                correlation_id=signal.correlation_id,
            )
            return RiskCheckResult(passed=False, code=violation.code, message=violation.message)

        logger.info(
            "risk_engine.order_approved",
            symbol=signal.symbol,
            quantity=signal.target_quantity,
            correlation_id=signal.correlation_id,
        )
        return RiskCheckResult(
            passed=True,
            code="OK",
            message="all pre-trade checks passed",
            approved_quantity=signal.target_quantity,
        )

    def _check_circuit_breaker(self) -> None:
        if self.circuit_breaker.is_halted:
            raise RiskViolation("CIRCUIT_BREAKER_HALTED", self.circuit_breaker.halt_reason)

    def _check_fat_finger(self, signal: SignalEvent) -> None:
        qty = abs(signal.target_quantity)
        if qty <= 0:
            raise RiskViolation("INVALID_QUANTITY", f"non-positive quantity {qty}")
        if qty > self.max_contracts_per_order:
            raise RiskViolation(
                "FAT_FINGER_ORDER_SIZE",
                f"order size {qty} exceeds max_contracts_per_order={self.max_contracts_per_order}",
            )

    def _check_rate_limit(self) -> None:
        ok, reason = self._rate_limiter.try_acquire()
        if not ok:
            raise RiskViolation("ORDER_RATE_LIMIT", reason)

    def _check_position_limits(self, signal: SignalEvent) -> None:
        current = self._positions.get(signal.symbol, 0)
        signed_qty = signal.target_quantity * signal.side.sign
        projected = abs(current + signed_qty)
        if projected > self.max_contracts_per_symbol:
            raise RiskViolation(
                "POSITION_LIMIT_BREACH",
                f"projected position {projected} for {signal.symbol} exceeds "
                f"max_contracts_per_symbol={self.max_contracts_per_symbol}",
            )

    def _check_notional_limit(self, signal: SignalEvent) -> float:
        price = self._prices.get(signal.symbol)
        multiplier = self._contract_multipliers.get(signal.symbol, 1.0)
        if price is None or price <= 0:
            raise RiskViolation("NO_REFERENCE_PRICE", f"no reference price for {signal.symbol}")
        notional = abs(signal.target_quantity) * price * multiplier
        if notional > self.max_position_notional_usd:
            raise RiskViolation(
                "NOTIONAL_LIMIT_BREACH",
                f"order notional {notional:.2f} exceeds "
                f"max_position_notional_usd={self.max_position_notional_usd:.2f}",
            )
        return notional

    def _check_leverage(
        self, gross_exposure_usd: float, incremental_notional: float, account_equity: float
    ) -> None:
        if account_equity <= 0:
            raise RiskViolation("NO_EQUITY", "account_equity must be positive")
        projected_gross = gross_exposure_usd + incremental_notional
        leverage = projected_gross / account_equity
        if leverage > self.max_gross_leverage:
            raise RiskViolation(
                "LEVERAGE_LIMIT_BREACH",
                f"projected gross leverage {leverage:.2f}x exceeds "
                f"max_gross_leverage={self.max_gross_leverage:.2f}x",
            )

    def _check_margin(
        self,
        signal: SignalEvent,
        *,
        account_equity: float,
        current_initial_margin_used: float,
        current_maintenance_margin_used: float,
    ) -> None:
        current_position = self._positions.get(signal.symbol, 0)
        signed_qty = signal.target_quantity * signal.side.sign
        result = self.margin_calculator.check_order(
            symbol=signal.symbol,
            additional_contracts=signed_qty,
            current_position_contracts=current_position,
            net_liquidation_value=account_equity,
            current_initial_margin_used=current_initial_margin_used,
            current_maintenance_margin_used=current_maintenance_margin_used,
        )
        if not result.passed:
            raise RiskViolation("MARGIN_BREACH", result.reason)
