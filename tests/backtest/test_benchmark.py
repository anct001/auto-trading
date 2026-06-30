"""Tests for backtest/benchmark.py — buy-and-hold baseline (the honest comparison)."""
from __future__ import annotations

import pandas as pd

from backtest.benchmark import buy_and_hold
from backtest.runner import Costs

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _frame(closes):
    return pd.DataFrame({
        "timestamp": [T0 + i * HOUR for i in range(len(closes))],
        "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes, "volume": [10.0] * len(closes),
    })


def test_rising_market_positive_hold_return():
    closes = [100 * (1.01 ** i) for i in range(50)]
    bh = buy_and_hold(_frame(closes), costs=Costs(taker_fee=0.001, slippage=0.0005))
    assert bh["total_return"] > 0
    assert bh["sharpe"] > 0
    assert bh["max_drawdown"] >= -0.01   # essentially monotonic up
    assert bh["periods"] == 50


def test_costs_reduce_hold_return():
    closes = [100 * (1.01 ** i) for i in range(30)]
    free = buy_and_hold(_frame(closes), costs=Costs(taker_fee=0.0, slippage=0.0))
    costed = buy_and_hold(_frame(closes), costs=Costs(taker_fee=0.002, slippage=0.001))
    assert costed["total_return"] < free["total_return"]


def test_falling_market_negative():
    closes = [100 * (0.99 ** i) for i in range(40)]
    bh = buy_and_hold(_frame(closes), costs=Costs())
    assert bh["total_return"] < 0 and bh["max_drawdown"] < 0


def test_empty_safe():
    bh = buy_and_hold(_frame([]), costs=Costs())
    assert bh["total_return"] == 0.0 and bh["periods"] == 0
