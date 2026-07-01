"""src/ui/replay_dashboard.py — multi-pair dry-run/replay comparison model (§12, read-only).

Turns the per-pair output of a replay (closed round-trip trades + the marked equity curve) into a
compact, JSON-safe comparison the operator dashboard can render as a table, and the read-only chat
assistant can analyse across coins. Pure: it computes and serializes, it never trades.

Metrics are deliberately compact and self-contained (no pandas): total return + max drawdown +
a per-tick Sharpe from the equity curve, plus the realized-trade stats from
``ui.performance.performance_summary`` (win rate / profit factor / expectancy / P&L). For a
statistically rigorous read use the backtest + deflated-Sharpe tooling — replay P&L is optimistic
(§2) and, on the dumb strategies, has no validated edge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from backtest.risk_analytics import bootstrap_max_drawdown, conditional_var, value_at_risk
from src.ui.performance import performance_summary

_MC_PATHS = 500  # bootstrap paths for the Monte-Carlo drawdown distribution (seeded → reproducible)


@dataclass(frozen=True)
class ReplayResult:
    pair: str
    start_equity: float
    final_equity: float
    total_return: float           # final/start - 1
    max_drawdown: float           # peak-to-trough on the equity curve (<= 0)
    sharpe: float                 # per-tick mean/std of equity returns (comparable across pairs)
    sortino: float                # per-tick mean / downside deviation (target 0)
    calmar: float                 # total_return / |max_drawdown| (period Calmar; 0 if no DD)
    var95: float                  # 95% historical VaR of per-tick returns (<= 0 = tail loss)
    cvar95: float                 # 95% CVaR / expected shortfall (mean of the tail; <= VaR)
    mc_dd_p95: float              # Monte-Carlo bootstrapped max-drawdown, 95%-worst case (<= 0)
    mc_dd_p99: float              # Monte-Carlo bootstrapped max-drawdown, 99%-worst case (<= 0)
    avg_exposure: float           # mean gross-position-value / equity over the run
    time_in_market: float         # fraction of ticks holding a position
    performance: dict             # ui.performance.performance_summary(trades)
    trades: list = field(default_factory=list)          # closed round-trips (newest first)
    equity_curve: list = field(default_factory=list)    # [{t, equity, exposure}] for the mini chart


def _equity_series(equity_history: list[dict]) -> list[float]:
    return [float(e.get("equity", 0.0)) for e in (equity_history or [])]


def _max_drawdown(equity: list[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def _per_tick_sharpe(equity: list[float]) -> float:
    if len(equity) < 3:
        return 0.0
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]
    n = len(rets)
    if n < 2:
        return 0.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = var ** 0.5
    return (mean / sd) if sd > 0 else 0.0


def _tick_returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1.0
            for i in range(1, len(equity)) if equity[i - 1] > 0]


def _per_tick_sortino(rets: list[float]) -> float:
    """Mean return / downside deviation (target 0). 0.0 if too few returns or no downside."""
    n = len(rets)
    if n < 2:
        return 0.0
    mean = sum(rets) / n
    downside = sum(r * r for r in rets if r < 0) / n
    dd = downside ** 0.5
    return (mean / dd) if dd > 0 else 0.0


def summarize_result(pair: str, *, trades: list[dict], equity_history: list[dict],
                     start_equity: float) -> ReplayResult:
    """Build a ReplayResult from one pair's replay output (trades + marked equity curve)."""
    equity = _equity_series(equity_history)
    final = equity[-1] if equity else float(start_equity)
    total_return = (final / start_equity - 1.0) if start_equity else 0.0
    max_dd = _max_drawdown(equity)
    rets = _tick_returns(equity)
    mc = bootstrap_max_drawdown(rets, n=_MC_PATHS, seed=0) if len(rets) >= 2 else {"p95": 0.0, "p99": 0.0}
    exposures = [float(e.get("exposure", 0.0)) for e in (equity_history or [])]
    return ReplayResult(
        pair=pair,
        start_equity=float(start_equity),
        final_equity=float(final),
        total_return=total_return,
        max_drawdown=max_dd,
        sharpe=_per_tick_sharpe(equity),
        sortino=_per_tick_sortino(rets),
        calmar=(total_return / abs(max_dd)) if max_dd < 0 else 0.0,
        var95=value_at_risk(rets, level=0.95) if rets else 0.0,
        cvar95=conditional_var(rets, level=0.95) if rets else 0.0,
        mc_dd_p95=mc["p95"],
        mc_dd_p99=mc["p99"],
        avg_exposure=(sum(exposures) / len(exposures)) if exposures else 0.0,
        time_in_market=(sum(1 for x in exposures if x > 1e-9) / len(exposures)) if exposures else 0.0,
        performance=performance_summary(trades),
        trades=list(trades),
        equity_curve=list(equity_history or []),
    )


def build_replay_comparison(results: dict[str, ReplayResult]) -> dict:
    """A JSON-safe comparison across pairs: a sortable metric table + best/worst + per-pair detail.

    Rows are sorted by total return (desc). ``profit_factor`` stays None (=∞, JSON-safe) when a
    pair had wins and no losses — the UI renders None as ∞."""
    rows = []
    for r in results.values():
        perf = r.performance
        rows.append({
            "pair": r.pair,
            "trades": perf.get("trade_count", 0),
            "win_rate": perf.get("win_rate", 0.0),
            "profit_factor": perf.get("profit_factor", 0.0),
            "total_return": r.total_return,
            "max_drawdown": r.max_drawdown,
            "calmar": r.calmar,
            "sharpe": r.sharpe,
            "sortino": r.sortino,
            "var95": r.var95,
            "cvar95": r.cvar95,
            "mc_dd_p95": r.mc_dd_p95,
            "mc_dd_p99": r.mc_dd_p99,
            "avg_exposure": r.avg_exposure,
            "time_in_market": r.time_in_market,
            "final_equity": r.final_equity,
            "total_pnl": perf.get("total_pnl", 0.0),
        })
    rows.sort(key=lambda x: x["total_return"], reverse=True)
    start_equity = next(iter(results.values())).start_equity if results else 0.0
    return {
        "pairs": [r["pair"] for r in rows],
        "table": rows,
        "best_pair": rows[0]["pair"] if rows else None,
        "worst_pair": rows[-1]["pair"] if rows else None,
        "start_equity": start_equity,
        "detail": {p: asdict(r) for p, r in results.items()},
    }
