"""Offline statistical validation for a candidate pair: Engle-Granger
cointegration test and Ornstein-Uhlenbeck half-life of mean reversion.
Run this before enabling `KalmanPairsStrategy` on a pair — trading an
uncointegrated "pair" turns mean-reversion into directional risk.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.stattools import coint


@dataclass(frozen=True, slots=True)
class CointegrationResult:
    is_cointegrated: bool
    p_value: float
    test_statistic: float
    critical_values: tuple[float, float, float]


def engle_granger_test(series_a: np.ndarray, series_b: np.ndarray, *, significance: float = 0.05) -> CointegrationResult:
    if len(series_a) != len(series_b):
        raise ValueError("series_a and series_b must be the same length")
    if len(series_a) < 30:
        raise ValueError("need at least 30 observations for a meaningful cointegration test")

    test_stat, p_value, crit_values = coint(series_a, series_b)
    return CointegrationResult(
        is_cointegrated=p_value < significance,
        p_value=float(p_value),
        test_statistic=float(test_stat),
        critical_values=tuple(float(c) for c in crit_values),  # type: ignore[arg-type]
    )


def ou_half_life(spread: np.ndarray) -> float:
    """Estimate the Ornstein-Uhlenbeck mean-reversion half-life (in bars) by
    regressing Δspread_t on spread_{t-1}: Δspread_t = θ * spread_{t-1} + ε.
    half_life = -ln(2) / θ. Returns +inf if the fitted θ implies no mean
    reversion (θ >= 0).
    """
    spread = np.asarray(spread, dtype=float)
    lagged = spread[:-1]
    delta = np.diff(spread)

    lagged_centered = lagged - lagged.mean()
    theta = float(np.dot(lagged_centered, delta) / np.dot(lagged_centered, lagged_centered))

    if theta >= 0:
        return float("inf")
    return -np.log(2) / theta
