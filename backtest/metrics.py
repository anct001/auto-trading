"""backtest/metrics.py — §8.7 metric suite.

Computes the metrics the P0 gate reports from a backtest's equity curve + trades:
CAGR, max drawdown, Calmar, Sharpe, Sortino, win rate, profit factor, expectancy, trade count.
Per §0/§8 the **drawdown and Calmar are primary** — risk-adjusted-with-ruin-constraint, not raw
return. Annualization uses the bar cadence inferred from the equity index (override-able).

Degenerate inputs return NaN rather than a misleading number (e.g. Sharpe with zero variance,
profit factor with no losing trades is +inf). A number here is not proof of edge — §8.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_SECONDS_PER_YEAR = 365.25 * 24 * 3600


def infer_periods_per_year(index: pd.DatetimeIndex) -> float:
    """Bars per year from the median spacing of ``index`` (e.g. 1h → ~8766)."""
    if len(index) < 2:
        return float("nan")
    deltas = pd.Series(index).diff().dropna().dt.total_seconds()
    median = float(deltas.median())
    if median <= 0:
        return float("nan")
    return _SECONDS_PER_YEAR / median


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return float("nan")
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    if initial <= 0 or final <= 0:
        return float("nan")
    elapsed_s = (equity.index[-1] - equity.index[0]).total_seconds()
    years = elapsed_s / _SECONDS_PER_YEAR
    if years <= 0:
        return float("nan")
    try:
        return (final / initial) ** (1.0 / years) - 1.0
    except OverflowError:
        # annualizing a sub-day window blows up the exponent; meaningless but finite-safe.
        # Real P0 windows span months (§8.6), where this never triggers.
        return float("inf") if final > initial else float("-inf")


def _max_drawdown(equity: pd.Series) -> float:
    if len(equity) == 0:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def _sharpe(returns: pd.Series, ppy: float) -> float:
    if len(returns) < 2:
        return float("nan")
    std = returns.std(ddof=1)
    if std == 0 or np.isnan(std):
        return float("nan")
    return float(returns.mean() / std * np.sqrt(ppy))


def _sortino(returns: pd.Series, ppy: float) -> float:
    if len(returns) < 2:
        return float("nan")
    downside = np.minimum(returns, 0.0)
    dd = np.sqrt(np.mean(np.square(downside)))
    if dd == 0 or np.isnan(dd):
        return float("nan")
    return float(returns.mean() / dd * np.sqrt(ppy))


def _trade_stats(trades: pd.DataFrame) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "trade_count": 0,
            "win_rate": float("nan"),
            "profit_factor": float("nan"),
            "expectancy": float("nan"),
        }
    r = trades["return"].astype("float64")
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses == 0:
        profit_factor = float("inf") if gains > 0 else float("nan")
    else:
        profit_factor = float(gains / losses)
    return {
        "trade_count": int(n),
        "win_rate": float((r > 0).mean()),
        "profit_factor": profit_factor,
        "expectancy": float(r.mean()),
    }


def compute_metrics(
    equity_curve: pd.Series, trades: pd.DataFrame, *, periods_per_year: float | None = None
) -> dict:
    """Full metric dict from an equity curve and a trades frame (with a ``return`` column)."""
    ppy = periods_per_year if periods_per_year is not None else infer_periods_per_year(
        equity_curve.index
    )
    returns = equity_curve.pct_change().dropna()

    initial = float(equity_curve.iloc[0]) if len(equity_curve) else float("nan")
    final = float(equity_curve.iloc[-1]) if len(equity_curve) else float("nan")
    total_return = (final / initial - 1.0) if initial else float("nan")

    cagr = _cagr(equity_curve)
    mdd = _max_drawdown(equity_curve)
    calmar = (cagr / abs(mdd)) if (mdd and not np.isnan(mdd)) else float("nan")

    out = {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": mdd,
        "calmar": calmar,
        "sharpe": _sharpe(returns, ppy),
        "sortino": _sortino(returns, ppy),
    }
    out.update(_trade_stats(trades))
    return out


def meets_sample_size(trades: pd.DataFrame, min_trades: int = 100) -> bool:
    """§5/§8 sample-size gate: metrics are meaningless below ~100 trades."""
    return len(trades) >= min_trades
