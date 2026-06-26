"""Tests for src/risk/runtime.py — runtime tighten-only guard (PLAN_RISK R7, Invariant 3).

Money code. Runtime controls may only **tighten** a limit or **halt**, never **loosen**. A
loosening needs a config change + human sign-off, so it is refused at runtime. "Tighter" is
direction-aware: smaller per-trade/exposure caps are tighter; a daily/drawdown limit closer to
zero (fires sooner) is tighter.
"""
from __future__ import annotations

import pytest

from src.risk.config import RiskConfig
from src.risk.runtime import RuntimeLoosenError, apply_runtime_override


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    }
    return RiskConfig.from_dict(d)


def test_tighten_per_trade_applies():
    out = apply_runtime_override(_cfg(), per_trade_risk_pct=0.3)
    assert out.per_trade_risk_pct == 0.3


def test_loosen_per_trade_refused():
    with pytest.raises(RuntimeLoosenError, match="per_trade_risk_pct"):
        apply_runtime_override(_cfg(), per_trade_risk_pct=1.0)


def test_tighten_per_asset_and_gross_apply():
    out = apply_runtime_override(_cfg(), per_asset_cap_pct=10, gross_exposure_pct=50)
    assert out.per_asset_cap_pct == 10 and out.gross_exposure_pct == 50


def test_loosen_gross_refused():
    with pytest.raises(RuntimeLoosenError):
        apply_runtime_override(_cfg(), gross_exposure_pct=150)


def test_tighten_daily_soft_toward_zero_applies():
    out = apply_runtime_override(_cfg(), daily_soft_pct=-1.5)  # fires sooner → tighter
    assert out.daily_soft_pct == -1.5


def test_loosen_daily_soft_refused():
    with pytest.raises(RuntimeLoosenError):
        apply_runtime_override(_cfg(), daily_soft_pct=-3.0)  # more room → looser


def test_tighten_drawdown_applies_loosen_refused():
    assert apply_runtime_override(_cfg(), max_drawdown_killswitch_pct=-10.0).max_drawdown_killswitch_pct == -10.0
    with pytest.raises(RuntimeLoosenError):
        apply_runtime_override(_cfg(), max_drawdown_killswitch_pct=-15.0)


def test_fewer_concurrent_positions_is_tighter():
    assert apply_runtime_override(_cfg(), max_concurrent_positions=1).max_concurrent_positions == 1
    with pytest.raises(RuntimeLoosenError):
        apply_runtime_override(_cfg(), max_concurrent_positions=5)


def test_equal_value_is_a_noop():
    out = apply_runtime_override(_cfg(), per_trade_risk_pct=0.5)
    assert out.per_trade_risk_pct == 0.5


def test_one_loosening_among_tightenings_refuses_everything():
    with pytest.raises(RuntimeLoosenError):
        apply_runtime_override(_cfg(), per_trade_risk_pct=0.3, gross_exposure_pct=150)
