"""backtest/scenario.py — adversarial stress / scenario testing (§15).

A professional desk doesn't just backtest the average case — it stress-tests the tail: flash
crash, overnight gap, volatility spike. These deterministic synthetic scenarios are run through
the SAME risk-sized backtest (with the exchange-side protective stop) so we can *prove* the stop
caps the realized loss near the modeled per-trade risk instead of the full market move.

Pure/deterministic; never trades. Returns a report with the worst realized trade, max drawdown,
and the worst single-candle market move (to contrast "what the market did" vs "what we lost").
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs, RiskSizing, run_backtest

_T0 = pd.Timestamp("2024-01-01T00:00:00Z")
_HOUR = pd.Timedelta(hours=1)
_COLS = ["timestamp", "open", "high", "low", "close", "volume"]


def _frame_from_closes(closes: list[float], *, vol: float = 10.0) -> pd.DataFrame:
    """Build an OHLCV frame from a close path (open = previous close; tight intrabar range)."""
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * 1.001
        lo = min(o, c) * 0.999
        rows.append([_T0 + i * _HOUR, o, hi, lo, c, vol])
        prev = c
    return pd.DataFrame(rows, columns=_COLS)


def flash_crash(*, n: int = 60, crash_at: int = 40, drop: float = 0.30, drift: float = 0.002,
                recover: float = 0.5) -> pd.DataFrame:
    """Slow uptrend, then one candle drops ``drop``, then a partial recovery."""
    closes, p = [], 100.0
    for i in range(n):
        if i == crash_at:
            p *= (1.0 - drop)
        elif i == crash_at + 1:
            p *= (1.0 + drop * recover)
        else:
            p *= (1.0 + drift)
        closes.append(p)
    return _frame_from_closes(closes)


def gap_down(*, n: int = 40, gap_at: int = 25, gap: float = 0.15, drift: float = 0.002) -> pd.DataFrame:
    """Uptrend then an overnight gap down (the gap shows as a low open below the prior close)."""
    closes, p = [], 100.0
    for i in range(n):
        p *= (1.0 - gap) if i == gap_at else (1.0 + drift)
        closes.append(p)
    return _frame_from_closes(closes)


def vol_spike(*, n: int = 80, spike_at: int = 40, mult: float = 8.0) -> pd.DataFrame:
    """Calm, then a burst of large alternating moves (volatility regime change)."""
    closes, p = [], 100.0
    for i in range(n):
        step = 0.003 * (mult if abs(i - spike_at) < 8 else 1.0)
        p *= (1.0 + (step if i % 2 == 0 else -step))
        closes.append(p)
    return _frame_from_closes(closes)


def run_stress(df: pd.DataFrame, strategy, *, costs: Costs | None = None,
               risk: RiskSizing | None = None, initial_equity: float = 10_000.0) -> dict:
    """Run a scenario through the risk-sized backtest and report tail behavior."""
    res = run_backtest(df, strategy, costs=costs or Costs(), initial_equity=initial_equity, risk=risk)
    trades = res.trades
    worst = float(trades["return"].min()) if len(trades) else 0.0
    eq = res.equity_curve
    mdd = float((eq / eq.cummax() - 1.0).min()) if len(eq) else 0.0
    market_drop = float(df["close"].pct_change().min()) if len(df) > 1 else 0.0
    return {
        "trade_count": int(len(trades)),
        "worst_trade_return": worst,
        "max_drawdown": mdd,
        "max_single_candle_drop": market_drop,
        "final_equity": float(eq.iloc[-1]) if len(eq) else initial_equity,
    }
