"""Tests for StateRecoveryService's circuit-breaker snapshot restore logic.

The environment_id check exists because of a real production incident
(2026-08-16): switching from a testnet account to a live account on the
same calendar day restored a same-day snapshot whose starting/peak equity
belonged to the testnet account (~4500 USDT), then compared it against
the live account's real balance (70 USDT) — a ~98% "drawdown" that
tripped the circuit breaker on numbers that were never comparable in the
first place. These tests pin the fix: a same-day snapshot from a
different environment must be treated exactly like a stale prior-day one.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from risk.circuit_breaker import DrawdownCircuitBreaker
from utils.state_recovery import StateRecoveryService


def _write_snapshot(path, **overrides) -> None:
    payload = {
        "session_date": datetime.now(timezone.utc).date().isoformat(),
        "starting_equity": 4501.47,
        "peak_equity": 4578.36,
        "halted": False,
        "halt_reason": "",
        "environment_id": "testnet:aaa111",
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_service(*, state_path, environment_id: str, current_equity: float = 70.0):
    broker = AsyncMock()
    broker.reconcile_state.return_value = {}
    broker.get_account_equity.return_value = current_equity
    risk_engine = MagicMock()
    circuit_breaker = DrawdownCircuitBreaker(max_daily_drawdown_pct=10.0, starting_equity=1.0)
    service = StateRecoveryService(
        broker=broker,
        risk_engine=risk_engine,
        circuit_breaker=circuit_breaker,
        state_path=str(state_path),
        environment_id=environment_id,
    )
    return service, circuit_breaker, broker


class TestEnvironmentAwareRestore:
    async def test_same_day_same_environment_restores_the_snapshot(self, tmp_path):
        state_path = tmp_path / "session_state.json"
        _write_snapshot(state_path, environment_id="live:abc123", halted=True, halt_reason="daily limit hit")
        service, circuit_breaker, _ = _make_service(
            state_path=state_path, environment_id="live:abc123", current_equity=4510.0
        )

        result = await service.recover()

        assert result.circuit_breaker_restored_from_disk is True
        assert circuit_breaker.is_halted is True  # the prior halt carried over, as intended
        assert circuit_breaker.halt_reason == "daily limit hit"

    async def test_same_day_different_environment_does_not_restore(self, tmp_path):
        state_path = tmp_path / "session_state.json"
        # Stale testnet snapshot: large equity, not halted.
        _write_snapshot(state_path, environment_id="testnet:aaa111", starting_equity=4501.47, peak_equity=4578.36)
        service, circuit_breaker, broker = _make_service(
            state_path=state_path, environment_id="live:zzz999", current_equity=70.0
        )

        result = await service.recover()

        assert result.circuit_breaker_restored_from_disk is False
        assert not circuit_breaker.is_halted
        # A fresh session was started from the REAL current equity, not
        # the stale testnet figures — this is the actual bug fix: without
        # it, starting_equity stays at 4501.47 and a 70 USDT balance reads
        # as a ~98% drawdown.
        assert circuit_breaker.snapshot()["starting_equity"] == pytest.approx(70.0)

    async def test_prior_day_snapshot_is_still_ignored_even_with_matching_environment(self, tmp_path):
        state_path = tmp_path / "session_state.json"
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _write_snapshot(state_path, session_date=yesterday, environment_id="live:abc123", halted=True)
        service, circuit_breaker, _ = _make_service(
            state_path=state_path, environment_id="live:abc123", current_equity=1000.0
        )

        result = await service.recover()

        assert result.circuit_breaker_restored_from_disk is False
        assert not circuit_breaker.is_halted  # a halt from a prior day must not carry over

    async def test_snapshot_missing_environment_id_defaults_to_empty_and_still_restores(self, tmp_path):
        # Simulates a snapshot written before this field existed.
        state_path = tmp_path / "session_state.json"
        payload = {
            "session_date": datetime.now(timezone.utc).date().isoformat(),
            "starting_equity": 900.0,
            "peak_equity": 950.0,
            "halted": False,
            "halt_reason": "",
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        # No environment_id passed -> defaults to "", matching the missing field.
        service, circuit_breaker, _ = _make_service(state_path=state_path, environment_id="", current_equity=900.0)

        result = await service.recover()

        assert result.circuit_breaker_restored_from_disk is True
        assert circuit_breaker.snapshot()["starting_equity"] == pytest.approx(900.0)


class TestPersistWritesEnvironmentId:
    async def test_persist_includes_the_current_environment_id(self, tmp_path):
        state_path = tmp_path / "session_state.json"
        service, _, _ = _make_service(state_path=state_path, environment_id="live:deadbeef")

        service.persist()

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert raw["environment_id"] == "live:deadbeef"
