"""src/risk/limits.py — §4 hard portfolio limits.

MONEY CODE (TDD mandatory). One pure predicate per limit, each returning a ``CheckResult``
(ok + reason). They never mutate state and never place orders — the engine (R6) runs them and
disposes. Conventions:

  - gross ≤ 100% of equity, per-asset ≤ 25%, max 3 concurrent positions.
  - **daily soft (−2%)** blocks *new entries only*; **daily hard (−4%)** and the **drawdown
    kill (−12%)** halt everything — the engine applies the side/halt semantics.
  - **correlation cluster:** positions correlated > 0.7 with the order's pair (and the same pair)
    are summed as ONE cluster, capped at 40% — several alts are really one BTC-beta bet (§4).
  - per-trade *risk* is enforced upstream in sizing (R1); not re-checked here.

Limits are conservative starting guardrails to tune, not profit targets (§4). They live below
the strategy: a strategy never sees them.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.risk.config import RiskConfig
from src.risk.types import Order, PortfolioState


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    reason: str = ""


_OK = CheckResult(True)


def check_gross_exposure(
    order: Order, state: PortfolioState, cfg: RiskConfig, prices: dict[str, float]
) -> CheckResult:
    new_total = state.total_position_value(prices) + order.notional
    cap = cfg.gross_exposure_pct / 100.0 * state.equity
    return _OK if new_total <= cap else CheckResult(False, "gross_exposure")


def check_per_asset(
    order: Order, state: PortfolioState, cfg: RiskConfig, prices: dict[str, float]
) -> CheckResult:
    existing = state.positions.get(order.pair)
    existing_value = existing.value(prices[order.pair]) if existing else 0.0
    cap = cfg.per_asset_cap_pct / 100.0 * state.equity
    return _OK if existing_value + order.notional <= cap else CheckResult(False, "per_asset_cap")


def check_concurrency(order: Order, state: PortfolioState, cfg: RiskConfig) -> CheckResult:
    if order.pair in state.positions:
        return _OK  # adding to an existing position takes no new slot
    if len(state.positions) + 1 <= cfg.max_concurrent_positions:
        return _OK
    return CheckResult(False, "max_concurrent_positions")


def check_daily_soft(state: PortfolioState, cfg: RiskConfig) -> CheckResult:
    """Soft daily loss → stop *new entries* (ok=False means: no new entries)."""
    if state.day_return() <= cfg.daily_soft_pct / 100.0:
        return CheckResult(False, "daily_soft_limit")
    return _OK


def check_daily_hard(state: PortfolioState, cfg: RiskConfig) -> CheckResult:
    """Hard daily loss → flatten all / halt."""
    if state.day_return() <= cfg.daily_hard_pct / 100.0:
        return CheckResult(False, "daily_hard_limit")
    return _OK


def check_drawdown(state: PortfolioState, cfg: RiskConfig) -> CheckResult:
    """Peak-to-trough drawdown kill-switch (non-overridable at runtime — §4)."""
    if state.drawdown() <= cfg.max_drawdown_killswitch_pct / 100.0:
        return CheckResult(False, "drawdown_killswitch")
    return _OK


def _corr(correlations: dict[tuple[str, str], float], a: str, b: str) -> float | None:
    """Pairwise correlation, or None if unknown. Same pair is perfectly correlated."""
    if a == b:
        return 1.0
    if (a, b) in correlations:
        return correlations[(a, b)]
    return correlations.get((b, a))  # None if absent


def check_correlation_cluster(
    order: Order,
    state: PortfolioState,
    cfg: RiskConfig,
    prices: dict[str, float],
    correlations: dict[tuple[str, str], float],
) -> CheckResult:
    """Sum the order with every position correlated > threshold (and the same pair) as one
    cluster; cap the cluster's exposure at cluster_exposure_cap_pct of equity.

    **Fail-closed:** a position whose correlation with the order's pair is *unknown* is assumed
    correlated and included in the cluster — missing data is the dangerous case (several alts, no
    correlation feed, all silently passing), so we treat it conservatively (§4)."""
    cluster_value = order.notional
    for pair, pos in state.positions.items():
        corr = _corr(correlations, order.pair, pair)
        if corr is None or corr > cfg.cluster_corr_threshold:
            cluster_value += pos.value(prices[pair])
    cap = cfg.cluster_exposure_cap_pct / 100.0 * state.equity
    return _OK if cluster_value <= cap else CheckResult(False, "correlation_cluster_cap")


def check_portfolio_beta(
    order: Order,
    state: PortfolioState,
    prices: dict[str, float],
    betas: dict[str, float],
    max_beta: float,
) -> CheckResult:
    """Cap beta-weighted exposure / equity at ``max_beta``.

    The beta cap value is not yet a `config/risk` default (§4 names the cap but gives no number);
    it is passed explicitly until an ORIENT decision sets one — we do not invent a default.
    """
    exposure = order.notional * betas.get(order.pair, 1.0)
    for pair, pos in state.positions.items():
        exposure += pos.value(prices[pair]) * betas.get(pair, 1.0)
    return _OK if exposure / state.equity <= max_beta else CheckResult(False, "portfolio_beta_cap")
