"""src/risk/runtime.py — runtime tighten-only guard (Invariant 3).

MONEY CODE (TDD mandatory). Runtime controls may only **tighten** a limit or **halt** trading,
never **loosen**. A loosening requires a code/config change plus human sign-off, so it is refused
at runtime — this is the executable form of Invariant 3.

"Tighter" is direction-aware:
  - LOWER is tighter: per-trade risk, fractional-Kelly, gross/per-asset/cluster caps, concurrency,
    de-peg threshold (more sensitive), human-heartbeat days (halts sooner).
  - HIGHER (closer to zero) is tighter for the negative loss limits: daily soft/hard and the
    drawdown kill — a limit nearer zero fires sooner.

`apply_runtime_override` returns a new, tightened RiskConfig (re-validated) or raises
`RuntimeLoosenError` naming the offending field.
"""
from __future__ import annotations

from dataclasses import replace

from src.risk.config import RiskConfig

# fields where a SMALLER value is a tighter (safer) limit
_LOWER_IS_TIGHTER = frozenset({
    "per_trade_risk_pct",
    "max_fractional_kelly",
    "gross_exposure_pct",
    "per_asset_cap_pct",
    "max_concurrent_positions",
    "cluster_exposure_cap_pct",
    "depeg_threshold_pct",
    "human_heartbeat_days",
})
# negative limits where a HIGHER value (closer to zero) fires sooner → tighter
_HIGHER_IS_TIGHTER = frozenset({
    "daily_soft_pct",
    "daily_hard_pct",
    "max_drawdown_killswitch_pct",
})


class RuntimeLoosenError(ValueError):
    """Raised when a runtime override would loosen a limit (forbidden — Inv. 3)."""

    def __init__(self, field: str, current, proposed):
        self.field = field
        super().__init__(
            f"runtime override may not loosen {field!r}: {current} → {proposed} "
            f"(loosening needs a config change + human sign-off)"
        )


def _is_loosening(field: str, current: float, proposed: float) -> bool:
    if field in _LOWER_IS_TIGHTER:
        return proposed > current
    if field in _HIGHER_IS_TIGHTER:
        return proposed < current
    # unknown field → treat as not-tunable at runtime (refuse to be safe)
    raise RuntimeLoosenError(field, current, proposed)


def apply_runtime_override(cfg: RiskConfig, **overrides) -> RiskConfig:
    """Return a tightened copy of ``cfg``. Refuses (raises) if any field would loosen."""
    for field, proposed in overrides.items():
        current = getattr(cfg, field)
        if _is_loosening(field, current, proposed):
            raise RuntimeLoosenError(field, current, proposed)
    # replace re-runs RiskConfig validation, so a tightening that breaks an invariant still fails
    return replace(cfg, **overrides)
