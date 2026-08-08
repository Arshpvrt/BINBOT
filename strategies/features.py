"""Multi-timeframe feature calculation on bar data using polars (avoids the
pandas execution bottlenecks — chained rolling ops here run as a single
lazy query plan).
"""
from __future__ import annotations

import polars as pl


def log_returns(df: pl.DataFrame, price_col: str = "close") -> pl.DataFrame:
    return df.with_columns((pl.col(price_col).log() - pl.col(price_col).log().shift(1)).alias("log_return"))


def rolling_realized_volatility(
    df: pl.DataFrame, *, window: int, bars_per_year: float, price_col: str = "close"
) -> pl.DataFrame:
    """Annualized realized volatility from rolling std of log returns."""
    out = log_returns(df, price_col)
    return out.with_columns(
        (pl.col("log_return").rolling_std(window_size=window) * (bars_per_year**0.5)).alias(
            f"realized_vol_{window}"
        )
    )


def rolling_zscore(df: pl.DataFrame, col: str, *, window: int) -> pl.DataFrame:
    mean = pl.col(col).rolling_mean(window_size=window)
    std = pl.col(col).rolling_std(window_size=window)
    return df.with_columns(((pl.col(col) - mean) / std).alias(f"{col}_zscore_{window}"))


def resample_ohlcv(df: pl.DataFrame, *, every: str, ts_col: str = "ts") -> pl.DataFrame:
    """Aggregate finer-grained bars up to a coarser timeframe, e.g. 1-minute
    bars to 5-minute bars, for multi-timeframe feature construction."""
    return (
        df.sort(ts_col)
        .group_by_dynamic(ts_col, every=every)
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            ((pl.col("close") * pl.col("volume")).sum() / pl.col("volume").sum().clip(lower_bound=1e-9)).alias(
                "vwap"
            ),
        )
    )


def multi_timeframe_features(
    df: pl.DataFrame, *, timeframes: dict[str, str], zscore_window: int = 20
) -> dict[str, pl.DataFrame]:
    """Build a feature frame per named timeframe, e.g.
    `timeframes={"1m": "1m", "5m": "5m", "15m": "15m"}`.
    """
    features: dict[str, pl.DataFrame] = {}
    for name, every in timeframes.items():
        resampled = resample_ohlcv(df, every=every)
        with_returns = log_returns(resampled)
        with_vol = rolling_realized_volatility(with_returns, window=zscore_window, bars_per_year=252 * 6.5 * 60)
        with_z = rolling_zscore(with_vol, "close", window=zscore_window)
        features[name] = with_z
    return features
