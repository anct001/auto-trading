"""Tests for src/risk/types.py — risk-engine core types (PLAN_RISK R0).

The types are the vocabulary the whole risk layer speaks. They validate their own inputs (a
malformed order can't exist) and expose the small derived quantities the limits use.
"""
from __future__ import annotations

import math

import pytest

from src.risk.types import Order, Position, PortfolioState, RiskDecision


# ---- Order -----------------------------------------------------------------------------------

def test_order_valid_and_notional():
    o = Order(pair="BTC/USDT", side="buy", qty=0.5, price=100.0, source="strategy")
    assert math.isclose(o.notional, 50.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"qty": 0.0},
        {"qty": -1.0},
        {"price": 0.0},
        {"price": -5.0},
        {"side": "long"},      # must be buy/sell
        {"source": "robot"},   # must be strategy/manual/llm
    ],
)
def test_order_rejects_invalid(kwargs):
    base = {"pair": "BTC/USDT", "side": "buy", "qty": 1.0, "price": 100.0, "source": "strategy"}
    base.update(kwargs)
    with pytest.raises(ValueError):
        Order(**base)


def test_order_source_manual_is_allowed():
    # the manual-UI path is a first-class source — it still goes through the same engine (Inv. 9)
    assert Order("BTC/USDT", "buy", 1.0, 100.0, "manual").source == "manual"


# ---- Position --------------------------------------------------------------------------------

def test_position_value():
    p = Position(pair="BTC/USDT", qty=2.0, entry_price=100.0)
    assert math.isclose(p.value(150.0), 300.0)


# ---- PortfolioState --------------------------------------------------------------------------

def test_portfolio_drawdown_and_day_return():
    state = PortfolioState(equity=90.0, peak_equity=100.0, day_start_equity=100.0)
    assert math.isclose(state.drawdown(), -0.10)
    assert math.isclose(state.day_return(), -0.10)


def test_portfolio_total_position_value():
    state = PortfolioState(
        equity=1000.0,
        peak_equity=1000.0,
        day_start_equity=1000.0,
        positions={"BTC/USDT": Position("BTC/USDT", 1.0, 100.0)},
    )
    assert math.isclose(state.total_position_value({"BTC/USDT": 120.0}), 120.0)


@pytest.mark.parametrize("kwargs", [{"equity": 0.0}, {"peak_equity": -1.0}, {"day_start_equity": 0.0}])
def test_portfolio_rejects_nonpositive(kwargs):
    base = {"equity": 100.0, "peak_equity": 100.0, "day_start_equity": 100.0}
    base.update(kwargs)
    with pytest.raises(ValueError):
        PortfolioState(**base)


# ---- RiskDecision ----------------------------------------------------------------------------

def test_decision_reject_carries_reasons_and_no_order():
    d = RiskDecision.reject("per_trade_cap", "drawdown_kill")
    assert d.approved is False
    assert d.sized is None
    assert list(d.reasons) == ["per_trade_cap", "drawdown_kill"]


def test_decision_approve_carries_sized_order():
    o = Order("BTC/USDT", "buy", 1.0, 100.0, "strategy")
    d = RiskDecision.approve(o)
    assert d.approved is True
    assert d.sized is o
    assert list(d.reasons) == []
