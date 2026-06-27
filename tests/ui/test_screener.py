"""Tests for src/ui/screener.py — operator research screener (§12).

Read-only: filter the universe by indicator conditions to surface candidates. It NEVER trades —
a surfaced pair still has to pass backtest + walk-forward before joining the traded set. Pins the
condition evaluation (ALL must hold; missing/NaN field → no match) and the OHLCV readouts.
"""
from __future__ import annotations

import pandas as pd

from src.ui.screener import Condition, readouts_from_ohlcv, screen

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def test_evaluate_all_conditions_must_hold():
    uni = {
        "A": {"atr_pct": 3.0, "ema_fast_above_slow": True},
        "B": {"atr_pct": 1.0, "ema_fast_above_slow": True},
        "C": {"atr_pct": 5.0, "ema_fast_above_slow": False},
    }
    conds = [Condition("atr_pct", ">", 2.0), Condition("ema_fast_above_slow", "is_true", None)]
    assert screen(uni, conds) == ["A"]  # B fails atr_pct, C fails the cross


def test_missing_or_nan_field_does_not_match():
    uni = {"A": {"atr_pct": float("nan")}, "B": {}}
    assert screen(uni, [Condition("atr_pct", ">", 0.0)]) == []


def test_operators():
    r = {"x": 10.0}
    assert Condition("x", ">=", 10.0).holds(r)
    assert Condition("x", "<=", 10.0).holds(r)
    assert not Condition("x", "<", 10.0).holds(r)
    assert Condition("x", "==", 10.0).holds(r)
    assert Condition("x", "!=", 5.0).holds(r)


def test_no_conditions_returns_all_sorted():
    assert screen({"B": {}, "A": {}}, []) == ["A", "B"]


def _uptrend(n=60):
    rows, price = [], 100.0
    for i in range(n):
        price *= 1.01
        rows.append([T0 + i * HOUR, price, price * 1.002, price * 0.998, price, 10.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_readouts_from_ohlcv_uptrend():
    r = readouts_from_ohlcv(_uptrend(), ema_fast=12, ema_slow=26, atr_period=14, lookback=24)
    assert r["ema_fast_above_slow"] is True       # momentum up
    assert r["atr_pct"] > 0
    assert r["return_lookback_pct"] > 0           # rose over the window
    assert r["close"] > 0


def test_readouts_feed_the_screener():
    uni = {"UP": readouts_from_ohlcv(_uptrend())}
    hits = screen(uni, [Condition("ema_fast_above_slow", "is_true", None),
                        Condition("return_lookback_pct", ">", 0.0)])
    assert hits == ["UP"]
