"""src/strategy/donchian_breakout.py — §5 Donchian channel breakout (trend-following).

Long-or-flat: enter long when close breaks above the highest high of the prior ``period`` bars;
exit when it breaks below the lowest low of the prior ``period``. The prior-window extremes are
``shift(1)``-ed so the signal at bar t never peeks at bar t's own range (no look-ahead, §8.4).
Intent-only (Inv 3); declares the ``trend`` regime (§5). A hypothesis to validate, not an edge.
"""
from __future__ import annotations

import pandas as pd

from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD, Strategy


class DonchianBreakout(Strategy):
    target_regime = "trend"

    def __init__(self, period: int = 20):
        if period <= 1:
            raise ValueError("period must be > 1")
        self.period = period

    @classmethod
    def from_config(cls, cfg: dict) -> "DonchianBreakout":
        return cls(period=int(cfg.get("params", {}).get("period", 20)))

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        upper = df["high"].rolling(self.period).max().shift(1)   # prior-window high (causal)
        lower = df["low"].rolling(self.period).min().shift(1)    # prior-window low
        signals = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        signals[df["close"] > upper] = INTENT_ENTER_LONG
        signals[df["close"] < lower] = INTENT_EXIT
        return signals
