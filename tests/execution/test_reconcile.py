"""Tests for src/execution/reconcile.py — restart-safe reconciliation (E3, §9).

Money code. On restart the exchange is the source of truth (§9). Reconciliation adopts positions/
orders that exist on the exchange, drops local ghosts that no longer exist, never re-submits an
order that already exists (no double-trade), and flags a position with no protective stop as
naked (so a stop can be re-attached, §4).
"""
from __future__ import annotations

import math

from src.execution.reconcile import ExchangeTruth, LocalState, reconcile


def _truth(positions=None, open_orders=None):
    return ExchangeTruth(positions=positions or {}, open_orders=open_orders or [])


def _local(positions=None, open_order_ids=None):
    return LocalState(positions=positions or {}, open_order_ids=set(open_order_ids or []))


def _order(cid, pair, reduce_only=False):
    return {"clientOrderId": cid, "pair": pair, "reduceOnly": reduce_only}


def test_adopts_unknown_exchange_position():
    # an entry filled while the bot was down → exchange has a position the bot doesn't know
    res = reconcile(_local(), _truth(positions={"BTC/USDT": 0.5}))
    assert math.isclose(res.positions["BTC/USDT"], 0.5)
    assert res.adopted_positions == ["BTC/USDT"]


def test_exchange_truth_wins_on_quantity():
    res = reconcile(_local(positions={"BTC/USDT": 1.0}), _truth(positions={"BTC/USDT": 0.5}))
    assert math.isclose(res.positions["BTC/USDT"], 0.5)  # partial fill while down


def test_orphan_local_order_dropped():
    # local thinks order "x" is open, but the exchange has no such order (filled/canceled)
    res = reconcile(_local(open_order_ids=["x"]), _truth(open_orders=[]))
    assert res.orphan_local_order_ids == ["x"]


def test_existing_order_not_resubmitted():
    # order "x" is open on BOTH sides → it must NOT be treated as orphan (no double-trade)
    res = reconcile(
        _local(open_order_ids=["x"]),
        _truth(positions={"BTC/USDT": 1.0}, open_orders=[_order("x", "BTC/USDT", reduce_only=True)]),
    )
    assert res.orphan_local_order_ids == []


def test_naked_position_flagged():
    # a position with no reduceOnly stop on the exchange is naked
    res = reconcile(_local(), _truth(positions={"BTC/USDT": 1.0}, open_orders=[]))
    assert res.naked_positions == ["BTC/USDT"]


def test_position_with_protective_stop_is_not_naked():
    res = reconcile(
        _local(),
        _truth(positions={"BTC/USDT": 1.0}, open_orders=[_order("s1", "BTC/USDT", reduce_only=True)]),
    )
    assert res.naked_positions == []


def test_non_reduce_only_order_does_not_cover_position():
    # a resting entry order is not a protective stop → the position is still naked
    res = reconcile(
        _local(),
        _truth(positions={"BTC/USDT": 1.0}, open_orders=[_order("e1", "BTC/USDT", reduce_only=False)]),
    )
    assert res.naked_positions == ["BTC/USDT"]


def test_zero_quantity_position_not_naked():
    res = reconcile(_local(), _truth(positions={"BTC/USDT": 0.0}, open_orders=[]))
    assert res.naked_positions == []
