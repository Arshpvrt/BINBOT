"""DuckDB-backed persistence for tick and bar data.

DuckDB gives us a zero-ops embedded columnar store that is fast enough for
both real-time append-only ingestion and later analytical queries (backtest
data loading, performance research) without standing up TimescaleDB. Swap
the connection string for a Postgres/TimescaleDB DSN if/when multi-writer
concurrency is needed; the SQL here is intentionally vanilla.
"""
from __future__ import annotations

import threading
from pathlib import Path

import duckdb

from core.events import BarEvent, TickEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ts TIMESTAMPTZ NOT NULL,
    symbol VARCHAR NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    bid_size INTEGER,
    ask_size INTEGER,
    last DOUBLE,
    last_size INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts);

CREATE TABLE IF NOT EXISTS bars (
    ts TIMESTAMPTZ NOT NULL,
    symbol VARCHAR NOT NULL,
    timeframe_s INTEGER NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    vwap DOUBLE
);
CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf_ts ON bars(symbol, timeframe_s, ts);
"""


class MarketDataStore:
    """Thread-safe wrapper: DuckDB connections are not safe to share across
    threads/event-loop-callbacks without serializing access, so all writes
    go through a lock. Reads (analytics/backtest loading) use their own
    read-only connection and are unaffected."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(path)
        self._conn = duckdb.connect(self._db_path)
        self._lock = threading.Lock()
        self._conn.execute(_SCHEMA)

    def write_tick(self, tick: TickEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ticks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [tick.ts, tick.symbol, tick.bid, tick.ask, tick.bid_size, tick.ask_size, tick.last, tick.last_size],
            )

    def write_bar(self, bar: BarEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    bar.ts,
                    bar.symbol,
                    bar.timeframe_s,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.vwap,
                ],
            )

    def read_bars(self, symbol: str, timeframe_s: int, start_ts: str, end_ts: str):
        with self._lock:
            return self._conn.execute(
                """
                SELECT ts, open, high, low, close, volume, vwap
                FROM bars
                WHERE symbol = ? AND timeframe_s = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
                """,
                [symbol, timeframe_s, start_ts, end_ts],
            ).fetchall()

    def read_bars_df(self, symbol: str, timeframe_s: int, start_ts: str, end_ts: str):
        import polars as pl

        rows = self.read_bars(symbol, timeframe_s, start_ts, end_ts)
        return pl.DataFrame(
            rows,
            schema=["ts", "open", "high", "low", "close", "volume", "vwap"],
            orient="row",
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
