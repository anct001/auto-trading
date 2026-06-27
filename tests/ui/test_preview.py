"""Tests for src/ui/preview.py — manual-order pre-trade risk preview (§12, Inv. 9).

The UI preview MUST run the exact same checks the engine applies on send — no UI backdoor around
the limits. So the preview's verdict is asserted to equal `engine.validate` directly, and a
hard-limit failure leaves the order disallowed.
"""
from __future__ import annotations

from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, PortfolioState, Position
from src.ui.preview import preview_manual_order

PAIR = "BTC/JPY"
_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}
_MARKET = MarketConstraints(min_notional=500.0, lot_step=1e-6, tick_size=1.0)


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": dict(_GOOD_EXCHANGE),
    }
    return RiskConfig.from_dict(d)


def _state(positions=None):
    return PortfolioState(equity=1_000_000.0, peak_equity=1_000_000.0,
                          day_start_equity=1_000_000.0, quote_price=1.0, positions=positions or {})


def _ctx(ks=None, price=10_000_000.0):
    return engine.RiskContext(prices={PAIR: price}, exchange_state=dict(_GOOD_EXCHANGE),
                              killswitch=ks or KillSwitch(), market=_MARKET)


def test_within_limits_buy_is_allowed_and_routes_as_manual():
    state, ctx = _state(), _ctx()
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.001, price=10_000_000.0,
                                state=state, cfg=_cfg(), ctx=ctx)
    assert prev.allowed and prev.reasons == ()
    assert prev.order.source == "manual"  # routes through the engine like any order (Inv. 9)
    assert "notional" in prev.metrics and "gross_exposure_after_pct" in prev.metrics


def test_preview_verdict_equals_engine_exactly():
    state, cfg, ctx = _state(), _cfg(), _ctx()
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.001, price=10_000_000.0,
                                state=state, cfg=cfg, ctx=ctx)
    decision = engine.validate(Order(PAIR, "buy", 0.001, 10_000_000.0, "manual"), state, cfg, ctx)
    assert prev.allowed == decision.approved
    assert prev.reasons == tuple(decision.reasons)


def test_killswitch_blocks_and_is_explained():
    ks = KillSwitch()
    ks.manual_kill()
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.001, price=10_000_000.0,
                                state=_state(), cfg=_cfg(), ctx=_ctx(ks=ks))
    assert not prev.allowed
    assert any("killswitch" in r for r in prev.reasons)


def test_oversized_buy_breaches_a_cap():
    # buy ~50% of equity in one asset → exceeds the 25% per-asset cap
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.05, price=10_000_000.0,
                                state=_state(), cfg=_cfg(), ctx=_ctx())
    assert not prev.allowed and prev.reasons  # at least one cap reason present


def test_sell_with_no_position_is_rejected():
    prev = preview_manual_order(pair=PAIR, side="sell", qty=0.001, price=10_000_000.0,
                                state=_state(), cfg=_cfg(), ctx=_ctx())
    assert not prev.allowed
    assert "no_position_to_sell" in prev.reasons


def test_invalid_order_inputs_fail_closed_without_raising():
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.0, price=10_000_000.0,
                                state=_state(), cfg=_cfg(), ctx=_ctx())
    assert not prev.allowed
    assert prev.order is None
    assert any("invalid_order" in r for r in prev.reasons)


def test_metrics_report_exposure_and_daily_headroom():
    state = _state(positions={PAIR: Position(PAIR, 0.001, 9_000_000.0)})
    prev = preview_manual_order(pair=PAIR, side="buy", qty=0.001, price=10_000_000.0,
                                state=state, cfg=_cfg(), ctx=_ctx())
    m = prev.metrics
    assert m["per_asset_after_pct"] > 0
    assert "daily_hard_pct" in m and "drawdown_pct" in m
