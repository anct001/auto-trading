"""src/strategy/regime.py — deterministic regime gating (§5).

Strategies declare a ``target_regime``. The slow loop classifies the current market regime into
``regime_state.json``; this module's **deterministic** rule (not the LLM) decides whether a
strategy is allowed to act, given that classification. Per §5 the regime state "can
deterministically disable a strategy outside it (informed by, not decided by, the LLM)."

The gate may only **disable** — it can never trigger an entry, flip a direction, or size a trade
(those stay with the deterministic strategy + risk engine). And it is fail-to-enabled: absent,
stale, or unknown-pair regime → enabled, so the fast loop runs correctly without the file (Inv. 2).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ANY_REGIME = "any"


@dataclass(frozen=True)
class RegimeState:
    """Slow-loop regime classification per pair (`regime_state.json`, §3/§5)."""

    schema_version: int
    generated_at: datetime
    ttl_seconds: float
    regimes: dict[str, str] = field(default_factory=dict)

    def is_fresh(self, now: datetime) -> bool:
        return (now - self.generated_at).total_seconds() <= self.ttl_seconds


def is_strategy_enabled(
    state: RegimeState | None, pair: str, target_regime: str, *, now: datetime
) -> bool:
    """True unless the live regime is known, fresh, and differs from ``target_regime`` (§5).

    A regime-agnostic strategy (``target_regime == "any"``) is always enabled. We only disable
    when we have a trustworthy, current regime label that contradicts the strategy's regime;
    anything less (no state / stale / unknown pair) leaves it enabled — never silently halting on
    missing information.
    """
    if target_regime == ANY_REGIME:
        return True
    if state is None or not state.is_fresh(now):
        return True
    current = state.regimes.get(pair)
    if current is None:
        return True
    return current == target_regime


def write_regime_state(path: str | Path, state: RegimeState) -> None:
    """Write regime_state.json (slow loop / tests). Human-auditable ISO timestamp."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "schema_version": state.schema_version,
        "generated_at": state.generated_at.isoformat(),
        "ttl_seconds": state.ttl_seconds,
        "regimes": dict(state.regimes),
    }, indent=2) + "\n", encoding="utf-8")


def load_regime_state(path: str | Path) -> RegimeState | None:
    """Load regime_state.json, or None on ANY problem (fail-to-neutral; never crash the loop)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return RegimeState(
            schema_version=int(raw["schema_version"]),
            generated_at=datetime.fromisoformat(raw["generated_at"]),
            ttl_seconds=float(raw["ttl_seconds"]),
            regimes={str(k): str(v) for k, v in dict(raw["regimes"]).items()},
        )
    except (KeyError, ValueError, TypeError, OSError):
        return None
