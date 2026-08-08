from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.enums import OrderSide
from core.events import ExecutionEvent
from execution.trade_tracker import TradeTracker

MULTIPLIER = 1.0  # crypto lot->unit multiplier is folded into fill_quantity for these tests


def multiplier_lookup(symbol: str) -> float:
    return MULTIPLIER


def make_fill(
    *, symbol="BTCUSDT", side=OrderSide.BUY, qty=1, price=100.0, commission=0.0, ts=None, cumulative=None
) -> ExecutionEvent:
    ts = ts or datetime.now(timezone.utc)
    return ExecutionEvent(
        ts=ts,
        order_id="o1",
        correlation_id="c1",
        symbol=symbol,
        side=side,
        fill_quantity=qty,
        fill_price=price,
        cumulative_quantity=cumulative if cumulative is not None else qty,
        remaining_quantity=0,
        commission=commission,
    )


class TestTradeTracker:
    def test_no_trade_open_after_single_entry_fill(self):
        tracker = TradeTracker(multiplier_lookup)
        result = tracker.on_execution(make_fill(side=OrderSide.BUY, qty=1, price=100.0))
        assert result is None
        assert "BTCUSDT" in tracker.open_trades()

    def test_full_round_trip_profit(self):
        tracker = TradeTracker(multiplier_lookup)
        start = datetime.now(timezone.utc)
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=1, price=100.0, ts=start))
        closed = tracker.on_execution(
            make_fill(side=OrderSide.SELL, qty=1, price=115.0, ts=start + timedelta(minutes=5))
        )
        assert closed is not None
        assert closed.pnl == pytest.approx(15.0)
        assert closed.entry_price == pytest.approx(100.0)
        assert closed.exit_price == pytest.approx(115.0)
        assert closed.side is OrderSide.BUY
        assert closed.duration_seconds == pytest.approx(300.0)
        assert "BTCUSDT" not in tracker.open_trades()
        assert tracker.closed_trades == [closed]

    def test_full_round_trip_loss(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(side=OrderSide.SELL, qty=2, price=100.0))
        closed = tracker.on_execution(make_fill(side=OrderSide.BUY, qty=2, price=110.0))
        assert closed is not None
        # short entered at 100, bought back at 110 -> loss of 10 per unit * 2
        assert closed.pnl == pytest.approx(-20.0)
        assert closed.side is OrderSide.SELL

    def test_commission_reduces_pnl(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=1, price=100.0, commission=0.5))
        closed = tracker.on_execution(make_fill(side=OrderSide.SELL, qty=1, price=110.0, commission=0.5))
        assert closed.pnl == pytest.approx(10.0 - 1.0)  # 10 profit minus 1.0 total commission

    def test_scaling_into_position_averages_entry_price(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=1, price=100.0))
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=1, price=120.0))
        open_trade = tracker.open_trades()["BTCUSDT"]
        assert open_trade.avg_entry_price == pytest.approx(110.0)
        assert open_trade.quantity == 2

    def test_partial_close_keeps_trade_open(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=4, price=100.0))
        result = tracker.on_execution(make_fill(side=OrderSide.SELL, qty=1, price=110.0))
        assert result is None
        open_trade = tracker.open_trades()["BTCUSDT"]
        assert open_trade.quantity == 3

    def test_partial_closes_then_full_close_produces_weighted_exit_price(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(side=OrderSide.BUY, qty=2, price=100.0))
        tracker.on_execution(make_fill(side=OrderSide.SELL, qty=1, price=110.0))
        closed = tracker.on_execution(make_fill(side=OrderSide.SELL, qty=1, price=130.0))
        assert closed is not None
        assert closed.exit_price == pytest.approx(120.0)  # (110*1 + 130*1) / 2
        assert closed.pnl == pytest.approx(40.0)  # (110-100)*1 + (130-100)*1

    def test_independent_symbols_tracked_separately(self):
        tracker = TradeTracker(multiplier_lookup)
        tracker.on_execution(make_fill(symbol="BTCUSDT", side=OrderSide.BUY, qty=1, price=100.0))
        tracker.on_execution(make_fill(symbol="ETHUSDT", side=OrderSide.BUY, qty=1, price=2000.0))
        assert set(tracker.open_trades().keys()) == {"BTCUSDT", "ETHUSDT"}

    def test_closing_fill_with_nothing_open_is_ignored(self):
        tracker = TradeTracker(multiplier_lookup)
        result = tracker.on_execution(make_fill(side=OrderSide.SELL, qty=1, price=100.0))
        # nothing was open, so this fill silently starts a new (short) position instead
        # of being treated as a close against nothing
        assert result is None
        assert "BTCUSDT" in tracker.open_trades()
        assert tracker.open_trades()["BTCUSDT"].side is OrderSide.SELL
