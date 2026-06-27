"""src/ui/screener.py — operator research screener (§12), read-only.

Filter the universe by indicator conditions to surface research candidates — a one-glance "what's
worth a closer look." It **never trades**: a surfaced pair is only a candidate; it still has to
pass backtest + walk-forward (§5/§8) before it can join the traded set. This is operator-facing
research, not a signal path — exactly the §12 separation of *viewing* from *trading*.

The screener is generic and deterministic: `screen` keeps the pairs whose readouts satisfy ALL
conditions. Missing or NaN readouts never match (conservative — don't surface what we can't
evaluate). `readouts_from_ohlcv` derives a small standard readout set from the project's existing
single-feature-path indicators (EMA, ATR), so a caller can build the universe from OHLCV.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.features.indicators import atr as atr_fn
from src.features.indicators import ema

_NUMERIC_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class Condition:
    field: str
    op: str             # one of _NUMERIC_OPS, or "is_true" / "is_false"
    value: Any = None   # the threshold for numeric ops; ignored for is_true/is_false

    def holds(self, readouts: dict) -> bool:
        if self.field not in readouts:
            return False
        v = readouts[self.field]
        if self.op == "is_true":
            return v is True
        if self.op == "is_false":
            return v is False
        if isinstance(v, bool) or v is None:
            return False
        if isinstance(v, float) and math.isnan(v):
            return False
        fn = _NUMERIC_OPS.get(self.op)
        if fn is None:
            raise ValueError(f"unknown screener op: {self.op!r}")
        return bool(fn(v, self.value))


def screen(universe: dict[str, dict], conditions: list[Condition]) -> list[str]:
    """Return the sorted pairs whose readouts satisfy EVERY condition (read-only; never trades)."""
    return sorted(
        pair for pair, readouts in universe.items()
        if all(c.holds(readouts) for c in conditions)
    )


def readouts_from_ohlcv(
    df: pd.DataFrame, *, ema_fast: int = 12, ema_slow: int = 26,
    atr_period: int = 14, lookback: int = 24,
) -> dict:
    """Compute a standard readout set for one pair from OHLCV (latest bar). NaN where insufficient."""
    close = df["close"]
    last = float(close.iloc[-1]) if len(close) else float("nan")
    fast = float(ema(close, ema_fast).iloc[-1]) if len(close) else float("nan")
    slow = float(ema(close, ema_slow).iloc[-1]) if len(close) else float("nan")
    atr_val = float(atr_fn(df, atr_period).iloc[-1]) if len(df) > atr_period else float("nan")
    if len(close) > lookback:
        prev = float(close.iloc[-1 - lookback])
        ret_pct = (last / prev - 1.0) * 100.0 if prev else float("nan")
    else:
        ret_pct = float("nan")
    return {
        "close": last,
        "ema_fast": fast,
        "ema_slow": slow,
        "ema_fast_above_slow": bool(fast > slow) if not (math.isnan(fast) or math.isnan(slow)) else False,
        "atr": atr_val,
        "atr_pct": (atr_val / last * 100.0) if (last and not math.isnan(atr_val)) else float("nan"),
        "return_lookback_pct": ret_pct,
    }
