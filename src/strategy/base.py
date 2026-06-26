"""src/strategy/base.py — §5 strategy interface: emits intent, NEVER sizes or executes.

A strategy proposes an **intent** per bar (enter_long / exit / hold) and declares its target
regime. It never computes a position size, never places an order, and never sees the risk
limits — the risk engine disposes (Invariant 3). This separation is what keeps "the strategy
proposes, the risk engine disposes" true in code, not just in prose.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

# The only intents a strategy may emit. Deliberately tiny: no size, no price, no side beyond
# long-or-flat (spot-only / long-or-cash before P5 — §0).
INTENT_ENTER_LONG = "enter_long"
INTENT_EXIT = "exit"
INTENT_HOLD = "hold"
VALID_INTENTS = frozenset({INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD})


class Strategy(ABC):
    """Base class for deterministic strategies.

    Subclasses set ``target_regime`` (so ``regime_state.json`` can deterministically disable them
    outside it — §5) and implement ``generate_signals`` to return one intent per input bar.
    """

    target_regime: str = "any"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Return a Series of intents (from VALID_INTENTS), aligned 1:1 with ``df`` rows.

        Must be causal: the intent at bar t may use only data ≤ t (no look-ahead, §8.4).
        """
        raise NotImplementedError
