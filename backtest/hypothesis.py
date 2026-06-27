"""backtest/hypothesis.py — §5/§8 offline validation of a strategy hypothesis (P2).

One candidate hypothesis (a strategy spec a human approved for testing — Inv. 1/8) is run through
the existing walk-forward harness, then judged with the **multiple-testing correction**: the more
hypotheses already tried (from the §15 journal), the higher the Deflated-Sharpe bar it must clear.
The trial — pass or fail — is appended to the journal so it counts against future candidates too.

A candidate is VALIDATED only if all hold:
  - the out-of-sample sample-size gate passes (≥ ~100 trades, §5/§8);
  - the Deflated Sharpe Ratio (PSR vs the expected-max-Sharpe of N trials) ≥ ``dsr_threshold``.
Otherwise it is REJECTED. No historical edge is trusted on isolation alone (that is the trap §5
exists to catch).

Deterministic and offline. The candidate's Sharpe/skew/kurtosis are measured on the OOS **per-trade
returns** (same unit as the sample-size gate); the cross-trial Sharpe dispersion ``sr_std`` is the
std of the journal's recorded trial Sharpes (empirical when ≥2 exist, else a 1/√n_obs fallback).
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from backtest.multiple_testing import deflated_sharpe_ratio
from backtest.runner import Costs
from backtest.walkforward import walk_forward
from src.llm.journal import REJECTED, VALIDATED, HypothesisJournal, HypothesisRecord

BuildStrategy = Callable[[pd.DataFrame], object]


@dataclass(frozen=True)
class _TradeStats:
    sharpe: float
    n_obs: int
    skew: float
    kurtosis: float  # non-excess (normal = 3)


def _per_trade_stats(returns: np.ndarray) -> _TradeStats:
    """Per-trade Sharpe and distribution shape (population moments). Degenerate → zero Sharpe."""
    n = int(returns.size)
    if n < 2:
        return _TradeStats(0.0, n, 0.0, 3.0)
    mean = float(returns.mean())
    std = float(returns.std(ddof=0))
    if std <= 0:
        return _TradeStats(0.0, n, 0.0, 3.0)
    z = (returns - mean) / std
    skew = float((z**3).mean())
    kurt = float((z**4).mean())  # non-excess
    return _TradeStats(mean / std, n, skew, kurt)


def validate_hypothesis(
    *,
    name: str,
    params: dict,
    target_regime: str,
    generated_by: str,
    data: pd.DataFrame,
    build_strategy: BuildStrategy,
    journal: HypothesisJournal,
    now: datetime,
    train_size: int,
    test_size: int,
    step: int | None = None,
    costs: Costs | None = None,
    min_trades: int = 100,
    dsr_threshold: float = 0.95,
) -> HypothesisRecord:
    """Walk-forward + deflated-Sharpe validate one hypothesis, recording the trial to the journal."""
    wf = walk_forward(data, build_strategy, costs=costs, train_size=train_size,
                      test_size=test_size, step=step, min_trades=min_trades)
    returns = wf.oos_trades["return"].to_numpy(dtype="float64") if len(wf.oos_trades) else np.array([])
    stats = _per_trade_stats(returns)

    # N trials = prior tested hypotheses + this one; dispersion from the journal's trial Sharpes.
    prior_sharpes = [float(r.outcome["sharpe"]) for r in journal.all()
                     if r.is_trial() and "sharpe" in r.outcome]
    n_trials = len(prior_sharpes) + 1
    pool = prior_sharpes + [stats.sharpe]
    sr_std = statistics.stdev(pool) if len(pool) >= 2 else 0.0
    if sr_std <= 0:
        sr_std = math.sqrt(1.0 / stats.n_obs) if stats.n_obs >= 2 else 0.0

    if stats.n_obs >= 2 and sr_std > 0:
        dsr = deflated_sharpe_ratio(observed_sharpe=stats.sharpe, n_obs=stats.n_obs,
                                    sr_std=sr_std, n_trials=n_trials,
                                    skew=stats.skew, kurtosis=stats.kurtosis)
    else:
        dsr = 0.0

    if not wf.sample_size_ok:
        passed, reason = False, f"insufficient_sample ({stats.n_obs} < {min_trades} trades)"
    elif dsr < dsr_threshold:
        passed, reason = False, f"deflated_sharpe {dsr:.4f} < {dsr_threshold} (N={n_trials} trials)"
    else:
        passed, reason = True, f"deflated_sharpe {dsr:.4f} >= {dsr_threshold} (N={n_trials} trials)"

    rec = HypothesisRecord(
        name=name, params=dict(params), target_regime=target_regime, generated_by=generated_by,
        proposed_at=now, status=VALIDATED if passed else REJECTED,
        outcome={
            "sharpe": stats.sharpe, "trade_count": stats.n_obs, "skew": stats.skew,
            "kurtosis": stats.kurtosis, "dsr": dsr, "n_trials": n_trials, "sr_std": sr_std,
            "sample_ok": wf.sample_size_ok, "oos_annual_sharpe": wf.oos_metrics.get("sharpe"),
            "reason": reason,
        },
    )
    journal.record(rec)
    return rec
