"""Tests for src/execution/broker.py — idempotent, precise, approval-gated submit (E1, §9).

Money code. The broker is downstream of the risk gate: it refuses anything that isn't an
approved RiskDecision (Inv. 3/9), it never double-trades a repeated client order id (§9
idempotency), and it rounds to the exchange's lot/tick before sending (§9 precision).
"""
from __future__ import annotations

import math

import pytest

from src.execution.broker import Broker
from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, PortfolioState, RiskDecision

_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}


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


class FakeExchange:
    def __init__(self):
        self.created = []

    def create_order(self, symbol, type, side, amount, price, params):
        self.created.append(
            {"symbol": symbol, "type": type, "side": side, "amount": amount,
             "price": price, "params": dict(params)}
        )
        return {"id": f"ex-{len(self.created)}", "status": "open",
                "filled": 0.0, "amount": amount, "clientOrderId": params.get("clientOrderId")}


_MARKETS = {"BTC/USDT": MarketConstraints(min_notional=10.0, lot_step=0.001, tick_size=0.1)}


def _approved(qty=1.234567, price=10000.456):
    """A REAL engine-minted approval (the only kind the broker accepts). Big equity so it passes."""
    order = Order("BTC/USDT", "buy", qty, price, "strategy")
    state = PortfolioState(equity=1e7, peak_equity=1e7, day_start_equity=1e7, quote_price=1.0)
    ctx = engine.RiskContext(prices={"BTC/USDT": price}, exchange_state=dict(_GOOD_EXCHANGE),
                             killswitch=KillSwitch())
    decision = engine.validate(order, state, _cfg(), ctx)
    assert decision.approved
    return decision


def test_refuses_unapproved_order():
    ex = FakeExchange()
    broker = Broker(ex, _MARKETS)
    with pytest.raises(ValueError):
        broker.submit(RiskDecision.reject("per_asset_cap"), client_order_id="c1")
    assert ex.created == []  # exchange never touched


def test_refuses_forged_public_approval():
    # a hand-built RiskDecision.approve (no engine capability) must NOT reach the exchange (Inv. 3)
    ex = FakeExchange()
    forged = RiskDecision.approve(Order("BTC/USDT", "buy", 1.0, 100.0, "strategy"))
    with pytest.raises(ValueError):
        Broker(ex, _MARKETS).submit(forged, client_order_id="c1")
    assert ex.created == []


def test_submits_approved_order_with_expected_args():
    ex = FakeExchange()
    Broker(ex, _MARKETS).submit(_approved(), client_order_id="c1", order_type="limit", tif="GTC")
    call = ex.created[0]
    assert call["symbol"] == "BTC/USDT" and call["side"] == "buy" and call["type"] == "limit"
    assert call["params"]["clientOrderId"] == "c1"
    assert call["params"]["timeInForce"] == "GTC"


def test_precision_rounds_qty_and_price_down():
    ex = FakeExchange()
    Broker(ex, _MARKETS).submit(_approved(qty=1.234567, price=10000.456), client_order_id="c1")
    call = ex.created[0]
    assert math.isclose(call["amount"], 1.234, abs_tol=1e-9)   # lot_step 0.001
    assert math.isclose(call["price"], 10000.4, abs_tol=1e-9)  # tick_size 0.1


def test_idempotent_same_client_id_submits_once():
    ex = FakeExchange()
    broker = Broker(ex, _MARKETS)
    r1 = broker.submit(_approved(), client_order_id="dup")
    r2 = broker.submit(_approved(), client_order_id="dup")
    assert len(ex.created) == 1      # second call did not reach the exchange
    assert r1.exchange_id == r2.exchange_id


def test_different_client_ids_submit_separately():
    ex = FakeExchange()
    broker = Broker(ex, _MARKETS)
    broker.submit(_approved(), client_order_id="a")
    broker.submit(_approved(), client_order_id="b")
    assert len(ex.created) == 2


def test_partial_fill_tracked_from_exchange_reply():
    class PartialExchange(FakeExchange):
        def create_order(self, symbol, type, side, amount, price, params):
            super().create_order(symbol, type, side, amount, price, params)
            return {"id": "ex-1", "status": "open", "filled": amount / 2, "amount": amount,
                    "clientOrderId": params.get("clientOrderId")}

    res = Broker(PartialExchange(), _MARKETS).submit(_approved(qty=1.0), client_order_id="c1")
    assert res.filled > 0 and res.remaining > 0
    assert math.isclose(res.filled + res.remaining, res.amount, rel_tol=1e-9)
