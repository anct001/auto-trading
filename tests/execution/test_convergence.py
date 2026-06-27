"""Convergence test (E4) — Invariant 3/9 end-to-end.

Proves the only path from an order to the exchange is engine.validate → (approved) →
broker.submit. An unapproved or over-limit order never reaches the exchange. This is the
executable form of "one execution path, no backdoor".
"""
from __future__ import annotations

import pytest

from src.execution.broker import Broker
from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints, compute_size
from src.risk.types import Order, PortfolioState, RiskDecision


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                                "futures_disabled": True, "reduce_only_on_exit": True},
    }
    return RiskConfig.from_dict(d)


_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}
_MARKET = MarketConstraints(min_notional=10.0, lot_step=0.0001, tick_size=0.01)


class FakeExchange:
    def __init__(self):
        self.created = []

    def create_order(self, symbol, type, side, amount, price, params):
        self.created.append({"symbol": symbol, "side": side, "amount": amount})
        return {"id": f"ex-{len(self.created)}", "status": "open", "filled": 0.0, "amount": amount}


def _state():
    return PortfolioState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0, quote_price=1.0)


def _ctx(ks=None):
    return engine.RiskContext(prices={"BTC/USDT": 10000.0}, exchange_state=dict(_GOOD_EXCHANGE),
                              killswitch=ks or KillSwitch())


def test_approved_order_flows_through_to_the_exchange():
    sized = compute_size(pair="BTC/USDT", price=10000.0, atr=200.0, state=_state(),
                         cfg=_cfg(), market=_MARKET, atr_stop_mult=2.0)
    decision = engine.validate(sized.to_order(source="strategy"), _state(), _cfg(), _ctx())
    assert decision.approved

    ex = FakeExchange()
    Broker(ex, {"BTC/USDT": _MARKET}).submit(decision, client_order_id="c1")
    assert len(ex.created) == 1  # reached the exchange — only because it was approved


def test_over_limit_order_is_rejected_and_never_submitted():
    ex = FakeExchange()
    broker = Broker(ex, {"BTC/USDT": _MARKET})
    over = Order("BTC/USDT", "buy", 2600.0, 1.0, "manual")  # 26% > 25% per-asset cap
    decision = engine.validate(over, _state(), _cfg(), _ctx())
    assert not decision.approved and "per_asset_cap" in decision.reasons

    # the broker is the only door to the exchange, and it refuses an unapproved decision
    with pytest.raises(ValueError):
        broker.submit(decision, client_order_id="c1")
    assert ex.created == []


def test_halt_blocks_the_whole_pipeline():
    ks = KillSwitch()
    ks.manual_kill()
    ex = FakeExchange()
    broker = Broker(ex, {"BTC/USDT": _MARKET})
    sized = compute_size(pair="BTC/USDT", price=10000.0, atr=200.0, state=_state(),
                         cfg=_cfg(), market=_MARKET)
    decision = engine.validate(sized.to_order(source="strategy"), _state(), _cfg(), _ctx(ks=ks))
    assert not decision.approved
    with pytest.raises(ValueError):
        broker.submit(decision, client_order_id="c1")
    assert ex.created == []


def test_broker_cannot_be_handed_a_fabricated_approval_for_an_unsized_decision():
    # a RiskDecision with approved=True but no sized order is malformed → broker refuses
    ex = FakeExchange()
    broker = Broker(ex, {"BTC/USDT": _MARKET})
    with pytest.raises(ValueError):
        broker.submit(RiskDecision(approved=True, reasons=(), sized=None), client_order_id="c1")
    assert ex.created == []
