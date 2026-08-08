from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.enums import OrderSide
from data.binance_universe_feed import BinanceUniverseFeed
from strategies.funding_momentum_scanner import compute_lots, evaluate_entry


class TestEvaluateEntry:
    def test_long_setup_qualifies(self):
        side = evaluate_entry(
            price_change_pct=16.0, funding_now=-0.002, funding_window_start=-0.0005, price_jump_pct=15.0
        )
        assert side is OrderSide.BUY

    def test_short_setup_qualifies(self):
        side = evaluate_entry(
            price_change_pct=-18.0, funding_now=-0.0002, funding_window_start=-0.002, price_jump_pct=15.0
        )
        assert side is OrderSide.SELL

    def test_price_move_below_threshold_no_signal(self):
        side = evaluate_entry(
            price_change_pct=10.0, funding_now=-0.002, funding_window_start=-0.0005, price_jump_pct=15.0
        )
        assert side is None

    def test_long_setup_wrong_funding_direction_no_signal(self):
        # price jumped up, but funding is trending LESS negative (wrong direction for a long)
        side = evaluate_entry(
            price_change_pct=20.0, funding_now=-0.0002, funding_window_start=-0.002, price_jump_pct=15.0
        )
        assert side is None

    def test_short_setup_requires_funding_started_negative(self):
        # funding started positive, so the "increasing from negative" clause never applies
        side = evaluate_entry(
            price_change_pct=-20.0, funding_now=0.001, funding_window_start=0.0002, price_jump_pct=15.0
        )
        assert side is None

    def test_short_setup_funding_still_falling_no_signal(self):
        # funding started negative but is still getting MORE negative, not rising
        side = evaluate_entry(
            price_change_pct=-20.0, funding_now=-0.003, funding_window_start=-0.001, price_jump_pct=15.0
        )
        assert side is None

    def test_missing_price_data_returns_none(self):
        side = evaluate_entry(
            price_change_pct=None, funding_now=-0.002, funding_window_start=-0.0005, price_jump_pct=15.0
        )
        assert side is None

    def test_missing_funding_data_returns_none(self):
        side = evaluate_entry(price_change_pct=20.0, funding_now=None, funding_window_start=None, price_jump_pct=15.0)
        assert side is None

    def test_exact_threshold_boundary_qualifies(self):
        side = evaluate_entry(
            price_change_pct=15.0, funding_now=-0.002, funding_window_start=-0.0005, price_jump_pct=15.0
        )
        assert side is OrderSide.BUY


class TestComputeLots:
    def test_basic_lot_sizing(self):
        # 750 USDT notional / (65000 price * 0.001 step) = 750/65 = 11.5 -> 11 lots
        lots = compute_lots(notional_usdt=750.0, price=65000.0, step_size=0.001)
        assert lots == 11

    def test_never_exceeds_intended_notional(self):
        lots = compute_lots(notional_usdt=750.0, price=65000.0, step_size=0.001)
        assert lots * 65000.0 * 0.001 <= 750.0

    def test_zero_price_is_safe(self):
        assert compute_lots(notional_usdt=750.0, price=0.0, step_size=0.001) == 0

    def test_zero_step_size_is_safe(self):
        assert compute_lots(notional_usdt=750.0, price=100.0, step_size=0.0) == 0

    def test_too_small_notional_for_one_lot(self):
        # one lot alone costs more than the entire order notional
        assert compute_lots(notional_usdt=10.0, price=65000.0, step_size=0.001) == 0


@pytest.fixture
def feed() -> BinanceUniverseFeed:
    return BinanceUniverseFeed(socket_manager=None, event_bus=None, price_window_min=15.0, funding_window_min=30.0)


class TestBinanceUniverseFeedWindows:
    def test_price_change_pct_none_with_insufficient_history(self, feed: BinanceUniverseFeed):
        now = datetime.now(timezone.utc)
        feed._append_trimmed(feed._price_history["BTCUSDT"], now, 100.0, feed._price_window)
        assert feed.price_change_pct("BTCUSDT") is None

    def test_price_change_pct_computed_once_window_full(self, feed: BinanceUniverseFeed):
        start = datetime.now(timezone.utc) - timedelta(minutes=15)
        feed._append_trimmed(feed._price_history["BTCUSDT"], start, 100.0, feed._price_window)
        feed._append_trimmed(feed._price_history["BTCUSDT"], start + timedelta(minutes=15), 116.0, feed._price_window)
        pct = feed.price_change_pct("BTCUSDT")
        assert pct == pytest.approx(16.0)

    def test_trimming_drops_samples_outside_window(self, feed: BinanceUniverseFeed):
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=45)
        history = feed._price_history["BTCUSDT"]
        feed._append_trimmed(history, old_ts, 50.0, feed._price_window)
        feed._append_trimmed(history, old_ts + timedelta(minutes=20), 100.0, feed._price_window)
        # the 45-min-old sample should have been trimmed since the window is 15 min
        assert all(ts >= old_ts + timedelta(minutes=5) for ts, _ in history)

    def test_funding_trend_none_with_insufficient_history(self, feed: BinanceUniverseFeed):
        now = datetime.now(timezone.utc)
        feed._append_trimmed(feed._funding_history["BTCUSDT"], now, -0.001, feed._funding_window)
        assert feed.funding_trend("BTCUSDT") is None

    def test_funding_trend_computed_once_window_full(self, feed: BinanceUniverseFeed):
        start = datetime.now(timezone.utc) - timedelta(minutes=30)
        feed._append_trimmed(feed._funding_history["BTCUSDT"], start, -0.0005, feed._funding_window)
        feed._append_trimmed(
            feed._funding_history["BTCUSDT"], start + timedelta(minutes=30), -0.002, feed._funding_window
        )
        trend = feed.funding_trend("BTCUSDT")
        assert trend == pytest.approx((-0.002, -0.0005))

    def test_latest_price_tracks_most_recent(self, feed: BinanceUniverseFeed):
        feed._latest_price["ETHUSDT"] = 1234.5
        assert feed.latest_price("ETHUSDT") == pytest.approx(1234.5)

    def test_latest_price_unknown_symbol_is_none(self, feed: BinanceUniverseFeed):
        assert feed.latest_price("NOPEUSDT") is None
