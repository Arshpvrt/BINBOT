from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.enums import OrderSide, OrderType, TimeInForce
from core.events import SignalEvent
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.position_sizing import kelly_position_size, target_volatility_position_size


def make_signal(symbol: str = "ES", quantity: int = 1, side: OrderSide = OrderSide.BUY) -> SignalEvent:
    return SignalEvent(
        ts=datetime.now(timezone.utc),
        symbol=symbol,
        strategy_id="test-strategy",
        side=side,
        target_quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


class TestFatFingerCheck:
    def test_order_within_limit_passes(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(quantity=5),
            account_equity=1_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert result.passed

    def test_order_exceeding_max_contracts_is_rejected(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(quantity=999),
            account_equity=1_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "FAT_FINGER_ORDER_SIZE"

    def test_non_positive_quantity_is_rejected(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(quantity=0),
            account_equity=1_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "INVALID_QUANTITY"


class TestCircuitBreakerGate:
    def test_halted_breaker_rejects_every_order(self, risk_engine, circuit_breaker):
        circuit_breaker.force_halt("manual test halt")
        result = risk_engine.check_order(
            make_signal(quantity=1),
            account_equity=1_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "CIRCUIT_BREAKER_HALTED"

    def test_drawdown_breach_trips_breaker(self, circuit_breaker):
        tripped = circuit_breaker.update(980_000.0)  # -2.0% exactly at the limit
        assert tripped
        assert circuit_breaker.is_halted

    def test_drawdown_within_limit_does_not_trip(self, circuit_breaker):
        tripped = circuit_breaker.update(985_000.0)  # -1.5%, under the 2% limit
        assert not tripped
        assert not circuit_breaker.is_halted

    def test_reset_session_clears_halt(self, circuit_breaker):
        circuit_breaker.force_halt("test")
        assert circuit_breaker.is_halted
        circuit_breaker.reset_session(1_000_000.0)
        assert not circuit_breaker.is_halted

    def test_restore_rehydrates_prior_halt(self, circuit_breaker):
        circuit_breaker.update(970_000.0)
        assert circuit_breaker.is_halted
        snapshot = circuit_breaker.snapshot()

        fresh = DrawdownCircuitBreaker(max_daily_drawdown_pct=2.0, starting_equity=1_000_000.0)
        from datetime import date

        fresh.restore(
            session_date=date.fromisoformat(snapshot["session_date"]),
            starting_equity=snapshot["starting_equity"],
            peak_equity=snapshot["peak_equity"],
            halted=snapshot["halted"],
            halt_reason=snapshot["halt_reason"],
        )
        assert fresh.is_halted
        assert fresh.halt_reason == circuit_breaker.halt_reason


class TestPositionAndNotionalLimits:
    def test_projected_position_breach_is_rejected(self, risk_engine):
        risk_engine.update_position("ES", 48)
        result = risk_engine.check_order(
            make_signal(symbol="ES", quantity=5, side=OrderSide.BUY),
            account_equity=10_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "POSITION_LIMIT_BREACH"

    def test_opposite_side_reduces_position_and_passes(self, risk_engine):
        risk_engine.update_position("ES", 48)
        result = risk_engine.check_order(
            make_signal(symbol="ES", quantity=5, side=OrderSide.SELL),
            account_equity=10_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert result.passed

    def test_notional_limit_breach_is_rejected(self, risk_engine):
        # 20 NQ contracts * 15500 * 20 multiplier = $6.2M > max_position_notional_usd ($5M)
        result = risk_engine.check_order(
            make_signal(symbol="NQ", quantity=20, side=OrderSide.BUY),
            account_equity=10_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "NOTIONAL_LIMIT_BREACH"

    def test_missing_reference_price_is_rejected(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(symbol="CL", quantity=1),
            account_equity=1_000_000.0,
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code == "NO_REFERENCE_PRICE"


class TestLeverageAndMargin:
    def test_leverage_breach_is_rejected(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(symbol="ES", quantity=1),
            account_equity=1_000.0,  # tiny equity -> any notional blows leverage
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed
        assert result.code in {"LEVERAGE_LIMIT_BREACH", "NOTIONAL_LIMIT_BREACH"}

    def test_margin_breach_is_rejected(self, risk_engine):
        result = risk_engine.check_order(
            make_signal(symbol="ES", quantity=1),
            account_equity=5_000.0,  # below buffered initial margin requirement
            gross_exposure_usd=0.0,
            current_initial_margin_used=0.0,
            current_maintenance_margin_used=0.0,
        )
        assert not result.passed


class TestOrderRateLimit:
    def test_rate_limit_trips_after_max_orders_per_second(self, risk_engine):
        results = [
            risk_engine.check_order(
                make_signal(symbol="ES", quantity=1),
                account_equity=10_000_000.0,
                gross_exposure_usd=0.0,
                current_initial_margin_used=0.0,
                current_maintenance_margin_used=0.0,
            )
            for _ in range(10)
        ]
        codes = [r.code for r in results]
        assert "ORDER_RATE_LIMIT" in codes


class TestPositionSizing:
    def test_kelly_sizing_scales_with_edge(self):
        low_edge = kelly_position_size(
            win_probability=0.52, win_loss_ratio=1.0, account_equity=1_000_000, price=4500, contract_multiplier=50
        )
        high_edge = kelly_position_size(
            win_probability=0.65, win_loss_ratio=1.5, account_equity=1_000_000, price=4500, contract_multiplier=50
        )
        assert high_edge >= low_edge

    def test_kelly_sizing_zero_when_no_edge(self):
        contracts = kelly_position_size(
            win_probability=0.4, win_loss_ratio=1.0, account_equity=1_000_000, price=4500, contract_multiplier=50
        )
        assert contracts == 0

    def test_kelly_sizing_respects_fraction_cap(self):
        uncapped = kelly_position_size(
            win_probability=0.9,
            win_loss_ratio=5.0,
            account_equity=1_000_000,
            price=100,
            contract_multiplier=1,
            kelly_fraction_cap=1.0,
        )
        capped = kelly_position_size(
            win_probability=0.9,
            win_loss_ratio=5.0,
            account_equity=1_000_000,
            price=100,
            contract_multiplier=1,
            kelly_fraction_cap=0.1,
        )
        assert capped < uncapped

    def test_target_volatility_sizing_respects_leverage_cap(self):
        contracts = target_volatility_position_size(
            account_equity=100_000,
            target_annual_volatility_pct=50.0,  # aggressive target
            instrument_annual_volatility_pct=10.0,
            price=4500,
            contract_multiplier=50,
            max_leverage=1.0,
        )
        max_by_leverage = int((100_000 * 1.0) // (4500 * 50))
        assert contracts <= max_by_leverage

    def test_sizing_functions_reject_invalid_inputs(self):
        with pytest.raises(ValueError):
            kelly_position_size(
                win_probability=1.5, win_loss_ratio=1.0, account_equity=1000, price=10, contract_multiplier=1
            )
        with pytest.raises(ValueError):
            target_volatility_position_size(
                account_equity=1000,
                target_annual_volatility_pct=10,
                instrument_annual_volatility_pct=0,
                price=10,
                contract_multiplier=1,
            )
