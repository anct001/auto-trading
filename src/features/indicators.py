"""src/features/indicators.py — §15 single feature path.

The ONE shared place indicators are computed, used by BOTH the live fast loop and the backtest,
so there is no train-serve skew. Nothing else in the system should compute an indicator
independently. The bot reads these raw numeric values; charts are operator-facing only (§12).

EMA is implemented first (the dumb first strategy, §5, needs it). RSI / MACD / ATR / Bollinger
follow here as the strategy/risk layers need them — added to this module, never duplicated.

All functions are causal: a value at bar t depends only on bars ≤ t (no look-ahead, §8.4).
"""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def ema(values: Iterable[float], period: int) -> pd.Series:
    """Exponential moving average (recursive form, alpha = 2/(period+1)).

    Equivalent to pandas ``ewm(span=period, adjust=False)``: y0 = x0, and
    yt = (1-alpha)*y(t-1) + alpha*xt. Seeded from the first value, so it is defined from bar 0
    and strictly causal. Raises ValueError on a non-positive period.
    """
    if period <= 0:
        raise ValueError(f"EMA period must be positive, got {period}")
    s = pd.Series(list(values), dtype="float64")
    return s.ewm(span=period, adjust=False).mean()


def with_emas(
    df: pd.DataFrame, periods: Iterable[int], *, source: str = "close"
) -> pd.DataFrame:
    """Return a copy of ``df`` with an ``ema_{p}`` column for each period in ``periods``.

    Computed off ``source`` (default close) via the single ``ema`` path above. Row count and
    order are preserved, so the result aligns 1:1 with the input candles.
    """
    out = df.copy()
    for period in periods:
        out[f"ema_{period}"] = ema(out[source], period).to_numpy()
    return out
