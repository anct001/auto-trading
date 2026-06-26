"""src/risk/config.py — RiskConfig loader + validation (PLAN_RISK R0).

Loads the declarative risk limits from a version-controlled JSON file (§4 defaults live in
config/risk/default.json) and validates them on load. This is the **config** side of the §15
integrity check: it applies only to declarative config, never to runtime-computed state or to
legitimate runtime *tightening* (Inv. 3). The loader refuses anything that would breach a hard
invariant (leverage > 0 before P5) or that is internally inconsistent (a soft daily limit deeper
than the hard one would fire out of order).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RiskConfig:
    per_trade_risk_pct: float
    position_sizing: str
    max_fractional_kelly: float
    max_concurrent_positions: int
    gross_exposure_pct: float
    per_asset_cap_pct: float
    cluster_corr_threshold: float
    cluster_exposure_cap_pct: float
    daily_soft_pct: float
    daily_hard_pct: float
    max_drawdown_killswitch_pct: float
    depeg_quote_assets: tuple[str, ...]
    depeg_threshold_pct: float
    capital_on_exchange_cap: float | None
    human_heartbeat_days: int
    leverage: int
    exchange_assertions: dict[str, Any]

    def __post_init__(self) -> None:
        if self.per_trade_risk_pct <= 0:
            raise ValueError("per_trade_risk_pct must be positive")
        if not 0 < self.max_fractional_kelly <= 0.5:
            raise ValueError("max_fractional_kelly must be in (0, 0.5] (never raw Kelly, §4)")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be >= 1")
        if self.gross_exposure_pct <= 0 or self.per_asset_cap_pct <= 0:
            raise ValueError("exposure caps must be positive")
        if not 0 < self.cluster_corr_threshold <= 1:
            raise ValueError("cluster_corr_threshold must be in (0, 1]")
        if self.cluster_exposure_cap_pct <= 0:
            raise ValueError("cluster_exposure_cap_pct must be positive")
        # both daily limits are negative; soft must trigger BEFORE hard → |soft| < |hard|
        if self.daily_soft_pct >= 0 or self.daily_hard_pct >= 0:
            raise ValueError("daily loss limits must be negative")
        if not (self.daily_hard_pct < self.daily_soft_pct):
            raise ValueError("daily hard limit must be deeper (more negative) than the soft limit")
        if self.max_drawdown_killswitch_pct >= 0:
            raise ValueError("max_drawdown_killswitch_pct must be negative")
        if self.depeg_threshold_pct <= 0:
            raise ValueError("depeg_threshold_pct must be positive")
        if self.human_heartbeat_days < 1:
            raise ValueError("human_heartbeat_days must be >= 1")
        if self.leverage != 0:
            raise ValueError("leverage must be 0 before Phase 5 (Invariant 4)")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskConfig":
        corr = d.get("correlation", {})
        daily = d.get("daily_loss_limits", {})
        depeg = d.get("depeg_guard", {})
        return cls(
            per_trade_risk_pct=float(d["per_trade_risk_pct"]),
            position_sizing=str(d.get("position_sizing", "inverse_atr")),
            max_fractional_kelly=float(d["max_fractional_kelly"]),
            max_concurrent_positions=int(d["max_concurrent_positions"]),
            gross_exposure_pct=float(d["gross_exposure_pct"]),
            per_asset_cap_pct=float(d["per_asset_cap_pct"]),
            cluster_corr_threshold=float(corr["cluster_corr_threshold"]),
            cluster_exposure_cap_pct=float(corr["cluster_exposure_cap_pct"]),
            daily_soft_pct=float(daily["soft_pct"]),
            daily_hard_pct=float(daily["hard_pct"]),
            max_drawdown_killswitch_pct=float(d["max_drawdown_killswitch_pct"]),
            depeg_quote_assets=tuple(depeg.get("quote_assets", [])),
            depeg_threshold_pct=float(depeg["threshold_pct"]),
            capital_on_exchange_cap=(
                None if d.get("capital_on_exchange_cap") is None
                else float(d["capital_on_exchange_cap"])
            ),
            human_heartbeat_days=int(d["human_heartbeat_days"]),
            leverage=int(d["leverage"]),
            exchange_assertions=dict(d.get("exchange_assertions", {})),
        )

    @classmethod
    def load(cls, path: str | Path) -> "RiskConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))
