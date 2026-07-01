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

from src.ui.performance import performance_summary


@dataclass(frozen=True)
class ReplayResult:
    pair: str
    start_equity: float
    final_equity: float
    total_return: float           # final/start - 1
    max_drawdown: float           # peak-to-trough on the equity curve (<= 0)
    sharpe: float                 # per-tick mean/std of equity returns (comparable across pairs)
    performance: dict             # ui.performance.performance_summary(trades)
    trades: list = field(default_factory=list)          # closed round-trips (newest first)
    equity_curve: list = field(default_factory=list)    # [{t, equity}] for the mini chart


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


def summarize_result(pair: str, *, trades: list[dict], equity_history: list[dict],
                     start_equity: float) -> ReplayResult:
    """Build a ReplayResult from one pair's replay output (trades + marked equity curve)."""
    equity = _equity_series(equity_history)
    final = equity[-1] if equity else float(start_equity)
    total_return = (final / start_equity - 1.0) if start_equity else 0.0
    return ReplayResult(
        pair=pair,
        start_equity=float(start_equity),
        final_equity=float(final),
        total_return=total_return,
        max_drawdown=_max_drawdown(equity),
        sharpe=_per_tick_sharpe(equity),
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
            "sharpe": r.sharpe,
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
