"""Idempotent state recovery on system reboot.

Two distinct things must survive a crash/restart, and this module recovers
both from the correct source of truth rather than trusting anything cached
locally:

1. **Position inventory** — always rebuilt from the broker's own record
   (`BrokerInterface.reconcile_state()`), never from a local cache, so a
   corrupted or stale local file can never cause us to trade against a
   position we don't actually hold.
2. **Circuit-breaker session state** (today's starting/peak equity and,
   critically, whether we were already halted) — rebuilt from a small local
   JSON snapshot written after every risk-relevant state change. If the
   snapshot is from a prior calendar day, a fresh session is started
   instead; if it's from today, the exact prior state is restored,
   including an existing halt, since a halted system must come back up
   halted.

That "same day" check is deliberately not the only guard, after a real
production incident (2026-08-16): switching from testnet to a live account
mid-day restored a snapshot whose starting/peak equity belonged to the
testnet account (~4500 USDT) and compared it against the live account's
real balance (70 USDT), computing a nonsensical ~98% "drawdown" and
tripping the circuit breaker on garbage data. `environment_id` (passed in
by the caller — see `scripts/run_live_binance_scanner.py` for how it's
derived from testnet/live + an API key fingerprint) is now persisted
alongside the snapshot and checked too: a same-day snapshot from a
DIFFERENT environment is treated exactly like a snapshot from a prior day
— a fresh session starts instead of restoring numbers that belong to a
different account.

Calling `recover()` multiple times in a row (e.g. a crash during recovery
itself) is safe and converges to the same state — that's the idempotency
guarantee.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from config.logging_config import get_logger
from execution.broker_interface import BrokerInterface
from risk.circuit_breaker import DrawdownCircuitBreaker
from risk.risk_engine import RiskEngine

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    positions: dict[str, int]
    account_equity: float
    circuit_breaker_restored_from_disk: bool
    circuit_breaker_halted: bool


class StateRecoveryService:
    def __init__(
        self,
        *,
        broker: BrokerInterface,
        risk_engine: RiskEngine,
        circuit_breaker: DrawdownCircuitBreaker,
        state_path: str = "./data_store/session_state.json",
        environment_id: str = "",
    ) -> None:
        self._broker = broker
        self._risk_engine = risk_engine
        self._circuit_breaker = circuit_breaker
        self._state_path = Path(state_path)
        # Identifies which account/environment a persisted snapshot belongs
        # to (e.g. "live:<key fingerprint>" vs "testnet:<key fingerprint>")
        # — left blank by default, which preserves the old date-only check
        # for any caller that doesn't set it. See module docstring.
        self._environment_id = environment_id

    async def recover(self) -> RecoveryResult:
        positions = await self._broker.reconcile_state()
        for symbol, quantity in positions.items():
            self._risk_engine.update_position(symbol, quantity)

        current_equity = await self._broker.get_account_equity()
        restored_from_disk = self._restore_circuit_breaker(current_equity)
        self.persist()

        result = RecoveryResult(
            positions=positions,
            account_equity=current_equity,
            circuit_breaker_restored_from_disk=restored_from_disk,
            circuit_breaker_halted=self._circuit_breaker.is_halted,
        )
        logger.info(
            "state_recovery.completed",
            positions=positions,
            account_equity=current_equity,
            restored_from_disk=restored_from_disk,
            halted=result.circuit_breaker_halted,
        )
        return result

    def _restore_circuit_breaker(self, current_equity: float) -> bool:
        snapshot = self._read_snapshot()
        today = datetime.now(timezone.utc).date()

        if snapshot is not None and snapshot["session_date"] == today and snapshot["environment_id"] == self._environment_id:
            self._circuit_breaker.restore(
                session_date=today,
                starting_equity=snapshot["starting_equity"],
                peak_equity=snapshot["peak_equity"],
                halted=snapshot["halted"],
                halt_reason=snapshot["halt_reason"],
            )
            return True

        if snapshot is not None and snapshot["session_date"] == today:
            # Same day, but a DIFFERENT account/environment — e.g. a
            # testnet-to-live switch. Restoring this would compare the
            # current account's real equity against a baseline that
            # belongs to a different account entirely. Treat it exactly
            # like a stale prior-day snapshot: start fresh.
            logger.warning(
                "state_recovery.environment_mismatch_ignoring_snapshot",
                snapshot_environment_id=snapshot["environment_id"],
                current_environment_id=self._environment_id,
                account_equity=current_equity,
            )
        else:
            logger.info("state_recovery.new_session_no_prior_snapshot", account_equity=current_equity)
        self._circuit_breaker.reset_session(current_equity)
        return False

    def _read_snapshot(self) -> dict | None:
        if not self._state_path.exists():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {
                "session_date": date.fromisoformat(raw["session_date"]),
                "starting_equity": float(raw["starting_equity"]),
                "peak_equity": float(raw["peak_equity"]),
                "halted": bool(raw["halted"]),
                "halt_reason": str(raw["halt_reason"]),
                # Missing on any snapshot written before this field
                # existed — defaults to "", matching the default
                # environment_id for a caller that hasn't set one either,
                # so old deployments keep their prior (date-only) behavior.
                "environment_id": str(raw.get("environment_id", "")),
            }
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            logger.error("state_recovery.snapshot_read_failed", error=str(exc), path=str(self._state_path))
            return None

    def persist(self) -> None:
        """Write the current circuit-breaker state to disk. Call this after
        every risk-relevant state change (a new halt, a session reset) so
        the next recovery sees an up-to-date snapshot."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**self._circuit_breaker.snapshot(), "environment_id": self._environment_id}
        self._state_path.write_text(json.dumps(payload), encoding="utf-8")
