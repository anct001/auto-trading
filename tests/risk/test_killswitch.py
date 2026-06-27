"""Tests for src/risk/killswitch.py — kill-switch + dead-man's switches (PLAN_RISK R5, §4).

Money code. The manual human kill overrides everything and is never cleared automatically; the
drawdown kill is non-overridable at runtime (only an explicit human re-arm). Two dead-man's
switches: a stale *process* heartbeat cancels resting entries; an absent *human* heartbeat
(≥ N days) halts.
"""
from __future__ import annotations

from src.risk import killswitch
from src.risk.config import RiskConfig


def _cfg(human_days=7):
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": human_days, "leverage": 0,
        "exchange_assertions": {},
    }
    return RiskConfig.from_dict(d)


def test_starts_armed():
    ks = killswitch.KillSwitch()
    assert not ks.is_halted and ks.allows_trading()


def test_manual_kill_halts_and_blocks_trading():
    ks = killswitch.KillSwitch()
    ks.manual_kill()
    assert ks.is_halted and not ks.allows_trading()
    assert ks.halt_reason == "manual_kill"


def test_re_arm_clears_halt_only_explicitly():
    ks = killswitch.KillSwitch()
    ks.manual_kill()
    ks.re_arm(operator="ops-alice")
    assert ks.allows_trading()
    assert ks.rearmed_by == "ops-alice"


def test_re_arm_requires_an_operator_identity():
    import pytest

    ks = killswitch.KillSwitch()
    ks.trip_drawdown()
    with pytest.raises(ValueError):
        ks.re_arm(operator="")  # cannot clear a halt without a named human
    assert ks.is_halted


def test_drawdown_kill_not_cleared_by_runtime_evaluation():
    ks = killswitch.KillSwitch()
    ks.trip_drawdown()
    assert ks.is_halted
    # a runtime dead-man's evaluation with everything healthy must NOT un-halt it
    ks.evaluate_dead_mans(process_heartbeat_age_s=0.0, human_heartbeat_age_days=0.0, cfg=_cfg())
    assert ks.is_halted and ks.halt_reason == "drawdown_kill"


def test_first_halt_reason_is_preserved():
    ks = killswitch.KillSwitch()
    ks.manual_kill()
    ks.trip_drawdown()  # already halted → original cause preserved
    assert ks.halt_reason == "manual_kill"


def test_human_heartbeat_absent_halts_at_threshold():
    ks = killswitch.KillSwitch()
    ks.evaluate_dead_mans(process_heartbeat_age_s=0.0, human_heartbeat_age_days=6.0, cfg=_cfg(7))
    assert ks.allows_trading()  # 6 days < 7
    ks.evaluate_dead_mans(process_heartbeat_age_s=0.0, human_heartbeat_age_days=7.0, cfg=_cfg(7))
    assert ks.is_halted and ks.halt_reason == "human_heartbeat"


def test_stale_process_heartbeat_cancels_resting_entries_without_halting():
    ks = killswitch.KillSwitch()
    ks.evaluate_dead_mans(
        process_heartbeat_age_s=120.0, human_heartbeat_age_days=0.0, cfg=_cfg(),
        process_timeout_s=60.0,
    )
    assert ks.cancel_resting_entries is True
    assert ks.allows_trading()  # cancelling entries is not a full halt


def test_recovered_process_heartbeat_clears_the_cancel_flag():
    ks = killswitch.KillSwitch()
    ks.evaluate_dead_mans(process_heartbeat_age_s=120.0, human_heartbeat_age_days=0.0, cfg=_cfg(),
                          process_timeout_s=60.0)
    ks.evaluate_dead_mans(process_heartbeat_age_s=1.0, human_heartbeat_age_days=0.0, cfg=_cfg(),
                          process_timeout_s=60.0)
    assert ks.cancel_resting_entries is False
