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
from src.risk.types import Order, Position, PortfolioState


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
    return PortfolioState(equity=equity, peak_equity=equity, day_start_equity=equity, quote_price=1.0)


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


def test_size_multiplier_default_is_no_op():
    # default 1.0 must reproduce the unmodified sizing math exactly (P0 path unchanged)
    common = dict(pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
                  cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0)
    base = compute_size(**common)
    with_default = compute_size(size_multiplier=1.0, **common)
    assert (with_default.qty, with_default.notional) == (base.qty, base.notional)


def test_size_multiplier_shrinks_the_risk_budget():
    # 0.5 multiplier halves the risk budget → halves the qty (50/200 → 0.125)
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
        cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0, size_multiplier=0.5,
    )
    assert res.feasible
    assert math.isclose(res.qty, 0.125, rel_tol=1e-9)


def test_size_multiplier_above_one_is_clamped_never_grows():
    # the LLM must never be able to GROW a position (Inv. 1/3): >1.0 clamps to the base size
    common = dict(pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
                  cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0)
    base = compute_size(**common)
    grown = compute_size(size_multiplier=2.0, **common)
    assert grown.qty == base.qty


def test_size_multiplier_can_push_below_minimum_and_skip():
    # shrinking a near-minimum size below min_notional must SKIP, not round up
    market = MarketConstraints(min_notional=2000.0, lot_step=0.0001, tick_size=0.01)
    common = dict(pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
                  cfg=_cfg(), market=market, atr_stop_mult=2.0)
    assert compute_size(**common).feasible  # 2500 notional clears 2000
    haircut = compute_size(size_multiplier=0.5, **common)  # → ~1250 notional
    assert not haircut.feasible
    assert haircut.reason == "below_min_notional"


def test_non_positive_size_multiplier_is_infeasible():
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
        cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0, size_multiplier=0.0,
    )
    assert not res.feasible


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


def test_non_positive_price_is_infeasible():
    res = compute_size(
        pair="BTC/USDT", price=0.0, atr=100.0, state=_state(10000.0), cfg=_cfg(), market=_LOOSE
    )
    assert res.feasible is False and res.reason == "invalid_price"


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


_ALT_MARKET = MarketConstraints(min_notional=10.0, lot_step=0.001, tick_size=0.01)


def test_clamp_per_asset_is_opt_in_default_off_preserves_kelly():
    # default (no clamp): tiny ATR sizes up to the 50% Kelly cap = 5000 notional (unchanged P0 path)
    res = compute_size(pair="ALT/USDT", price=100.0, atr=0.1, state=_state(10000.0),
                       cfg=_cfg(), market=_ALT_MARKET, atr_stop_mult=2.0)
    assert math.isclose(res.notional, 5000.0, rel_tol=1e-9)


def test_clamp_per_asset_limits_notional_to_the_cap():
    # with the clamp on, the same over-cap size is reduced to the 25% per-asset cap = 2500 notional
    res = compute_size(pair="ALT/USDT", price=100.0, atr=0.1, state=_state(10000.0),
                       cfg=_cfg(), market=_ALT_MARKET, atr_stop_mult=2.0, clamp_per_asset=True)
    assert res.feasible
    assert math.isclose(res.notional, 2500.0, rel_tol=1e-9)   # 25% of 10000, not 50%
    assert math.isclose(res.qty, 25.0, rel_tol=1e-9)


def test_clamp_per_asset_accounts_for_existing_exposure():
    # an existing position already uses part of the 25% cap; the clamp leaves only the headroom
    st = PortfolioState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0,
                        quote_price=1.0, positions={"ALT/USDT": Position("ALT/USDT", 15.0, 100.0)})
    # existing value 15*100=1500; cap 2500; headroom 1000 → notional clamped to 1000
    res = compute_size(pair="ALT/USDT", price=100.0, atr=0.1, state=st, cfg=_cfg(),
                       market=_ALT_MARKET, atr_stop_mult=2.0, clamp_per_asset=True)
    assert res.feasible and math.isclose(res.notional, 1000.0, rel_tol=1e-9)


def test_clamp_per_asset_skips_when_already_at_cap():
    # asset already at/over the 25% cap → no headroom → SKIP with per_asset_cap (not a forced trade)
    st = PortfolioState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0,
                        quote_price=1.0, positions={"ALT/USDT": Position("ALT/USDT", 30.0, 100.0)})
    res = compute_size(pair="ALT/USDT", price=100.0, atr=0.1, state=st, cfg=_cfg(),
                       market=_ALT_MARKET, atr_stop_mult=2.0, clamp_per_asset=True)
    assert not res.feasible and res.reason == "per_asset_cap"


def test_clamp_per_asset_never_grows_when_size_under_cap():
    # when the risk-based size is already under the cap, the clamp changes nothing (tighten-only)
    common = dict(pair="BTC/USDT", price=10000.0, atr=100.0, state=_state(10000.0),
                  cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0)
    base = compute_size(**common)                       # 2500 notional (= 25%, at the boundary)
    clamped = compute_size(clamp_per_asset=True, **common)
    assert (clamped.qty, clamped.notional) == (base.qty, base.notional)


def test_clamped_order_is_accepted_by_the_engine_no_per_asset_rejection():
    # the whole point: a clamped entry passes the engine's per-asset check instead of being rejected
    from src.risk import engine
    res = compute_size(pair="ALT/USDT", price=100.0, atr=0.1, state=_state(10000.0),
                       cfg=_cfg(), market=_ALT_MARKET, atr_stop_mult=2.0, clamp_per_asset=True)
    ctx = engine.RiskContext(prices={"ALT/USDT": 100.0},
                             exchange_state={"spot_mode": True, "leverage": 1,
                                             "margin_disabled": True, "futures_disabled": True,
                                             "reduce_only_on_exit": True},
                             market=_ALT_MARKET)
    decision = engine.validate(res.to_order(), _state(10000.0), _cfg(), ctx)
    assert decision.approved and "per_asset_cap" not in decision.reasons


def test_realized_risk_never_exceeds_cap():
    # because qty is floored, the loss if the stop is hit must be <= the risk budget
    res = compute_size(
        pair="BTC/USDT", price=10000.0, atr=137.0, state=_state(8000.0),
        cfg=_cfg(), market=_LOOSE, atr_stop_mult=2.0,
    )
    assert res.feasible
    risk_budget = 8000.0 * 0.005
    assert res.qty * (2.0 * 137.0) <= risk_budget + 1e-9
