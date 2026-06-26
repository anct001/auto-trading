"""Tests for true_range / atr in src/features/indicators.py — §15 single feature path.

ATR is the volatility input the risk engine sizes from (PLAN_RISK R1). It must live on the one
shared feature path so the quality gate, the backtest, and live sizing all use the same true
range (no train-serve skew).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features import indicators

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _frame(rows):
    # rows: list of (high, low, close); open is irrelevant to TR/ATR
    data = []
    for i, (h, low, c) in enumerate(rows):
        data.append([T0 + i * HOUR, c, h, low, c, 10.0])
    return pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_true_range_hand_computed():
    # r0: no prev → high-low = 2
    # r1: max(12-10=2, |12-10|=2, |10-10|=0) = 2
    # r2: max(9-7=2, |9-11|=2, |7-11|=4) = 4
    df = _frame([(11, 9, 10), (12, 10, 11), (9, 7, 8)])
    tr = indicators.true_range(df)
    assert np.allclose(tr.to_numpy(), [2.0, 2.0, 4.0])


def test_atr_is_rolling_mean_of_true_range():
    df = _frame([(11, 9, 10), (12, 10, 11), (9, 7, 8), (10, 8, 9)])
    atr = indicators.atr(df, period=2)
    tr = indicators.true_range(df)
    expected = tr.rolling(2).mean()
    pd.testing.assert_series_equal(atr, expected, check_names=False)


def test_atr_constant_range_equals_that_range():
    # steady close, constant 2-wide bar → TR settles to 2 → ATR = 2
    rows = [(c + 1, c - 1, c) for c in [10, 10, 10, 10, 10]]
    atr = indicators.atr(_frame(rows), period=3)
    assert np.allclose(atr.dropna().to_numpy(), 2.0)


@pytest.mark.parametrize("bad", [0, -1])
def test_atr_rejects_nonpositive_period(bad):
    with pytest.raises(ValueError):
        indicators.atr(_frame([(11, 9, 10), (12, 10, 11)]), period=bad)
