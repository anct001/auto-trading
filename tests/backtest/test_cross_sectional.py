"""Tests for backtest/cross_sectional.py — cross-sectional momentum (relative value)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.cross_sectional import backtest_cross_sectional


def _panel(paths: dict[str, list[float]]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(next(iter(paths.values()))), freq="h", tz="UTC")
    return pd.DataFrame(paths, index=idx)


def test_picks_the_strongest_asset():
    n = 80
    up = [100 * 1.01 ** i for i in range(n)]      # strong uptrend
    flat = [100.0] * n
    down = [100 * 0.99 ** i for i in range(n)]
    res = backtest_cross_sectional(_panel({"UP": up, "FLAT": flat, "DOWN": down}),
                                   lookback=20, top_k=1, cost=0.0)
    assert res["total_return"] > 0                 # rotates into UP
    assert res["selections"][-1] == ["UP"]


def test_cost_drags_return():
    n = 80
    rng = np.random.default_rng(0)
    # two noisy assets that swap leadership often -> turnover -> cost bites
    a = list(100 * np.cumprod(1 + rng.normal(0, 0.02, n)))
    b = list(100 * np.cumprod(1 + rng.normal(0, 0.02, n)))
    panel = _panel({"A": a, "B": b})
    free = backtest_cross_sectional(panel, lookback=10, top_k=1, cost=0.0)
    costed = backtest_cross_sectional(panel, lookback=10, top_k=1, cost=0.005)
    assert costed["total_return"] <= free["total_return"]


def test_reports_shape_and_reproducible():
    n = 60
    panel = _panel({"X": [100 * 1.005 ** i for i in range(n)],
                    "Y": [100 * 1.002 ** i for i in range(n)]})
    a = backtest_cross_sectional(panel, lookback=15, top_k=1, cost=0.0007)
    b = backtest_cross_sectional(panel, lookback=15, top_k=1, cost=0.0007)
    assert a["total_return"] == b["total_return"]
    assert {"total_return", "sharpe", "max_drawdown", "avg_turnover"} <= a.keys()


def test_empty_or_too_short_safe():
    res = backtest_cross_sectional(_panel({"A": [1, 2, 3], "B": [1, 1, 1]}), lookback=20, top_k=1)
    assert res["total_return"] == 0.0
