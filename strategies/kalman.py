"""Kalman filter for a dynamic linear regression (adaptive hedge ratio)
between two cointegrated price series, following the standard formulation
used for statistical-arbitrage pairs trading (state = [beta, alpha], a
random-walk model of the hedge ratio and intercept).

The filtered innovation `e_t = y_t - (beta_t * x_t + alpha_t)` IS the
mean-reverting spread of the pair (an Ornstein-Uhlenbeck-like process by
construction, since it is the pair's cointegrating residual), and its
innovation variance gives a natural, adaptive z-score without needing an
arbitrary rolling window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class KalmanHedgeRatioFilter:
    delta: float = 1e-4
    obs_covariance: float = 1e-3

    _state: np.ndarray = field(init=False)  # [beta, alpha]
    _P: np.ndarray = field(init=False)  # state covariance
    _Q: np.ndarray = field(init=False)  # process noise covariance
    _initialized: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._state = np.zeros(2)
        self._P = np.eye(2)
        # Vw = delta / (1 - delta) * I, the standard Chan formulation:
        # smaller delta => slower-adapting (more stable) hedge ratio.
        self._Q = (self.delta / (1.0 - self.delta)) * np.eye(2)

    def update(self, y: float, x: float) -> "KalmanUpdateResult":
        """Feed the latest pair of prices (y = dependent/asset A, x =
        independent/asset B). Returns the filtered spread and its z-score.
        """
        H = np.array([x, 1.0])

        if not self._initialized:
            # Bootstrap the hedge ratio from the very first price pair
            # (y/x) rather than assuming 1.0 — for two assets on wildly
            # different price scales (e.g. BTC ~$65k vs ETH ~$1.9k, a ~34x
            # ratio), starting from an assumed 1:1 ratio produces a huge,
            # meaningless spread/z-score on bar one that has nothing to do
            # with real mean-reversion. Starting near the true ratio means
            # the first z-score reflects an actual (small) pricing
            # discrepancy instead of the filter's own initial ignorance.
            initial_beta = y / x if x != 0 else 1.0
            self._state = np.array([initial_beta, 0.0])
            self._P = np.eye(2) * 1.0
            self._initialized = True
            return KalmanUpdateResult(beta=initial_beta, alpha=0.0, spread=0.0, spread_variance=self.obs_covariance, z_score=0.0)

        # -- predict --
        state_pred = self._state  # random walk: no deterministic drift
        P_pred = self._P + self._Q

        # -- observe / innovate --
        y_hat = float(H @ state_pred)
        innovation = y - y_hat
        innovation_cov = float(H @ P_pred @ H.T) + self.obs_covariance
        innovation_cov = max(innovation_cov, 1e-12)

        # -- update --
        K = (P_pred @ H) / innovation_cov
        self._state = state_pred + K * innovation
        self._P = P_pred - np.outer(K, H) @ P_pred

        z_score = innovation / (innovation_cov**0.5)

        return KalmanUpdateResult(
            beta=float(self._state[0]),
            alpha=float(self._state[1]),
            spread=innovation,
            spread_variance=innovation_cov,
            z_score=z_score,
        )

    @property
    def beta(self) -> float:
        return float(self._state[0])

    @property
    def alpha(self) -> float:
        return float(self._state[1])


@dataclass(frozen=True, slots=True)
class KalmanUpdateResult:
    beta: float
    alpha: float
    spread: float
    spread_variance: float
    z_score: float
