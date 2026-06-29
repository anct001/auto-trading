"""backtest/risk_analytics.py — VaR / CVaR + Monte-Carlo drawdown (professional risk reporting).

Risk analytics a professional desk expects beyond the headline Sharpe/Calmar (backtest/metrics):

- **Value at Risk (VaR)** — the left-tail quantile of returns at a confidence level (e.g. the 5th
  percentile for 95% VaR). Negative = a loss.
- **Conditional VaR / Expected Shortfall (CVaR)** — the mean of returns in the tail at/below VaR;
  always at least as bad as VaR, and the metric that actually captures tail severity.
- **Bootstrap max-drawdown distribution** — resample the return series (i.i.d. bootstrap) into
  many synthetic equity paths and report the drawdown you'd expect at the p50/p95/p99 worst case.
  A single backtest's drawdown is one draw; this shows the *distribution* you're exposed to.

Pure/deterministic (the bootstrap is seeded → reproducible, §8). Operates on a return series
(per-trade or per-period); never trades. These feed reporting + the §14 go-live risk review.
"""
from __future__ import annotations

import numpy as np


def value_at_risk(returns, *, level: float = 0.95) -> float:
    """Historical VaR: the ``(1-level)`` quantile of returns (negative = loss). 0.0 if empty."""
    r = np.asarray(returns, dtype="float64")
    if r.size == 0:
        return 0.0
    return float(np.quantile(r, 1.0 - level))


def conditional_var(returns, *, level: float = 0.95) -> float:
    """Expected shortfall: mean of returns at or below the VaR threshold. 0.0 if empty."""
    r = np.asarray(returns, dtype="float64")
    if r.size == 0:
        return 0.0
    threshold = np.quantile(r, 1.0 - level)
    tail = r[r <= threshold]
    return float(tail.mean()) if tail.size else float(threshold)


def _max_drawdown(equity: np.ndarray) -> float:
    """Peak-to-trough max drawdown of an equity path (≤ 0)."""
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def bootstrap_max_drawdown(returns, *, n: int = 1000, seed: int = 0) -> dict:
    """Distribution of max drawdown over ``n`` i.i.d.-bootstrapped equity paths (seeded).

    Returns p50/p95/p99 of the (negative) max drawdown — p99 is the deeper, worse tail.
    """
    r = np.asarray(returns, dtype="float64")
    if r.size == 0:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "samples": 0}
    rng = np.random.default_rng(seed)
    dds = np.empty(n, dtype="float64")
    for i in range(n):
        sample = rng.choice(r, size=r.size, replace=True)
        equity = np.cumprod(1.0 + sample)
        dds[i] = _max_drawdown(equity)
    return {
        "p50": float(np.quantile(dds, 0.50)),
        "p95": float(np.quantile(dds, 0.05)),   # 5th pct of drawdowns = the 95%-worst case
        "p99": float(np.quantile(dds, 0.01)),
        "samples": n,
    }
