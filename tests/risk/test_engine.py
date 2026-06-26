"""Tests for src/risk/engine.py — THE single gate (PLAN_RISK R6, Invariant 3/9).

Money code. Every order — strategy, LLM-influenced, or manual UI — passes the SAME validate().
Entries (buys) run the full gauntlet, fail-closed, with all breach reasons aggregated. Exits
(sells) are risk-reducing and allowed (so flatten works during a halt) — but may never exceed
the held position (spot-only, no shorting, §0).
"""
from __future__ import annotations

from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, Position, PortfolioState


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


_GOOD_EXCHANGE = {
    "spot_mode": True, "leverage": 1, "margin_disabled": True,
    "futures_disabled": True, "reduce_only_on_exit": True,
}


def _state(equity=10000.0, positions=None, peak=None, day_start=None, quote=1.0):
    return PortfolioState(
        equity=equity, peak_equity=peak or equity, day_start_equity=day_start or equity,
        positions=positions or {}, quote_price=quote,
    )


def _ctx(prices, **over):
    kw = dict(prices=prices, exchange_state=dict(_GOOD_EXCHANGE), killswitch=KillSwitch())
    kw.update(over)
    return engine.RiskContext(**kw)


def _buy(pair="BTC/USDT", notional=1000.0):
    return Order(pair=pair, side="buy", qty=notional, price=1.0, source="strategy")


# ---- entries ---------------------------------------------------------------------------------

def test_clean_buy_is_approved():
    d = engine.validate(_buy(notional=1000.0), _state(), _cfg(), _ctx({"BTC/USDT": 1.0}))
    assert d.approved and d.sized is not None and not d.reasons


def test_halted_killswitch_blocks_buy():
    ks = KillSwitch()
    ks.manual_kill()
    d = engine.validate(_buy(), _state(), _cfg(), _ctx({"BTC/USDT": 1.0}, killswitch=ks))
    assert not d.approved and any("killswitch:manual_kill" == r for r in d.reasons)


def test_exchange_mismatch_blocks_buy():
    ctx = _ctx({"BTC/USDT": 1.0}, exchange_state={**_GOOD_EXCHANGE, "leverage": 2})
    d = engine.validate(_buy(), _state(), _cfg(), ctx)
    assert not d.approved and "exchange_state:leverage" in d.reasons


def test_depeg_blocks_buy():
    d = engine.validate(_buy(), _state(quote=0.95), _cfg(), _ctx({"BTC/USDT": 1.0}))
    assert not d.approved and "quote_depeg" in d.reasons


def test_per_asset_cap_blocks_buy():
    d = engine.validate(_buy(notional=2600.0), _state(), _cfg(), _ctx({"BTC/USDT": 1.0}))
    assert not d.approved and "per_asset_cap" in d.reasons


def test_daily_hard_blocks_buy():
    d = engine.validate(_buy(), _state(equity=960.0, day_start=1000.0), _cfg(), _ctx({"BTC/USDT": 1.0}))
    assert not d.approved and "daily_hard_limit" in d.reasons


def test_multiple_breaches_all_reported():
    ks = KillSwitch()
    ks.manual_kill()
    ctx = _ctx({"BTC/USDT": 1.0}, killswitch=ks, exchange_state={**_GOOD_EXCHANGE, "leverage": 2})
    d = engine.validate(_buy(notional=2600.0), _state(quote=0.95), _cfg(), ctx)
    assert not d.approved
    assert {"killswitch:manual_kill", "exchange_state:leverage", "quote_depeg", "per_asset_cap"} <= set(d.reasons)


def test_manual_over_limit_order_rejected_same_as_strategy():
    # the §14 drill: a manual order takes the same path and gets the same verdict as a bot order
    over = 2600.0
    strat = engine.validate(
        Order("BTC/USDT", "buy", over, 1.0, "strategy"), _state(), _cfg(), _ctx({"BTC/USDT": 1.0})
    )
    manual = engine.validate(
        Order("BTC/USDT", "buy", over, 1.0, "manual"), _state(), _cfg(), _ctx({"BTC/USDT": 1.0})
    )
    assert not manual.approved
    assert set(manual.reasons) == set(strat.reasons)


def test_feasibility_rechecked_when_market_provided():
    market = MarketConstraints(min_notional=50.0, lot_step=0.0001, tick_size=0.01)
    d = engine.validate(
        Order("BTC/USDT", "buy", 10.0, 1.0, "manual"),  # notional 10 < 50 min
        _state(), _cfg(), _ctx({"BTC/USDT": 1.0}, market=market),
    )
    assert not d.approved and "below_min_notional" in d.reasons


# ---- exits (risk-reducing; flatten must work even when halted) -------------------------------

def test_sell_within_position_approved():
    state = _state(positions={"BTC/USDT": Position("BTC/USDT", 1.0, 100.0)})
    d = engine.validate(Order("BTC/USDT", "sell", 0.5, 100.0, "strategy"), state, _cfg(), _ctx({"BTC/USDT": 100.0}))
    assert d.approved


def test_sell_allowed_during_halt_so_flatten_works():
    ks = KillSwitch()
    ks.manual_kill()
    state = _state(positions={"BTC/USDT": Position("BTC/USDT", 1.0, 100.0)})
    d = engine.validate(
        Order("BTC/USDT", "sell", 1.0, 100.0, "manual"), state, _cfg(),
        _ctx({"BTC/USDT": 100.0}, killswitch=ks),
    )
    assert d.approved  # flatten/exit is risk-reducing


def test_sell_more_than_held_rejected_no_shorting():
    state = _state(positions={"BTC/USDT": Position("BTC/USDT", 1.0, 100.0)})
    d = engine.validate(Order("BTC/USDT", "sell", 1.5, 100.0, "strategy"), state, _cfg(), _ctx({"BTC/USDT": 100.0}))
    assert not d.approved and "would_short" in d.reasons


def test_sell_without_position_rejected():
    d = engine.validate(Order("BTC/USDT", "sell", 1.0, 100.0, "strategy"), _state(), _cfg(), _ctx({"BTC/USDT": 100.0}))
    assert not d.approved and "no_position_to_sell" in d.reasons
