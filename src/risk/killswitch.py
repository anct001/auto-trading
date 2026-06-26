"""src/risk/killswitch.py — §4 kill-switch + dead-man's switches (process AND human).

MONEY CODE (TDD mandatory). A small state machine, ARMED ⇄ HALTED:

  - **manual human kill** → HALTED, overrides everything, and is **never cleared automatically**
    — only an explicit human ``re_arm()``.
  - **drawdown kill** → HALTED, **non-overridable at runtime**: the runtime dead-man's
    evaluation can only *add* halts, never clear one, so a drawdown halt persists until a human
    re-arms (§4 "require human re-enable").
  - **process dead-man's switch:** a stale process heartbeat cancels resting *entry* orders
    (not a full halt — exits/protective stops live exchange-side, §4); it recovers when the
    heartbeat does.
  - **human dead-man's switch:** no operator check-in for ≥ ``human_heartbeat_days`` → HALTED
    (the operator may be ill/away while capital runs unattended).

The first halt cause is preserved (the original "why"). ``re_arm`` is the only path back to
ARMED — there is deliberately no automatic un-halt.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.risk.config import RiskConfig


class KillState(Enum):
    ARMED = "armed"
    HALTED = "halted"


@dataclass
class KillSwitch:
    state: KillState = KillState.ARMED
    halt_reason: str = ""
    cancel_resting_entries: bool = False

    @property
    def is_halted(self) -> bool:
        return self.state is KillState.HALTED

    def allows_trading(self) -> bool:
        return self.state is KillState.ARMED

    def _trip(self, reason: str) -> None:
        # preserve the first cause; runtime logic only ever adds halts, never clears them
        if self.state is not KillState.HALTED:
            self.state = KillState.HALTED
            self.halt_reason = reason

    def manual_kill(self) -> None:
        """Human flatten + stop. Overrides everything; cleared only by re_arm()."""
        self._trip("manual_kill")

    def trip_drawdown(self) -> None:
        """Max-drawdown kill (non-overridable at runtime — §4)."""
        self._trip("drawdown_kill")

    def re_arm(self) -> None:
        """Explicit human re-enable — the ONLY way back to ARMED."""
        self.state = KillState.ARMED
        self.halt_reason = ""
        self.cancel_resting_entries = False

    def evaluate_dead_mans(
        self,
        *,
        process_heartbeat_age_s: float,
        human_heartbeat_age_days: float,
        cfg: RiskConfig,
        process_timeout_s: float = 60.0,
    ) -> None:
        """Runtime tick: apply both dead-man's switches. Can only add halts / set flags."""
        if human_heartbeat_age_days >= cfg.human_heartbeat_days:
            self._trip("human_heartbeat")
        # process heartbeat is a recoverable flag, not a halt
        self.cancel_resting_entries = process_heartbeat_age_s > process_timeout_s
