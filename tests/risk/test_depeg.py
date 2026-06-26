"""Tests for src/risk/depeg.py — quote-stablecoin de-peg guard (PLAN_RISK R3, §4).

Money code. Equity/P&L are denominated in the quote stablecoin valued implicitly at $1; a de-peg
silently corrupts every risk/P&L number. On a de-peg beyond the threshold: **block new entries,
flag a re-mark, alert** — but still **allow exits** (we can leave, not enter — §13.16).
"""
from __future__ import annotations

import math

from src.risk import depeg
from src.risk.config import RiskConfig


def _cfg(threshold_pct=1.0):
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": threshold_pct},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    }
    return RiskConfig.from_dict(d)


def test_pegged_quote_allows_entries():
    res = depeg.check_depeg(0.999, _cfg(), is_entry=True)
    assert res.ok


def test_threshold_is_strict_at_exactly_one_percent():
    # deviation exactly 1.0% is NOT "beyond" the 1% threshold → still pegged
    assert depeg.assess_depeg(0.99, _cfg()).depegged is False
    assert depeg.check_depeg(0.99, _cfg(), is_entry=True).ok


def test_depeg_below_blocks_entries():
    res = depeg.check_depeg(0.985, _cfg(), is_entry=True)  # 1.5% below peg
    assert not res.ok and res.reason == "quote_depeg"


def test_depeg_above_also_blocks_entries():
    res = depeg.check_depeg(1.02, _cfg(), is_entry=True)  # 2% above peg
    assert not res.ok and res.reason == "quote_depeg"


def test_exit_allowed_during_depeg():
    # we can always leave a position even when the quote is de-pegged
    assert depeg.check_depeg(0.90, _cfg(), is_entry=False).ok


def test_assess_reports_deviation_and_remark():
    status = depeg.assess_depeg(0.95, _cfg())
    assert status.depegged is True
    assert math.isclose(status.deviation, 0.05, abs_tol=1e-9)
    assert status.remark_needed is True


def test_pegged_needs_no_remark():
    status = depeg.assess_depeg(1.001, _cfg())
    assert status.depegged is False
    assert status.remark_needed is False
