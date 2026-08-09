"""Pure, offline tests for the Binance adapter's quantity/price rounding.

These are the functions where a bug would most directly translate into
"the bot sent the wrong size to a real exchange," so they get dedicated
coverage independent of any network access.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from execution.binance_broker import BinanceFuturesBroker, SymbolFilters, round_step


class TestRoundStep:
    def test_rounds_down_to_nearest_step(self):
        assert round_step(0.7345, 0.001) == pytest.approx(0.734)

    def test_exact_multiple_is_unchanged(self):
        assert round_step(1.5, 0.5) == pytest.approx(1.5)

    def test_never_rounds_up(self):
        # a naive round() here would give 0.005, overshooting the input
        assert round_step(0.0049999, 0.001) == pytest.approx(0.004)

    def test_zero_step_is_a_no_op(self):
        assert round_step(1.23456, 0.0) == 1.23456


@pytest.fixture
def broker() -> BinanceFuturesBroker:
    b = BinanceFuturesBroker(
        api_key="test", api_secret="test", testnet=True, symbols=["BTCUSDT", "ETHUSDT"]
    )
    b._filters["BTCUSDT"] = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=0.10,
        step_size=0.001,
        min_qty=0.001,
        min_notional=5.0,
        quantity_precision=3,
        price_precision=1,
    )
    b._filters["ETHUSDT"] = SymbolFilters(
        symbol="ETHUSDT",
        tick_size=0.01,
        step_size=0.01,
        min_qty=0.01,
        min_notional=5.0,
        quantity_precision=2,
        price_precision=2,
    )
    return b


class TestLotConversion:
    def test_lots_to_quantity_btc(self, broker: BinanceFuturesBroker):
        # 734 lots * 0.001 step = 0.734 BTC
        assert broker.lots_to_quantity("BTCUSDT", 734) == pytest.approx(0.734)

    def test_quantity_to_lots_btc(self, broker: BinanceFuturesBroker):
        assert broker.quantity_to_lots("BTCUSDT", 0.734) == 734

    def test_round_trip_is_stable(self, broker: BinanceFuturesBroker):
        for lots in (1, 5, 100, 1234, 99999):
            qty = broker.lots_to_quantity("BTCUSDT", lots)
            assert broker.quantity_to_lots("BTCUSDT", qty) == lots

    def test_different_symbols_have_independent_step_sizes(self, broker: BinanceFuturesBroker):
        assert broker.get_step_size("BTCUSDT") == pytest.approx(0.001)
        assert broker.get_step_size("ETHUSDT") == pytest.approx(0.01)
        # the same lot count means a different real quantity per symbol
        assert broker.lots_to_quantity("BTCUSDT", 100) != broker.lots_to_quantity("ETHUSDT", 100)

    def test_zero_lots_is_zero_quantity(self, broker: BinanceFuturesBroker):
        assert broker.lots_to_quantity("BTCUSDT", 0) == 0.0


class TestLeverageCapping:
    """Some symbols cap out below the requested leverage (Binance error
    -4028). The broker should fall back to that symbol's actual max rather
    than refusing to trade it, and never repeat the failed call or the
    bracket lookup for the same symbol again."""

    def _mock_client(self, broker: BinanceFuturesBroker) -> AsyncMock:
        client = AsyncMock()
        broker._client = client
        return client

    async def test_falls_back_to_symbol_max_leverage(self, broker: BinanceFuturesBroker):
        client = self._mock_client(broker)
        client.futures_change_leverage.side_effect = [Exception("APIError(code=-4028): Leverage 15 is not valid"), None]
        client.futures_leverage_bracket.return_value = [{"brackets": [{"initialLeverage": 10}]}]

        await broker._configure_leverage_and_margin("BICOUSDT", leverage=15, margin_type="CROSSED")

        assert broker._configured_leverage_margin["BICOUSDT"] == (10, "CROSSED")
        assert client.futures_change_leverage.call_count == 2
        client.futures_change_leverage.assert_any_call(symbol="BICOUSDT", leverage=10)

    async def test_reraises_when_bracket_lookup_also_fails(self, broker: BinanceFuturesBroker):
        client = self._mock_client(broker)
        original_error = Exception("APIError(code=-4028): Leverage 15 is not valid")
        client.futures_change_leverage.side_effect = original_error
        client.futures_leverage_bracket.side_effect = Exception("network error")

        with pytest.raises(Exception, match="Leverage 15 is not valid"):
            await broker._configure_leverage_and_margin("BICOUSDT", leverage=15, margin_type="CROSSED")
        assert "BICOUSDT" not in broker._configured_leverage_margin

    async def test_second_call_does_not_repeat_api_calls(self, broker: BinanceFuturesBroker):
        client = self._mock_client(broker)
        client.futures_change_leverage.side_effect = [Exception("APIError(code=-4028): Leverage 15 is not valid"), None]
        client.futures_leverage_bracket.return_value = [{"brackets": [{"initialLeverage": 10}]}]

        await broker._configure_leverage_and_margin("BICOUSDT", leverage=15, margin_type="CROSSED")
        await broker._configure_leverage_and_margin("BICOUSDT", leverage=15, margin_type="CROSSED")

        assert client.futures_change_leverage.call_count == 2  # not 3 or 4
        assert client.futures_leverage_bracket.call_count == 1

    async def test_no_fallback_needed_when_leverage_is_valid(self, broker: BinanceFuturesBroker):
        client = self._mock_client(broker)

        await broker._configure_leverage_and_margin("BTCUSDT", leverage=2, margin_type="ISOLATED")

        assert broker._configured_leverage_margin["BTCUSDT"] == (2, "ISOLATED")
        client.futures_leverage_bracket.assert_not_called()
