"""Tests for src/features/indicators.py — §15 single feature path (EMA first).

EMA is checked against a hand-computed reference and against pandas' recursive ewm semantics
(adjust=False, alpha = 2/(period+1)). The same function is the one the strategy and the backtest
use — there is no second EMA anywhere (no train-serve skew).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data import quality
from src.features import indicators

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def test_ema_matches_hand_computed_period_2():
    # alpha = 2/3; y0=1, y1=5/3, y2=23/9, y3=95/27
    out = indicators.ema([1, 2, 3, 4], 2)
    expected = [1.0, 5 / 3, 23 / 9, 95 / 27]
    assert np.allclose(out.to_numpy(), expected)


def test_ema_of_constant_is_constant():
    out = indicators.ema([7.0] * 10, 5)
    assert np.allclose(out.to_numpy(), 7.0)


def test_ema_length_preserved():
    out = indicators.ema(range(50), 10)
    assert len(out) == 50


@pytest.mark.parametrize("bad", [0, -1, -10])
def test_ema_rejects_nonpositive_period(bad):
    with pytest.raises(ValueError):
        indicators.ema([1, 2, 3], bad)


def _clean_frame(n=30, close0=100.0):
    rows = []
    for i in range(n):
        c = close0 + i
        rows.append([T0 + i * HOUR, c, c + 0.5, c - 0.5, c, 10.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_with_emas_adds_columns_from_close():
    df = _clean_frame(20)
    out = indicators.with_emas(df, periods=[5, 10])
    assert "ema_5" in out.columns and "ema_10" in out.columns
    assert len(out) == len(df)
    # ema_5 must equal the standalone ema() on close (single path)
    assert np.allclose(out["ema_5"].to_numpy(), indicators.ema(df["close"], 5).to_numpy())


def test_with_emas_consumes_quality_clean_frame():
    # integration: a quality-gated clean frame flows into the same feature path
    res = quality.check_quality(_clean_frame(30))
    out = indicators.with_emas(res.clean, periods=[12, 26])
    assert out["ema_12"].notna().all() and out["ema_26"].notna().all()
