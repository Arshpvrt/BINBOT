"""Tests for ScannerStrategySettings' trailing-profit validators.

A bad value here wouldn't fail loudly in an obvious way — a zero
step_increment would divide-by-zero deep inside PositionMonitor's polling
loop, and a base level above the arm level would silently produce a
trailing stop that's already past its own trigger point the moment it
arms. Both are worth catching at startup instead.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import ScannerStrategySettings


class TestTrailingProfitValidators:
    def test_defaults_are_valid(self):
        settings = ScannerStrategySettings()
        assert settings.trailing_profit_arm_roi_pct == 30.0
        assert settings.trailing_profit_base_roi_pct == 20.0
        assert settings.trailing_profit_step_roi_pct == 2.0
        assert settings.trailing_profit_step_increment_roi_pct == 5.0
        assert settings.trailing_profit_hard_cap_roi_pct == 80.0

    def test_zero_step_increment_is_rejected(self):
        with pytest.raises(ValidationError, match="trailing_profit_step_increment_roi_pct must be positive"):
            ScannerStrategySettings(trailing_profit_step_increment_roi_pct=0.0)

    def test_negative_step_increment_is_rejected(self):
        with pytest.raises(ValidationError, match="trailing_profit_step_increment_roi_pct must be positive"):
            ScannerStrategySettings(trailing_profit_step_increment_roi_pct=-5.0)

    def test_base_above_arm_is_rejected(self):
        with pytest.raises(ValidationError, match="trailing_profit_base_roi_pct must not exceed"):
            ScannerStrategySettings(trailing_profit_arm_roi_pct=20.0, trailing_profit_base_roi_pct=30.0)

    def test_base_equal_to_arm_is_allowed(self):
        settings = ScannerStrategySettings(trailing_profit_arm_roi_pct=30.0, trailing_profit_base_roi_pct=30.0)
        assert settings.trailing_profit_base_roi_pct == 30.0

    def test_hard_cap_at_or_below_arm_is_rejected(self):
        with pytest.raises(ValidationError, match="trailing_profit_hard_cap_roi_pct must exceed"):
            ScannerStrategySettings(trailing_profit_arm_roi_pct=30.0, trailing_profit_hard_cap_roi_pct=30.0)

    def test_hard_cap_above_arm_is_allowed(self):
        settings = ScannerStrategySettings(trailing_profit_arm_roi_pct=30.0, trailing_profit_hard_cap_roi_pct=80.0)
        assert settings.trailing_profit_hard_cap_roi_pct == 80.0


class TestEquityPctValidators:
    """margin_equity_pct / stop_loss_equity_pct replaced the old flat
    margin_usdt / stop_loss_usdt dollar amounts (2026-08-17) — both must
    be a sane percentage, not zero/negative or absurdly over 100%."""

    def test_defaults_are_valid(self):
        settings = ScannerStrategySettings()
        assert settings.margin_equity_pct == 15.0
        assert settings.stop_loss_equity_pct == 25.0

    def test_zero_margin_pct_is_rejected(self):
        with pytest.raises(ValidationError, match="margin_equity_pct and stop_loss_equity_pct must be in"):
            ScannerStrategySettings(margin_equity_pct=0.0)

    def test_negative_stop_loss_pct_is_rejected(self):
        with pytest.raises(ValidationError, match="margin_equity_pct and stop_loss_equity_pct must be in"):
            ScannerStrategySettings(stop_loss_equity_pct=-10.0)

    def test_over_100_pct_is_rejected(self):
        with pytest.raises(ValidationError, match="margin_equity_pct and stop_loss_equity_pct must be in"):
            ScannerStrategySettings(margin_equity_pct=150.0)

    def test_exactly_100_pct_is_allowed(self):
        settings = ScannerStrategySettings(stop_loss_equity_pct=100.0)
        assert settings.stop_loss_equity_pct == 100.0
