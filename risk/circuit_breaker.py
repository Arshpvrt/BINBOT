"""Hard kill-switch: halts all order generation the instant daily P&L breaches
a configured drawdown threshold. Once tripped, it stays tripped for the rest
of that trading day — there is no same-day automatic recovery, by design, so
a runaway strategy cannot re-trip and un-trip its way past risk within one
session. A new calendar day IS a new trading session, though, and starts a
fresh drawdown budget automatically (see `update()`) — a halt is a "stop for
today," not a permanent kill, unless something else (an operator, or state
recovery restoring a still-halted snapshot) says otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from config.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DrawdownCircuitBreaker:
    max_daily_drawdown_pct: float
    starting_equity: float
    _session_date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    _peak_equity: float = field(init=False)
    _halted: bool = field(default=False, init=False)
    _halt_reason: str = field(default="", init=False)
    _auto_reset_pending: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if not (0 < self.max_daily_drawdown_pct <= 100):
            raise ValueError("max_daily_drawdown_pct must be in (0, 100]")
        self._peak_equity = self.starting_equity

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def consume_auto_reset_flag(self) -> bool:
        """True exactly once, immediately after an `update()` call that
        auto-reset the session for a new calendar day — lets the caller
        push a one-time operator notification (audit line, Telegram) for
        it instead of this being silently invisible outside the logs."""
        flag = self._auto_reset_pending
        self._auto_reset_pending = False
        return flag

    def reset_session(self, starting_equity: float, *, session_date: date | None = None) -> None:
        """Must be called explicitly at the start of a new trading day.

        `session_date` defaults to the real UTC "today" (live trading). The
        backtest engine passes the historical bar's date explicitly instead,
        so daily-drawdown resets track the simulated calendar rather than
        wall-clock time when replaying multi-day historical data.
        """
        self.starting_equity = starting_equity
        self._peak_equity = starting_equity
        self._session_date = session_date or datetime.now(timezone.utc).date()
        self._halted = False
        self._halt_reason = ""
        logger.info("circuit_breaker.session_reset", starting_equity=starting_equity, session_date=str(self._session_date))

    def snapshot(self) -> dict[str, object]:
        """Serializable state for persistence (used for idempotent recovery
        on reboot — see `utils.state_recovery`)."""
        return {
            "session_date": self._session_date.isoformat(),
            "starting_equity": self.starting_equity,
            "peak_equity": self._peak_equity,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }

    def restore(
        self,
        *,
        session_date: date,
        starting_equity: float,
        peak_equity: float,
        halted: bool,
        halt_reason: str,
    ) -> None:
        """Rehydrate exact prior-session state (including a halt) rather
        than starting fresh — a system that was halted before a crash MUST
        still be halted after it comes back up."""
        self._session_date = session_date
        self.starting_equity = starting_equity
        self._peak_equity = peak_equity
        self._halted = halted
        self._halt_reason = halt_reason
        logger.info("circuit_breaker.restored", **self.snapshot())

    def update(self, current_equity: float, *, now: datetime | None = None) -> bool:
        """Feed the latest mark-to-market equity. Returns True if this call
        newly tripped the breaker (so the caller can emit a RiskHaltEvent
        exactly once).

        A calendar-day rollover is treated as the start of a new trading
        session: this auto-resets (fresh starting/peak equity, halt
        cleared) exactly like an explicit `reset_session()` call would,
        rather than leaving a halt from the prior day in effect
        indefinitely — which is what happened before this fix, on any
        deployment where the process stays up across midnight instead of
        restarting (restarts already got a fresh session via
        `utils.state_recovery`, which is the only reason this gap wasn't
        caught immediately). Call `consume_auto_reset_flag()` right after
        this to notify an operator that it happened.
        """
        now = now or datetime.now(timezone.utc)
        if now.date() != self._session_date:
            was_halted = self._halted
            previous_session_date = self._session_date
            self.reset_session(current_equity, session_date=now.date())
            self._auto_reset_pending = True
            logger.info(
                "circuit_breaker.new_day_auto_reset",
                previous_session_date=str(previous_session_date),
                new_session_date=str(now.date()),
                was_halted=was_halted,
                new_starting_equity=current_equity,
            )
            return False

        self._peak_equity = max(self._peak_equity, current_equity)

        if self._halted:
            return False

        drawdown_from_start_pct = (
            (self.starting_equity - current_equity) / self.starting_equity
        ) * 100.0
        drawdown_from_peak_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100.0
        drawdown_pct = max(drawdown_from_start_pct, drawdown_from_peak_pct)

        if drawdown_pct >= self.max_daily_drawdown_pct:
            self._halted = True
            self._halt_reason = (
                f"daily drawdown {drawdown_pct:.3f}% >= limit "
                f"{self.max_daily_drawdown_pct:.3f}% "
                f"(equity={current_equity:.2f}, start={self.starting_equity:.2f}, "
                f"peak={self._peak_equity:.2f})"
            )
            logger.error("circuit_breaker.tripped", reason=self._halt_reason)
            return True
        return False

    def force_halt(self, reason: str) -> None:
        """Manual/operator kill switch, or triggered by another risk subsystem
        (e.g. broker disconnect, margin breach)."""
        self._halted = True
        self._halt_reason = reason
        logger.error("circuit_breaker.force_halted", reason=reason)
