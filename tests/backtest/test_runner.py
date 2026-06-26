"""Tests for backtest/runner.py — §8 deterministic backtest (reproducible + costs).

A fake strategy supplies hand-crafted intents so the engine's fill timing and cost math can be
checked exactly. Two properties are load-bearing for the P0 gate:
  - reproducibility (§8.8): identical reruns produce identical trades + equity.
  - no look-ahead (§8.4): a signal at bar t fills at bar t+1's open, never on bar t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.runner import Costs, run_backtest
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


class FakeStrategy:
    """Returns a fixed list of intents aligned to the input frame."""

    target_regime = "any"

    def __init__(self, intents):
        self._intents = intents

    def generate_signals(self, df):
        return pd.Series(self._intents[: len(df)], index=df.index, dtype="object")


def _frame(opens):
    rows = []
    for i, o in enumerate(opens):
        rows.append([T0 + i * HOUR, o, o + 1, o - 1, o, 10.0])  # close == open keeps math clean
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


COSTS = Costs(taker_fee=0.001, maker_fee=0.001, slippage=0.0005)


def test_no_trades_when_all_hold():
    df = _frame([100, 101, 102, 103, 104])
    res = run_backtest(df, FakeStrategy([INTENT_HOLD] * 5), costs=COSTS)
    assert res.trades.empty
    assert np.allclose(res.equity_curve.to_numpy(), 1.0)  # flat equity, no costs
    assert res.stats["trade_count"] == 0


def test_single_round_trip_fills_at_next_open_with_costs():
    # enter signal at bar1 -> fills at bar2 open (100); exit signal at bar3 -> fills at bar4 open (100)
    df = _frame([90, 95, 100, 105, 100])
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_EXIT, INTENT_HOLD]
    res = run_backtest(df, FakeStrategy(intents), costs=COSTS)

    assert len(res.trades) == 1
    trade = res.trades.iloc[0]
    # fills are at the NEXT bar's open, not the signal bar (no look-ahead)
    assert trade["entry_time"] == T0 + 2 * HOUR
    assert trade["exit_time"] == T0 + 4 * HOUR

    # entry/exit open prices are equal (100) → return is pure cost drag
    factor = (1 - 0.001) ** 2 * (1 - 0.0005) / (1 + 0.0005)
    assert np.isclose(trade["return"], factor - 1.0)
    assert trade["return"] < 0


def test_costs_reduce_return_vs_zero_cost():
    df = _frame([90, 95, 100, 105, 100])
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_EXIT, INTENT_HOLD]
    with_costs = run_backtest(df, FakeStrategy(intents), costs=COSTS).trades.iloc[0]["return"]
    zero = Costs(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    no_costs = run_backtest(df, FakeStrategy(intents), costs=zero).trades.iloc[0]["return"]
    assert no_costs == 0.0  # same entry/exit price, no costs → flat
    assert with_costs < no_costs


def test_reproducible_identical_reruns():
    df = _frame([90, 95, 100, 105, 100, 98, 96, 99, 101])
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_EXIT,
               INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_HOLD, INTENT_EXIT]
    a = run_backtest(df, FakeStrategy(intents), costs=COSTS)
    b = run_backtest(df, FakeStrategy(intents), costs=COSTS)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    pd.testing.assert_series_equal(a.equity_curve, b.equity_curve)
    assert a.stats == b.stats


def test_open_position_force_closed_at_end():
    df = _frame([90, 95, 100, 105, 110])
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_HOLD, INTENT_HOLD]
    res = run_backtest(df, FakeStrategy(intents), costs=COSTS)
    assert len(res.trades) == 1
    assert res.trades.iloc[0]["exit_time"] == T0 + 4 * HOUR  # last bar
    # entered at open 100, marked out at last close 110 → gross positive despite costs
    assert res.trades.iloc[0]["return"] > 0


def test_max_drawdown_is_non_positive():
    df = _frame([100, 100, 100, 100, 100, 100])
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_HOLD, INTENT_HOLD, INTENT_EXIT]
    res = run_backtest(df, FakeStrategy(intents), costs=COSTS)
    assert res.stats["max_drawdown"] <= 0.0


def test_integration_with_real_ema_cross_is_deterministic():
    from src.strategy.ema_cross import EmaCross

    closes = [100, 100, 100, 101, 102, 103, 104, 103, 102, 101, 100, 99, 100, 101, 102]
    df = _frame(closes)
    a = run_backtest(df, EmaCross(ema_fast=3, ema_slow=5), costs=COSTS)
    b = run_backtest(df, EmaCross(ema_fast=3, ema_slow=5), costs=COSTS)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    assert a.stats == b.stats
