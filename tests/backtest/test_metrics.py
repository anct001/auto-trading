"""Tests for backtest/metrics.py — §8.7 metric suite.

Unambiguous quantities (total return, max drawdown, win rate, profit factor, expectancy, trade
count, the sample-size gate) are asserted exactly. Annualized ratios (CAGR/Calmar/Sharpe/
Sortino) are checked for finiteness/sign and for degenerate-input behavior (NaN), since their
exact value depends on the annualization convention.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backtest import metrics

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _equity(values):
    idx = pd.date_range(T0, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx, name="equity")


def _trades(returns):
    return pd.DataFrame({"return": returns})


def test_total_return_and_drawdown_exact():
    eq = _equity([1.0, 1.1, 1.05, 1.2])
    m = metrics.compute_metrics(eq, _trades([0.1, -0.0454, 0.142]))
    assert math.isclose(m["total_return"], 0.2, rel_tol=1e-9)
    # peak 1.1 → trough 1.05 = -4.5454...%
    assert math.isclose(m["max_drawdown"], 1.05 / 1.1 - 1.0, rel_tol=1e-9)


def test_calmar_consistent_with_cagr_and_drawdown():
    eq = _equity([1.0, 1.1, 1.05, 1.2])
    m = metrics.compute_metrics(eq, _trades([0.1]))
    assert np.isfinite(m["cagr"]) and m["cagr"] > 0  # equity grew
    assert math.isclose(m["calmar"], m["cagr"] / abs(m["max_drawdown"]), rel_tol=1e-9)


def test_sharpe_and_sortino_finite_on_mixed_returns():
    eq = _equity([1.0, 1.02, 1.01, 1.05, 1.03, 1.08])
    m = metrics.compute_metrics(eq, _trades([0.02, -0.01, 0.04]))
    assert np.isfinite(m["sharpe"])
    assert np.isfinite(m["sortino"])


def test_degenerate_constant_equity_gives_nan_ratios():
    eq = _equity([1.0, 1.0, 1.0, 1.0])  # zero variance, zero return
    m = metrics.compute_metrics(eq, _trades([]))
    assert math.isnan(m["sharpe"])
    assert math.isnan(m["sortino"])
    assert m["max_drawdown"] == 0.0


def test_trade_stats_exact():
    m = metrics.compute_metrics(_equity([1.0, 1.1]), _trades([0.2, -0.1, 0.3, -0.05]))
    assert m["trade_count"] == 4
    assert math.isclose(m["win_rate"], 0.5)
    # gains 0.5, losses 0.15 → PF 3.333...
    assert math.isclose(m["profit_factor"], 0.5 / 0.15, rel_tol=1e-9)
    assert math.isclose(m["expectancy"], (0.2 - 0.1 + 0.3 - 0.05) / 4, rel_tol=1e-9)


def test_profit_factor_infinite_when_no_losses():
    m = metrics.compute_metrics(_equity([1.0, 1.5]), _trades([0.2, 0.3]))
    assert math.isinf(m["profit_factor"])


def test_no_trades_gives_nan_trade_stats():
    m = metrics.compute_metrics(_equity([1.0, 1.0]), _trades([]))
    assert m["trade_count"] == 0
    assert math.isnan(m["win_rate"])
    assert math.isnan(m["expectancy"])


def test_sample_size_gate():
    assert metrics.meets_sample_size(_trades([0.0] * 100)) is True
    assert metrics.meets_sample_size(_trades([0.0] * 99)) is False
    assert metrics.meets_sample_size(_trades([0.0] * 50), min_trades=50) is True
