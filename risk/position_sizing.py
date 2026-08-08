"""Volatility-adjusted position sizing: Kelly Criterion and target-volatility targeting.

Both functions return a *signed contract count* rounded down (never up — sizing
must never accidentally exceed intent) so callers can feed the result straight
into an order quantity field.
"""
from __future__ import annotations

import math


def kelly_position_size(
    *,
    win_probability: float,
    win_loss_ratio: float,
    account_equity: float,
    price: float,
    contract_multiplier: float,
    kelly_fraction_cap: float = 0.5,
    max_kelly_leverage: float = 1.0,
) -> int:
    """Discrete (fractional) Kelly sizing.

    f* = p - (1-p)/b   where b = win_loss_ratio (avg win / avg loss)

    The raw Kelly fraction is capped by `kelly_fraction_cap` (institutional
    desks virtually never trade full Kelly due to estimation error / fat tails)
    and by `max_kelly_leverage` as an absolute notional-to-equity ceiling.
    """
    if not (0.0 < win_probability < 1.0):
        raise ValueError("win_probability must be in (0, 1)")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    if price <= 0 or contract_multiplier <= 0 or account_equity <= 0:
        raise ValueError("price, contract_multiplier, account_equity must be positive")

    raw_kelly = win_probability - (1.0 - win_probability) / win_loss_ratio
    raw_kelly = max(raw_kelly, 0.0)  # never size a negative-edge bet
    fraction = min(raw_kelly * kelly_fraction_cap, max_kelly_leverage)

    notional_target = fraction * account_equity
    contract_notional = price * contract_multiplier
    contracts = math.floor(notional_target / contract_notional)
    return max(contracts, 0)


def target_volatility_position_size(
    *,
    account_equity: float,
    target_annual_volatility_pct: float,
    instrument_annual_volatility_pct: float,
    price: float,
    contract_multiplier: float,
    max_leverage: float = 4.0,
) -> int:
    """Size so the position's standalone annualized $ volatility equals a
    fixed target fraction of equity: contracts = (equity * target_vol) /
    (price * multiplier * instrument_vol).
    """
    if instrument_annual_volatility_pct <= 0:
        raise ValueError("instrument_annual_volatility_pct must be positive")
    if price <= 0 or contract_multiplier <= 0 or account_equity <= 0:
        raise ValueError("price, contract_multiplier, account_equity must be positive")

    target_dollar_vol = account_equity * (target_annual_volatility_pct / 100.0)
    contract_notional = price * contract_multiplier
    contract_dollar_vol = contract_notional * (instrument_annual_volatility_pct / 100.0)

    contracts = math.floor(target_dollar_vol / contract_dollar_vol)

    max_contracts_by_leverage = math.floor((account_equity * max_leverage) / contract_notional)
    return max(min(contracts, max_contracts_by_leverage), 0)
