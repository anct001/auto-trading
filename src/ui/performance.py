"""src/ui/performance.py — live performance summary from closed paper trades (§12, read-only).

A small, pure summary of realized results the operator dashboard shows (the kind every comparable
bot dashboard has): trade count, win rate, profit factor, expectancy, total P&L, best/worst. It
operates on the closed round-trip trades the paper runner records (each: ``return`` + ``pnl``);
it never trades and is not a backtest metric (those live in backtest/metrics.py with the full
suite). Realized-only — open positions are marked separately on the equity curve.
"""
from __future__ import annotations


def performance_summary(trades: list[dict]) -> dict:
    """Summarize closed trades. ``profit_factor`` is +inf when there are wins but no losses."""
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0, "total_pnl": 0.0,
                "best": 0.0, "worst": 0.0}
    rets = [float(t.get("return", 0.0)) for t in trades]
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = sum(1 for r in rets if r > 0)
    losses = sum(1 for r in rets if r < 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        # None = undefined/infinite (wins, no losses). NOT float('inf') — that serializes as
        # "Infinity", which browsers' JSON.parse rejects. The UI renders None as "∞".
        profit_factor = None if gross_win > 0 else 0.0
    return {
        "trade_count": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n,
        "profit_factor": profit_factor,
        "expectancy": sum(rets) / n,
        "total_pnl": sum(pnls),
        "best": max(rets),
        "worst": min(rets),
    }
