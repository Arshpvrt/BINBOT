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
