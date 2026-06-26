"""Tests for src/execution/broker.py — idempotent, precise, approval-gated submit (E1, §9).

Money code. The broker is downstream of the risk gate: it refuses anything that isn't an
approved RiskDecision (Inv. 3/9), it never double-trades a repeated client order id (§9
idempotency), and it rounds to the exchange's lot/tick before sending (§9 precision).
"""
from __future__ import annotations

import math

import pytest

from src.execution.broker import Broker
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, RiskDecision


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
    return RiskDecision.approve(Order("BTC/USDT", "buy", qty, price, "strategy"))


def test_refuses_unapproved_order():
    ex = FakeExchange()
    broker = Broker(ex, _MARKETS)
    with pytest.raises(ValueError):
        broker.submit(RiskDecision.reject("per_asset_cap"), client_order_id="c1")
    assert ex.created == []  # exchange never touched


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
