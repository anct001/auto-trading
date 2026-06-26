"""backtest/walkforward.py — §8.5 out-of-sample split + walk-forward.

Tune on A, test blind on B, roll forward. The ``build_strategy`` factory sees only the *train*
window of each fold and is evaluated on the following *test* window, so no test bar ever informs
the strategy that trades it (the cross-fold form of no-look-ahead, §8.4). Out-of-sample trades
are aggregated to drive the §5/§8 sample-size gate.

Deflated-Sharpe / multiple-testing correction (§5) — which scales the bar by the number of
hypotheses tried (from the §15 journal) — plugs in at P2 when LLM-proposed strategies arrive;
the walk-forward mechanism it builds on lives here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from backtest.metrics import compute_metrics, meets_sample_size
from backtest.runner import Costs, run_backtest

BuildStrategy = Callable[[pd.DataFrame], object]


def out_of_sample_split(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` chronologically into (train, test). No shuffling — time order is sacred."""
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    cut = int(len(df) * train_frac)
    return df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[dict]  # per fold: test_start, test_end, metrics, trades
    oos_trades: pd.DataFrame  # all out-of-sample trades concatenated
    oos_equity: pd.Series  # compounded out-of-sample equity curve
    oos_metrics: dict
    sample_size_ok: bool


def walk_forward(
    df: pd.DataFrame,
    build_strategy: BuildStrategy,
    *,
    costs: Costs | None = None,
    train_size: int,
    test_size: int,
    step: int | None = None,
    min_trades: int = 100,
) -> WalkForwardResult:
    """Roll a train→test window across ``df`` and evaluate each test segment out-of-sample."""
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    step = test_size if step is None else step
    if step <= 0:
        raise ValueError("step must be positive")

    folds: list[dict] = []
    fold_equities: list[pd.Series] = []
    start = 0
    n = len(df)
    while start + train_size + test_size <= n:
        train = df.iloc[start : start + train_size]
        test = df.iloc[start + train_size : start + train_size + test_size].reset_index(drop=True)
        strat = build_strategy(train)
        res = run_backtest(test, strat, costs=costs)
        folds.append(
            {
                "test_start": test["timestamp"].iloc[0],
                "test_end": test["timestamp"].iloc[-1],
                "metrics": res.stats,
                "trades": res.trades,
            }
        )
        fold_equities.append(res.equity_curve)
        start += step

    oos_trades = (
        pd.concat([f["trades"] for f in folds], ignore_index=True)
        if folds
        else pd.DataFrame(columns=["entry_time", "entry_price", "exit_time", "exit_price", "return"])
    )
    oos_equity = _compound_equity(fold_equities)
    oos_metrics = (
        compute_metrics(oos_equity, oos_trades)
        if len(oos_equity)
        else {"trade_count": 0}
    )
    return WalkForwardResult(
        folds=folds,
        oos_trades=oos_trades,
        oos_equity=oos_equity,
        oos_metrics=oos_metrics,
        sample_size_ok=meets_sample_size(oos_trades, min_trades=min_trades),
    )


def _compound_equity(fold_equities: list[pd.Series]) -> pd.Series:
    """Chain per-fold equity curves (each starting at the same base) into one compounded OOS
    curve, so drawdown/return reflect the full out-of-sample experience."""
    if not fold_equities:
        return pd.Series(dtype="float64")
    pieces = []
    running = 1.0
    for eq in fold_equities:
        base = float(eq.iloc[0])
        normalized = (eq / base) * running
        pieces.append(normalized)
        running = float(normalized.iloc[-1])
    return pd.concat(pieces)
