"""Tests for src/risk/exchange_assert.py — exchange-config safety assertions (PLAN_RISK R4, §4).

Money code. The in-bot limits vanish if the VPS restarts or ccxt misbehaves, so on startup and
every reconnect the bot asserts the exchange-side account state matches intent: spot mode,
leverage = 1, margin/futures disabled, reduceOnly on exits. ANY mismatch → STOP, do not trade.
"""
from __future__ import annotations

import pytest

from src.risk import exchange_assert
from src.risk.config import RiskConfig


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {
            "spot_mode": True, "leverage": 1, "margin_disabled": True,
            "futures_disabled": True, "reduce_only_on_exit": True,
        },
    }
    return RiskConfig.from_dict(d)


def _good():
    return {
        "spot_mode": True, "leverage": 1, "margin_disabled": True,
        "futures_disabled": True, "reduce_only_on_exit": True,
    }


def test_matching_state_has_no_mismatches():
    assert exchange_assert.exchange_mismatches(_good(), _cfg()) == []


def test_matching_state_assert_does_not_raise():
    exchange_assert.assert_exchange_state(_good(), _cfg())  # must not raise


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("spot_mode", False),
        ("leverage", 2),
        ("margin_disabled", False),
        ("futures_disabled", False),
        ("reduce_only_on_exit", False),
    ],
)
def test_each_mismatch_is_detected(key, bad_value):
    actual = _good()
    actual[key] = bad_value
    mismatches = exchange_assert.exchange_mismatches(actual, _cfg())
    assert f"exchange_state:{key}" in mismatches


def test_missing_field_is_a_mismatch():
    actual = _good()
    del actual["leverage"]  # cannot confirm → must STOP
    assert "exchange_state:leverage" in exchange_assert.exchange_mismatches(actual, _cfg())


def test_assert_raises_and_reports_all_mismatches():
    actual = _good()
    actual["leverage"] = 5
    actual["margin_disabled"] = False
    with pytest.raises(exchange_assert.ExchangeStateError) as exc:
        exchange_assert.assert_exchange_state(actual, _cfg())
    assert set(exc.value.mismatches) >= {"exchange_state:leverage", "exchange_state:margin_disabled"}
