"""Tests for src/risk/sizing.py — position sizing + order-size feasibility (PLAN_RISK R1).

Money code. The load-bearing rule (§4/§9): on a small account, when the risk-capped size falls
below the exchange minimum, the trade is **skipped and flagged** — the size is NEVER rounded up
past the risk cap to meet the minimum. These tests pin that, plus inverse-ATR sizing, the
fractional-Kelly cap, and lot/tick rounding (always DOWN, never up).
"""
from __future__ import annotations

import math

import pytest

from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints, compute_size
from src.risk.types import Order, PortfolioState


def _cfg(**over):
    d = {
        "per_trade_risk_pct": 0.5,
        "position_sizing": "inverse_atr",
        "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3,
        "gross_exposure_pct": 100,
        "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None,
        "human_heartbeat_days": 7,
        "leverage": 0,
        "exchange_assertions": {},
    }
    d.update(over)
    return RiskConfig.from_dict(d)


def _state(equity):
    return PortfolioState(equity=equity, peak_equity=equity, day_start_equity=equity)


# generous constraints unless a test overrides them
_LOOSE = MarketConstraints(min_notional=10.0, lot_step=0.0001, tick_size=0.01)


def test_inverse_atr_sizing_math():
    # risk = 10000 * 0.5% = 50; stop = 2*ATR(100) = 200; qty = 50/200 = 0.25
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
        cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0,
    )
    assert res.feasible
    assert math.isclose(res.qty, 0.25, rel_tol=1e-9)
    assert math.isclose(res.notional, 2500.0, rel_tol=1e-9)


def test_higher_atr_gives_smaller_size():
    common = dict(pair="BTC/USDT", price=10000.0, state=_state(10000.0), cfg=_cfg(), market=_LOOSE)
    small_vol = compute_size(atr=100.0, **common)
    big_vol = compute_size(atr=200.0, **common)
    assert big_vol.qty < small_vol.qty


def test_fractional_kelly_cap_binds():
    # tiny ATR would size huge; cap notional at max_fractional_kelly (0.5) * equity
    res = compute_size(
        pair="ALT/USDT", price=100.0, atr=0.1, state=_state(10000.0),
        cfg=_cfg(), market=MarketConstraints(min_notional=10.0, lot_step=0.001, tick_size=0.01),
        atr_stop_mult=2.0,
    )
    assert res.feasible
    assert math.isclose(res.notional, 5000.0, rel_tol=1e-9)  # 50% of equity, capped
    assert math.isclose(res.qty, 50.0, rel_tol=1e-9)


def test_sub_minimum_size_is_skipped_not_rounded_up():
    # small account: risk=100*0.5%=0.5; stop=2*100=200; qty=0.0025; notional=0.0025*10000=25
    # exchange minNotional is 50 → 25 < 50 → SKIP. Rounding up to 50 would DOUBLE the risk → forbidden.
    market = MarketConstraints(min_notional=50.0, lot_step=0.0001, tick_size=0.01)
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(100.0),
        cfg=_cfg(), market=market, atr_stop_mult=2.0,
    )
    assert res.feasible is False
    assert res.reason == "below_min_notional"
    assert res.qty == 0.0  # no order; size NOT bumped up to meet the minimum


def test_below_lot_step_is_skipped():
    market = MarketConstraints(min_notional=1.0, lot_step=0.01, tick_size=0.01)
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(100.0),
        cfg=_cfg(), market=market, atr_stop_mult=2.0,
    )  # qty 0.0025 floors to 0 at lot_step 0.01
    assert res.feasible is False
    assert res.reason == "below_lot_step"


def test_lot_step_rounds_down():
    # raw qty 0.257-ish floored to lot_step 0.01 → 0.25, never 0.26
    market = MarketConstraints(min_notional=1.0, lot_step=0.01, tick_size=0.01)
    res = compute_size(
        pair="BTC/USDT", price=1000.0, atr=100.0, state=_state(51400.0),
        cfg=_cfg(), market=market, atr_stop_mult=2.0,
    )
    # risk=257; stop=200; raw qty=1.285 → floor to 1.28
    assert math.isclose(res.qty, 1.28, abs_tol=1e-9)


def test_tick_size_floors_price():
    res = compute_size(
        pair="BTC/USDT", price=10000.037, atr=100.0, state=_state(10000.0),
        cfg=_cfg(), market=MarketConstraints(min_notional=10.0, lot_step=0.0001, tick_size=0.01),
    )
    assert math.isclose(res.price, 10000.03, abs_tol=1e-9)


def test_zero_or_negative_atr_is_infeasible():
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=0.0, state=_state(10000.0), cfg=_cfg(), market=_LOOSE
    )
    assert res.feasible is False
    assert res.reason == "no_volatility"


def test_feasible_result_converts_to_valid_order():
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0), cfg=_cfg(), market=_LOOSE
    )
    order = res.to_order(source="manual")
    assert isinstance(order, Order)
    assert order.side == "buy" and order.source == "manual"
    assert math.isclose(order.qty, res.qty) and math.isclose(order.price, res.price)


def test_infeasible_result_cannot_make_order():
    market = MarketConstraints(min_notional=50.0, lot_step=0.0001, tick_size=0.01)
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(100.0), cfg=_cfg(), market=market
    )
    with pytest.raises(ValueError):
        res.to_order(source="strategy")


def test_realized_risk_never_exceeds_cap():
    # because qty is floored, the loss if the stop is hit must be <= the risk budget
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=137.0, state=_state(8000.0),
        cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0,
    )
    assert res.feasible
    risk_budget = 8000.0 * 0.005
    assert res.qty * (2.0 * 137.0) <= risk_budget + 1e-9
