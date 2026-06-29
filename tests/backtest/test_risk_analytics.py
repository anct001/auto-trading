"""Tests for backtest/risk_analytics.py — VaR / CVaR + Monte-Carlo drawdown (pro risk reporting)."""
from __future__ import annotations

import math

import numpy as np

from backtest.risk_analytics import (
    bootstrap_max_drawdown,
    conditional_var,
    value_at_risk,
)

# a returns sample with a clear left tail
R = [-0.10, -0.06, -0.03, -0.01, 0.0, 0.01, 0.02, 0.02, 0.03, 0.05]


def test_var_is_a_left_tail_quantile():
    v95 = value_at_risk(R, level=0.95)
    assert math.isclose(v95, float(np.quantile(np.array(R), 0.05)), rel_tol=1e-9)
    assert v95 < 0  # a loss


def test_var_more_extreme_at_higher_confidence():
    assert value_at_risk(R, level=0.99) <= value_at_risk(R, level=0.95)


def test_cvar_is_at_least_as_bad_as_var():
    v = value_at_risk(R, level=0.90)
    c = conditional_var(R, level=0.90)
    assert c <= v  # expected shortfall sits in the tail beyond VaR


def test_empty_returns_safe():
    assert value_at_risk([], level=0.95) == 0.0
    assert conditional_var([], level=0.95) == 0.0


def test_bootstrap_drawdown_reproducible_and_ordered():
    rng_returns = [0.01, -0.02, 0.015, -0.03, 0.02, -0.01, 0.005, -0.025] * 10
    a = bootstrap_max_drawdown(rng_returns, n=500, seed=42)
    b = bootstrap_max_drawdown(rng_returns, n=500, seed=42)
    assert a == b                              # seeded → reproducible
    assert a["p50"] <= 0 and a["p95"] <= a["p50"] and a["p99"] <= a["p95"]  # deeper at higher pct
    assert a["samples"] == 500


def test_bootstrap_empty_safe():
    d = bootstrap_max_drawdown([], n=100, seed=1)
    assert d["p50"] == 0.0 and d["p95"] == 0.0 and d["p99"] == 0.0
