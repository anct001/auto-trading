"""src/strategy/ema_cross.py — §5 the deliberately dumb first strategy (EMA crossover).

Two params (fast/slow EMA). Its only job is to exercise the pipeline end-to-end. If it ever
looks profitable, suspect the harness before believing in edge (§5).

Signal rule (long-or-flat, spot-only — §0):
  - bullish cross (fast crosses above slow) → enter_long
  - bearish cross (fast crosses below slow) → exit
  - otherwise                               → hold

EMAs come from the single feature path (features.indicators.ema). The crossover compares the
current bar to the previous bar, both derived from closed candles, so it is causal (§8.4).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from src.features.indicators import ema
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD, Strategy


class EmaCross(Strategy):
    target_regime = "trend"

    def __init__(self, ema_fast: int, ema_slow: int):
        if ema_fast <= 0 or ema_slow <= 0:
            raise ValueError("EMA periods must be positive")
        if ema_fast >= ema_slow:
            raise ValueError(f"ema_fast ({ema_fast}) must be < ema_slow ({ema_slow})")
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "EmaCross":
        """Build from a (hash-locked, §15) strategy config dict, e.g. config/strategy/ema_cross.json."""
        params = config["params"]
        strat = cls(ema_fast=int(params["ema_fast"]), ema_slow=int(params["ema_slow"]))
        if "target_regime" in config:
            strat.target_regime = config["target_regime"]
        return strat

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        fast = ema(close, self.ema_fast)
        slow = ema(close, self.ema_slow)
        prev_fast, prev_slow = fast.shift(1), slow.shift(1)

        cross_up = (prev_fast <= prev_slow) & (fast > slow)
        cross_down = (prev_fast >= prev_slow) & (fast < slow)

        signals = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        signals[cross_up] = INTENT_ENTER_LONG
        signals[cross_down] = INTENT_EXIT
        # first bar has no previous → NaN comparisons are False → stays hold (correct)
        return signals
