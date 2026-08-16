"""Tests for PositionMonitor's exit logic and its native-stop lifecycle.

The native stop is the resilience backstop from the AWS crash-loop
incident (2026-08-10): if the bot process itself is down, nothing but an
exchange-side order protects an open position. These tests exist to catch
exactly the class of bug that would silently defeat that backstop — an
arm that never fires, or a disarm that leaves a stale order behind.

Stop-loss and per-position margin are both equity-relative rather than
flat dollar amounts (2026-08-17) — the fixture's step sizes are chosen so
BTCUSDT/ETHUSDT positions land on a 50 USDT margin (matching this file's
pre-existing ROI% test values unchanged) at leverage=15, and equity=1000
with stop_loss_equity_pct=25 gives a 250 USDT stop-loss.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.enums import OrderSide
from execution.binance_broker import PositionDetail
from execution.position_monitor import PositionMonitor

LEVERAGE = 15.0
EQUITY = 1000.0
STOP_LOSS_USDT = EQUITY * 25.0 / 100.0  # 250.0

# Chosen so quantity * step_size * entry_price / LEVERAGE == 50.0 for each
# symbol's fixed (qty, entry) pair used throughout this file.
_STEP_SIZE = {
    "BTCUSDT": 50.0 * LEVERAGE / (15 * 65000.0),
    "ETHUSDT": 50.0 * LEVERAGE / (100 * 1900.0),
}


def detail(symbol: str, *, side: OrderSide, qty: int, entry: float, unrealized: float) -> PositionDetail:
    return PositionDetail(
        symbol=symbol, side=side, quantity=qty, entry_price=entry, mark_price=entry, unrealized_pnl=unrealized
    )


@pytest.fixture
def broker() -> AsyncMock:
    b = AsyncMock()
    b.place_stop_loss.return_value = "native-order-1"
    b.get_account_equity.return_value = EQUITY
    return b


@pytest.fixture
def order_manager() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def monitor(broker: AsyncMock, order_manager: AsyncMock) -> PositionMonitor:
    return PositionMonitor(
        broker,
        order_manager,
        stop_loss_equity_pct=25.0,
        leverage=LEVERAGE,
        step_size_lookup=lambda symbol: _STEP_SIZE[symbol],
        trailing_profit_arm_roi_pct=30.0,
        trailing_profit_base_roi_pct=20.0,
        trailing_profit_step_roi_pct=2.0,
        trailing_profit_step_increment_roi_pct=5.0,
        trailing_profit_hard_cap_roi_pct=80.0,
    )


class TestNativeStopArmingAndDisarming:
    async def test_new_position_arms_a_native_stop(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=0.0)
        ]

        await monitor._check_once()

        broker.place_stop_loss.assert_awaited_once_with(
            "BTCUSDT", is_long=True, entry_price=65000.0, quantity_lots=15, max_loss_usdt=STOP_LOSS_USDT
        )
        assert monitor._native_stop_order_id["BTCUSDT"] == "native-order-1"

    async def test_same_position_across_polls_arms_only_once(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]

        await monitor._check_once()
        await monitor._check_once()
        await monitor._check_once()

        assert broker.place_stop_loss.await_count == 1

    async def test_position_closing_disarms_the_native_stop(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]
        await monitor._check_once()

        broker.get_position_details.return_value = []  # position now gone (closed elsewhere)
        await monitor._check_once()

        broker.cancel_stop_loss.assert_awaited_once_with("BTCUSDT", "native-order-1")
        assert "BTCUSDT" not in monitor._native_stop_order_id

    async def test_failed_native_placement_does_not_track_a_stop(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.place_stop_loss.return_value = None  # e.g. Binance rejected it
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=0.0)
        ]

        await monitor._check_once()

        assert "BTCUSDT" not in monitor._native_stop_order_id

    async def test_short_position_arms_with_is_long_false(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.get_position_details.return_value = [
            detail("ETHUSDT", side=OrderSide.SELL, qty=100, entry=1900.0, unrealized=0.0)
        ]

        await monitor._check_once()

        broker.place_stop_loss.assert_awaited_once_with(
            "ETHUSDT", is_long=False, entry_price=1900.0, quantity_lots=100, max_loss_usdt=STOP_LOSS_USDT
        )


class TestSoftwareExitLogic:
    async def test_stop_loss_triggers_a_closing_sell_for_a_long(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=-251.0)  # past the 250 USDT stop
        ]

        await monitor._check_once()

        order_manager.submit.assert_awaited_once()
        signal = order_manager.submit.call_args.args[0]
        assert signal.symbol == "BTCUSDT"
        assert signal.side is OrderSide.SELL
        assert signal.target_quantity == 15

    async def test_a_closing_symbol_is_not_re_triggered_next_poll(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=-260.0)
        ]

        await monitor._check_once()
        await monitor._check_once()  # still shows the same losing position (fill hasn't landed yet)

        assert order_manager.submit.await_count == 1

    async def test_healthy_position_neither_closes_nor_reports_open_incorrectly(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]

        await monitor._check_once()

        order_manager.submit.assert_not_awaited()
        assert monitor.open_symbols() == {"BTCUSDT"}
        assert monitor.open_count() == 1


class TestStopLossIsSnapshotAtEntry:
    """The whole point of this design: the dollar stop-loss is computed
    ONCE, from equity at the moment a position is first seen, and must
    never move again for that position — not even if the account balance
    changes wildly on a later poll."""

    async def test_stop_loss_is_recorded_at_arm_time_from_current_equity(
        self, monitor: PositionMonitor, broker: AsyncMock
    ):
        broker.get_account_equity.return_value = 500.0  # -> 125 USDT stop-loss
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]

        await monitor._check_once()

        broker.place_stop_loss.assert_awaited_once_with(
            "BTCUSDT", is_long=True, entry_price=65000.0, quantity_lots=15, max_loss_usdt=125.0
        )
        assert monitor._position_stop_loss_usdt["BTCUSDT"] == pytest.approx(125.0)

    async def test_stop_loss_does_not_change_if_equity_changes_after_entry(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_account_equity.return_value = 1000.0  # armed at 250 USDT
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]
        await monitor._check_once()
        assert monitor._position_stop_loss_usdt["BTCUSDT"] == pytest.approx(250.0)

        # equity balloons 10x on a later poll — must NOT retroactively loosen this position's stop
        broker.get_account_equity.return_value = 10_000.0
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=-251.0)
        ]
        await monitor._check_once()

        # still closes at the ORIGINAL 250 USDT threshold, not a new 2500 USDT one
        order_manager.submit.assert_awaited_once()
        assert monitor._position_stop_loss_usdt["BTCUSDT"] == pytest.approx(250.0)

    async def test_stop_loss_tracking_is_cleared_on_close(self, monitor: PositionMonitor, broker: AsyncMock):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=5.0)
        ]
        await monitor._check_once()
        assert "BTCUSDT" in monitor._position_stop_loss_usdt

        broker.get_position_details.return_value = []
        await monitor._check_once()

        assert "BTCUSDT" not in monitor._position_stop_loss_usdt


class TestPerPositionMarginComputation:
    """ROI% is no longer against one global margin constant — each
    position's margin is derived from what Binance actually reports for
    IT (quantity * step_size * entry_price / leverage)."""

    async def test_roi_pct_uses_this_positions_own_margin_not_a_shared_constant(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        # SOLUSDT: qty=200, entry=150.0, leverage=15 -> margin=100 with step_size=0.05
        # (double the 50 USDT margin every other fixture position uses)
        monitor._step_size_lookup = lambda symbol: 0.05 if symbol == "SOLUSDT" else _STEP_SIZE[symbol]
        broker.get_position_details.return_value = [
            detail("SOLUSDT", side=OrderSide.BUY, qty=200, entry=150.0, unrealized=30.0)
        ]

        await monitor._check_once()

        # 30 unrealized / 100 margin = 30.0% ROI exactly — if this were
        # still computed against a global 50 USDT margin it would read
        # 60.0% instead, a materially different (and wrong) number.
        assert monitor._peak_roi_pct["SOLUSDT"] == pytest.approx(30.0)


class TestTrailingStopLevelFormula:
    """Pure formula checks, independent of the polling loop: base 20% once
    armed at 30% peak, +2% for every additional 5% of peak profit."""

    @pytest.mark.parametrize(
        "peak_pct,expected_level",
        [
            (30.0, 20.0),
            (34.9, 20.0),  # not yet a full 5% past arm — still the base level
            (35.0, 22.0),
            (39.9, 22.0),
            (40.0, 24.0),
            (44.9, 24.0),
            (45.0, 26.0),
        ],
    )
    def test_stop_level_steps_up_every_5pct_of_peak_profit(
        self, monitor: PositionMonitor, peak_pct: float, expected_level: float
    ):
        assert monitor._trailing_stop_level(peak_pct) == pytest.approx(expected_level)


class TestTrailingProfitExitLogic:
    """Every position here has a 50 USDT margin (via the fixture's chosen
    step sizes), so ROI% * 0.5 = unrealized USDT — e.g. 30% ROI is
    unrealized=15.0, 20% ROI is unrealized=10.0."""

    async def test_reaching_the_arm_threshold_alone_does_not_close(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=15.0)  # exactly 30% ROI
        ]

        await monitor._check_once()

        order_manager.submit.assert_not_awaited()

    async def test_pullback_below_base_trail_closes_a_long(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        # peak 32% ROI (unrealized=16.0) arms the trail at the 20% base level
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=16.0)
        ]
        await monitor._check_once()
        order_manager.submit.assert_not_awaited()

        # pulls back to 19% ROI (unrealized=9.5) — below the 20% base trail
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=9.5)
        ]
        await monitor._check_once()

        order_manager.submit.assert_awaited_once()
        signal = order_manager.submit.call_args.args[0]
        assert signal.side is OrderSide.SELL  # closes a long
        assert signal.target_quantity == 15

    async def test_pullback_below_base_trail_closes_a_short(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("ETHUSDT", side=OrderSide.SELL, qty=100, entry=1900.0, unrealized=16.0)
        ]
        await monitor._check_once()

        broker.get_position_details.return_value = [
            detail("ETHUSDT", side=OrderSide.SELL, qty=100, entry=1900.0, unrealized=9.5)
        ]
        await monitor._check_once()

        signal = order_manager.submit.call_args.args[0]
        assert signal.side is OrderSide.BUY  # closes a short
        assert signal.target_quantity == 100

    async def test_stop_level_rises_with_peak_and_still_requires_a_pullback(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        # peak reaches 40% ROI (unrealized=20.0) -> trail level rises to 24%
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=20.0)
        ]
        await monitor._check_once()

        # dips to 25% ROI (unrealized=12.5) — above the new 24% trail, must NOT close
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=12.5)
        ]
        await monitor._check_once()
        order_manager.submit.assert_not_awaited()

        # dips further to 23% ROI (unrealized=11.5) — at/below the 24% trail, closes
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=11.5)
        ]
        await monitor._check_once()
        order_manager.submit.assert_awaited_once()

    async def test_a_dip_that_never_reached_the_arm_threshold_never_closes(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        # peak only 25% ROI — never armed the trail
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=12.5)
        ]
        await monitor._check_once()

        # small pullback to 5% ROI — still well above stop-loss, and the
        # trail was never armed, so this must not trigger a close
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=2.5)
        ]
        await monitor._check_once()

        order_manager.submit.assert_not_awaited()

    async def test_peak_roi_tracking_is_cleared_when_the_position_closes(
        self, monitor: PositionMonitor, broker: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=16.0)
        ]
        await monitor._check_once()
        assert "BTCUSDT" in monitor._peak_roi_pct

        broker.get_position_details.return_value = []  # closed elsewhere (e.g. manual flatten)
        await monitor._check_once()

        assert "BTCUSDT" not in monitor._peak_roi_pct


class TestProfitHardCap:
    """Every position here has a 50 USDT margin, so 80% ROI is unrealized=40.0."""

    async def test_hard_cap_closes_immediately_without_waiting_for_a_pullback(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=40.0)  # exactly 80% ROI
        ]

        await monitor._check_once()  # closes on THIS poll — unlike trailing-profit, no second poll needed

        order_manager.submit.assert_awaited_once()
        signal = order_manager.submit.call_args.args[0]
        assert signal.side is OrderSide.SELL
        assert signal.target_quantity == 15

    async def test_hard_cap_closes_a_short_position(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("ETHUSDT", side=OrderSide.SELL, qty=100, entry=1900.0, unrealized=40.0)
        ]

        await monitor._check_once()

        signal = order_manager.submit.call_args.args[0]
        assert signal.side is OrderSide.BUY
        assert signal.target_quantity == 100

    async def test_just_below_the_cap_does_not_trigger_it(
        self, monitor: PositionMonitor, broker: AsyncMock, order_manager: AsyncMock
    ):
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=39.5)  # 79% ROI
        ]

        await monitor._check_once()

        order_manager.submit.assert_not_awaited()

    async def test_hard_cap_reason_string_is_distinct_from_trailing_profit(
        self, broker: AsyncMock, order_manager: AsyncMock
    ):
        # dashboard_bridge.py keys its Telegram/audit labeling off this
        # exact reason-string prefix (PROFIT-CAP vs TRAILING-PROFIT), so a
        # regression here would silently mislabel every cap-triggered exit.
        seen_reasons: list[str] = []
        monitor = PositionMonitor(
            broker,
            order_manager,
            stop_loss_equity_pct=25.0,
            leverage=LEVERAGE,
            step_size_lookup=lambda symbol: _STEP_SIZE[symbol],
            trailing_profit_arm_roi_pct=30.0,
            trailing_profit_base_roi_pct=20.0,
            trailing_profit_step_roi_pct=2.0,
            trailing_profit_step_increment_roi_pct=5.0,
            trailing_profit_hard_cap_roi_pct=80.0,
            on_close_event=lambda symbol, reason: seen_reasons.append(reason),
        )
        broker.get_position_details.return_value = [
            detail("BTCUSDT", side=OrderSide.BUY, qty=15, entry=65000.0, unrealized=40.0)
        ]

        await monitor._check_once()

        assert len(seen_reasons) == 1
        assert seen_reasons[0].startswith("PROFIT-CAP")
