"""Real-time initial/maintenance margin checks against available account balance.

Margin requirements are pulled per-symbol from a pluggable `MarginScheduleProvider`
(a static table by default; in production this would be refreshed from IBKR's
`reqMarginRequirements`-equivalent or an exchange margin file). The calculator
never trusts the broker to reject an over-margin order — it is a *pre-trade*
gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MarginRequirement:
    symbol: str
    initial_margin_per_contract: float
    maintenance_margin_per_contract: float


@dataclass(frozen=True, slots=True)
class MarginCheckResult:
    passed: bool
    reason: str
    required_initial_margin: float
    required_maintenance_margin: float
    available_funds: float
    projected_excess_liquidity: float


@dataclass
class MarginCalculator:
    schedule: dict[str, MarginRequirement] = field(default_factory=dict)
    initial_margin_buffer_pct: float = 25.0
    maintenance_margin_buffer_pct: float = 10.0

    def set_requirement(self, req: MarginRequirement) -> None:
        self.schedule[req.symbol] = req

    def check_order(
        self,
        *,
        symbol: str,
        additional_contracts: int,
        current_position_contracts: int,
        net_liquidation_value: float,
        current_initial_margin_used: float,
        current_maintenance_margin_used: float,
    ) -> MarginCheckResult:
        req = self.schedule.get(symbol)
        if req is None:
            return MarginCheckResult(
                passed=False,
                reason=f"no margin schedule for symbol={symbol}",
                required_initial_margin=0.0,
                required_maintenance_margin=0.0,
                available_funds=net_liquidation_value - current_initial_margin_used,
                projected_excess_liquidity=0.0,
            )

        projected_contracts = abs(current_position_contracts + additional_contracts)
        projected_initial = projected_contracts * req.initial_margin_per_contract
        projected_maintenance = projected_contracts * req.maintenance_margin_per_contract

        # buffers demand headroom beyond the raw requirement, protecting against
        # intraday margin-rate hikes and mark-to-market swings
        buffered_initial = projected_initial * (1 + self.initial_margin_buffer_pct / 100.0)
        buffered_maintenance = projected_maintenance * (
            1 + self.maintenance_margin_buffer_pct / 100.0
        )

        excess_liquidity = net_liquidation_value - buffered_initial
        passed = excess_liquidity >= 0 and net_liquidation_value >= buffered_maintenance

        reason = "ok"
        if not passed:
            reason = (
                f"insufficient margin: required_initial(buffered)={buffered_initial:.2f} "
                f"required_maintenance(buffered)={buffered_maintenance:.2f} "
                f"net_liq={net_liquidation_value:.2f}"
            )

        return MarginCheckResult(
            passed=passed,
            reason=reason,
            required_initial_margin=buffered_initial,
            required_maintenance_margin=buffered_maintenance,
            available_funds=net_liquidation_value - current_initial_margin_used,
            projected_excess_liquidity=excess_liquidity,
        )
