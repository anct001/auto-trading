"""src/strategy/regime_classify.py — deterministic regime classification (§5).

Labels each bar "trend" or "range" from the Kaufman Efficiency Ratio — pure code, *informed by,
not decided by, the LLM* (§5). Feeds two consumers: it can write `regime_state.json` (the regime
gate, strategy/regime.py) for the live loop, and `RegimeFiltered` uses it to suppress a strategy's
entries outside its target regime (sit out the bad regime; exits always allowed). Causal/§8.4.
"""
from __future__ import annotations

import pandas as pd

from src.features.indicators import efficiency_ratio
from src.strategy.base import INTENT_ENTER_LONG, Strategy

TREND, RANGE = "trend", "range"


def classify_regime(df: pd.DataFrame, *, period: int = 20, threshold: float = 0.30) -> pd.Series:
    """Per-bar regime: ER ≥ threshold → 'trend', else 'range' (NaN warmup → 'range')."""
    er = efficiency_ratio(df["close"], period)
    return pd.Series([TREND if (v == v and v >= threshold) else RANGE for v in er], index=df.index)


def latest_regime(df: pd.DataFrame, *, period: int = 20, threshold: float = 0.30) -> str:
    """The current regime label (for writing regime_state.json)."""
    if len(df) == 0:
        return RANGE
    return classify_regime(df, period=period, threshold=threshold).iloc[-1]


class RegimeFiltered(Strategy):
    """Wrap a strategy: keep its exits, but suppress entries when the live regime ≠ its target.

    Tests the research question "does sitting a strategy out of its wrong regime improve it?"
    A strategy whose ``target_regime`` is 'any' is unaffected.
    """

    def __init__(self, base: Strategy, *, period: int = 20, threshold: float = 0.30):
        self.base = base
        self.period = period
        self.threshold = threshold
        self.target_regime = getattr(base, "target_regime", "any")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        sig = self.base.generate_signals(df).copy()
        if self.target_regime in (TREND, RANGE):
            regime = classify_regime(df, period=self.period, threshold=self.threshold)
            wrong = (regime != self.target_regime) & (sig == INTENT_ENTER_LONG)
            sig[wrong] = "hold"   # suppress entries in the wrong regime; exits pass through
        return sig
