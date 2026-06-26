"""Tests for src/execution/stops.py — exchange-side protective stops at fill (E2, §4).

Money code. A crash/disconnect/dead process must never leave a naked position, so on a buy fill
a reduceOnly protective stop is attached exchange-side. reduceOnly guarantees it can only close
the long, never flip to a short (spot-only, §0). Idempotent: one stop per position.
"""
from __future__ import annotations

import math

from src.execution.stops import StopManager, protective_stop_price
from src.risk.sizing import MarketConstraints


class FakeExchange:
    def __init__(self):
        self.created = []

    def create_order(self, symbol, type, side, amount, price, params):
        self.created.append(
            {"symbol": symbol, "type": type, "side": side, "amount": amount,
             "price": price, "params": dict(params)}
        )
        return {"id": f"stop-{len(self.created)}", "status": "open"}


_MARKETS = {"BTC/USDT": MarketConstraints(min_notional=10.0, lot_step=0.001, tick_size=0.1)}


def test_protective_stop_price_from_atr():
    # entry - atr_stop_mult*ATR, floored to tick
    assert math.isclose(protective_stop_price(10000.0, 33.33, 2.0, 0.1), 9933.3, abs_tol=1e-9)


def test_attaches_reduce_only_sell_stop_at_fill():
    ex = FakeExchange()
    StopManager(ex, _MARKETS).attach(pair="BTC/USDT", qty=1.0, stop_price=9800.0, client_order_id="s1")
    call = ex.created[0]
    assert call["symbol"] == "BTC/USDT"
    assert call["side"] == "sell"           # a protective stop on a long is a sell
    assert call["params"]["reduceOnly"] is True
    assert math.isclose(call["params"]["stopPrice"], 9800.0, abs_tol=1e-9)


def test_stop_amount_and_price_rounded_to_market():
    ex = FakeExchange()
    StopManager(ex, _MARKETS).attach(pair="BTC/USDT", qty=1.23456, stop_price=9800.37, client_order_id="s1")
    call = ex.created[0]
    assert math.isclose(call["amount"], 1.234, abs_tol=1e-9)   # lot 0.001
    assert math.isclose(call["price"], 9800.3, abs_tol=1e-9)   # tick 0.1


def test_idempotent_no_duplicate_stop():
    ex = FakeExchange()
    mgr = StopManager(ex, _MARKETS)
    r1 = mgr.attach(pair="BTC/USDT", qty=1.0, stop_price=9800.0, client_order_id="dup")
    r2 = mgr.attach(pair="BTC/USDT", qty=1.0, stop_price=9800.0, client_order_id="dup")
    assert len(ex.created) == 1
    assert r1.exchange_id == r2.exchange_id


def test_attach_protective_computes_price_from_entry_and_atr():
    ex = FakeExchange()
    mgr = StopManager(ex, _MARKETS)
    res = mgr.attach_protective(
        pair="BTC/USDT", qty=1.0, entry_price=10000.0, atr=100.0, atr_stop_mult=2.0,
        client_order_id="s1",
    )
    assert math.isclose(res.stop_price, 9800.0, abs_tol=1e-9)  # 10000 - 2*100
    assert res.reduce_only is True
