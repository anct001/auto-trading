"""src/strategy/rsi_reversion.py — §5 RSI mean-reversion (deliberately simple).

Long-or-flat: enter long when RSI is oversold (< ``low``), exit when overbought (> ``high``).
Intent-only — it never sizes or executes (the risk engine disposes, Inv 3). Declares the
``range`` regime (mean-reversion works in ranging markets, §5). Uses the single RSI feature path.
Like every strategy here it's a hypothesis to be validated forward (§6) / deflated (§5), not an
edge until proven.
"""
from __future__ import annotations

import pandas as pd

from src.features.indicators import rsi
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD, Strategy


class RsiReversion(Strategy):
    target_regime = "range"

    def __init__(self, period: int = 14, low: float = 30.0, high: float = 70.0):
        if not (0 < low < high < 100):
            raise ValueError("require 0 < low < high < 100")
        self.period = period
        self.low = low
        self.high = high

    @classmethod
    def from_config(cls, cfg: dict) -> "RsiReversion":
        p = cfg.get("params", {})
        return cls(period=int(p.get("period", 14)), low=float(p.get("low", 30.0)),
                   high=float(p.get("high", 70.0)))

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        r = rsi(df["close"], self.period)
        signals = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        signals[r < self.low] = INTENT_ENTER_LONG     # oversold → enter (dedup'd if already long)
        signals[r > self.high] = INTENT_EXIT          # overbought → exit
        return signals
