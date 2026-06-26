"""Tests for src/risk/config.py — RiskConfig loader + validation (PLAN_RISK R0).

RiskConfig is declarative config (§15): it is validated on load and refuses anything that would
violate a hard invariant (leverage>0 pre-P5) or that is internally inconsistent (soft daily
limit deeper than hard). This is the config side of the §15 integrity check — never applied to
runtime state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.risk.config import RiskConfig

_DEFAULT_PATH = Path(__file__).parents[2] / "config" / "risk" / "default.json"


def _default_dict():
    return json.loads(_DEFAULT_PATH.read_text())


def test_loads_project_default():
    cfg = RiskConfig.load(_DEFAULT_PATH)
    assert cfg.per_trade_risk_pct == 0.5
    assert cfg.max_concurrent_positions == 3
    assert cfg.per_asset_cap_pct == 25
    assert cfg.cluster_corr_threshold == 0.7
    assert cfg.cluster_exposure_cap_pct == 40
    assert cfg.daily_soft_pct == -2.0
    assert cfg.daily_hard_pct == -4.0
    assert cfg.max_drawdown_killswitch_pct == -12.0
    assert cfg.depeg_threshold_pct == 1.0
    assert cfg.human_heartbeat_days == 7
    assert cfg.leverage == 0


def test_rejects_leverage_nonzero():
    d = _default_dict()
    d["leverage"] = 1
    with pytest.raises(ValueError, match="leverage"):
        RiskConfig.from_dict(d)


def test_rejects_soft_hard_inversion():
    # soft must trigger BEFORE hard: |soft| < |hard| (both negative)
    d = _default_dict()
    d["daily_loss_limits"] = {"soft_pct": -5.0, "hard_pct": -4.0}
    with pytest.raises(ValueError):
        RiskConfig.from_dict(d)


def test_rejects_nonpositive_per_trade():
    d = _default_dict()
    d["per_trade_risk_pct"] = 0.0
    with pytest.raises(ValueError):
        RiskConfig.from_dict(d)


def test_rejects_kelly_above_half():
    d = _default_dict()
    d["max_fractional_kelly"] = 0.6
    with pytest.raises(ValueError):
        RiskConfig.from_dict(d)


def test_rejects_nonnegative_drawdown_kill():
    d = _default_dict()
    d["max_drawdown_killswitch_pct"] = 12.0  # must be negative
    with pytest.raises(ValueError):
        RiskConfig.from_dict(d)
