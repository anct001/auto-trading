"""Tests for backtest/walkforward.py — §8.5 out-of-sample split + walk-forward.

Walk-forward rolls a train→test window forward and evaluates the strategy only on the
out-of-sample test segment. The build_strategy factory sees ONLY the train window (no
look-ahead into test). Aggregate OOS trades drive the §5/§8 sample-size gate.
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs
from backtest import walkforward
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")
COSTS = Costs(taker_fee=0.00075, maker_fee=0.00075, slippage=0.0005)


class FakeStrategy:
    target_regime = "any"

    def __init__(self, pattern):
        self._pattern = pattern

    def generate_signals(self, df):
        reps = (len(df) // len(self._pattern)) + 1
        seq = (self._pattern * reps)[: len(df)]
        return pd.Series(seq, index=df.index, dtype="object")


def _frame(n):
    rows = [[T0 + i * HOUR, 100 + i, 101 + i, 99 + i, 100 + i, 10.0] for i in range(n)]
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


# one round-trip per 5-bar test window
_PATTERN = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_HOLD, INTENT_EXIT, INTENT_HOLD]


def test_out_of_sample_split_sizes():
    df = _frame(100)
    train, test = walkforward.out_of_sample_split(df, train_frac=0.7)
    assert len(train) == 70 and len(test) == 30
    assert train["timestamp"].iloc[-1] < test["timestamp"].iloc[0]


def test_walk_forward_fold_count():
    df = _frame(20)
    res = walkforward.walk_forward(
        df, lambda _train: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    assert len(res.folds) == 3  # windows at test=[5:10],[10:15],[15:20]


def test_aggregate_trade_count_equals_sum_of_folds():
    df = _frame(20)
    res = walkforward.walk_forward(
        df, lambda _train: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    assert res.oos_metrics["trade_count"] == sum(f["metrics"]["trade_count"] for f in res.folds)
    assert res.oos_metrics["trade_count"] == 3


def test_build_strategy_sees_only_train_window():
    df = _frame(20)
    seen = []

    def build(train_df):
        seen.append((len(train_df), train_df["timestamp"].iloc[-1]))
        return FakeStrategy(_PATTERN)

    res = walkforward.walk_forward(df, build, costs=COSTS, train_size=5, test_size=5, step=5)
    # every train window is exactly train_size and ends strictly before its fold's test start
    for (n_train, train_last), fold in zip(seen, res.folds):
        assert n_train == 5
        assert train_last < fold["test_start"]


def test_walk_forward_is_deterministic():
    df = _frame(20)
    a = walkforward.walk_forward(
        df, lambda _t: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    b = walkforward.walk_forward(
        df, lambda _t: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    pd.testing.assert_frame_equal(a.oos_trades, b.oos_trades)
    assert a.oos_metrics == b.oos_metrics


def test_sample_size_flag_surfaced():
    df = _frame(20)
    res = walkforward.walk_forward(
        df, lambda _t: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    assert res.sample_size_ok is False  # only 3 OOS trades, well under 100
    assert res.oos_metrics["trade_count"] < 100


def test_too_short_yields_no_folds():
    df = _frame(8)
    res = walkforward.walk_forward(
        df, lambda _t: FakeStrategy(_PATTERN), costs=COSTS, train_size=5, test_size=5, step=5
    )
    assert res.folds == []
    assert res.oos_trades.empty
    assert res.sample_size_ok is False
