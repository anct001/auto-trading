"""backtest/cross_sectional.py — cross-sectional momentum (relative value) backtest (§5/§8).

A different *information source* than the single-asset TA strategies (which had no edge): rank a
basket each bar by trailing return and hold the strongest ``top_k`` (equal-weight, long-or-flat,
spot-only). Returns are earned causally — rank from data up to t-1, hold through t's move — and
turnover costs the configured fee. Pure/deterministic. This is research infrastructure to *test*
whether relative strength across pairs has edge; a green number must still clear walk-forward +
deflated-Sharpe before it means anything (§5).
"""
from __future__ import annotations

import pandas as pd

from backtest.metrics import _max_drawdown, _sharpe, infer_periods_per_year


def backtest_cross_sectional(
    closes: pd.DataFrame, *, lookback: int = 30, top_k: int = 1, cost: float = 0.00075,
    periods_per_year: float | None = None,
) -> dict:
    """Equal-weight long the top-``top_k`` pairs by trailing-``lookback`` return; rebalance each bar.

    ``closes`` is a DataFrame of aligned close prices (columns = pairs). Cost is charged on the
    fraction of the book that turns over when the selected set changes.
    """
    n = len(closes)
    cols = list(closes.columns)
    if n <= lookback + 1 or len(cols) == 0:
        return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "avg_turnover": 0.0, "selections": [], "periods": n}

    rets = closes.pct_change()
    equity = 1.0
    curve, selections, turnovers = [], [], []
    prev: set[str] = set()
    for t in range(n):
        if t <= lookback:
            curve.append(equity)
            continue
        mom = closes.iloc[t - 1] / closes.iloc[t - 1 - lookback] - 1.0  # known at t-1 (causal)
        sel = list(mom.sort_values(ascending=False).index[:top_k])
        turnover = len(set(sel) - prev) / float(top_k)  # fraction newly bought
        port_ret = float(rets.iloc[t][sel].mean())      # earn t's move on the t-1 selection
        equity *= (1.0 + port_ret) * (1.0 - turnover * cost)
        curve.append(equity)
        selections.append(sel)
        turnovers.append(turnover)
        prev = set(sel)

    eq = pd.Series(curve, index=closes.index)
    ppy = periods_per_year if periods_per_year is not None else infer_periods_per_year(closes.index)
    return {
        "total_return": float(eq.iloc[-1] - 1.0),
        "sharpe": float(_sharpe(eq.pct_change().dropna(), ppy)),
        "max_drawdown": float(_max_drawdown(eq)),
        "avg_turnover": float(sum(turnovers) / len(turnovers)) if turnovers else 0.0,
        "selections": selections,
        "periods": n,
    }
