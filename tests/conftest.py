from __future__ import annotations

from datetime import datetime, timezone

import pytest

from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.margin import MarginCalculator, MarginRequirement
from risk.risk_engine import RiskEngine


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def circuit_breaker() -> DrawdownCircuitBreaker:
    return DrawdownCircuitBreaker(max_daily_drawdown_pct=2.0, starting_equity=1_000_000.0)


@pytest.fixture
def margin_calculator() -> MarginCalculator:
    calc = MarginCalculator(initial_margin_buffer_pct=25.0, maintenance_margin_buffer_pct=10.0)
    calc.set_requirement(
        MarginRequirement(symbol="ES", initial_margin_per_contract=12_000.0, maintenance_margin_per_contract=11_000.0)
    )
    calc.set_requirement(
        MarginRequirement(symbol="NQ", initial_margin_per_contract=17_000.0, maintenance_margin_per_contract=15_000.0)
    )
    return calc


@pytest.fixture
def risk_engine(circuit_breaker: DrawdownCircuitBreaker, margin_calculator: MarginCalculator) -> RiskEngine:
    engine = RiskEngine(
        max_contracts_per_order=20,
        max_contracts_per_symbol=50,
        max_position_notional_usd=5_000_000.0,
        max_orders_per_second=5.0,
        max_orders_per_minute=60,
        max_gross_leverage=4.0,
        circuit_breaker=circuit_breaker,
        margin_calculator=margin_calculator,
    )
    engine.update_market_state("ES", 4500.0)
    engine.update_market_state("NQ", 15500.0)
    engine.set_contract_multiplier("ES", 50.0)
    engine.set_contract_multiplier("NQ", 20.0)
    return engine
