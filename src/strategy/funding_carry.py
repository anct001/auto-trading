"""src/strategy/funding_carry.py — §5 funding-rate carry (spot long-or-flat, intent-only).

Thesis (a *different information source* than TA): perpetual funding reflects positioning. When
funding is low/negative, shorts are paying longs — a crowded-bearish / potential-squeeze setup, so
lean **long spot**; otherwise stay flat. This is contrarian to crowd positioning, not a price
pattern. It never trades the perp and never shorts (spot-only, §0) — funding is only the signal.

Signal rule (causal §8.4 — uses funding known at or before bar t):
  - want_long := smoothed_funding(t) ≤ entry_threshold
  - enter_long on the flat→want_long transition, exit on want_long→flat, else hold.

Two params (entry_threshold, smooth) keep the §5 multiple-testing budget small. Requires a
``funding`` column (attach it with data.funding.align_funding); a missing/NaN funding value is
treated as 'no long signal' (fail-flat), never a guess.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from src.features.indicators import ema
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD, Strategy


class FundingCarry(Strategy):
    target_regime = "any"

    def __init__(self, entry_threshold: float = 0.0, smooth: int = 1):
        if smooth <= 0:
            raise ValueError("smooth must be a positive integer")
        self.entry_threshold = float(entry_threshold)
        self.smooth = int(smooth)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "FundingCarry":
        params = config["params"]
        strat = cls(entry_threshold=float(params.get("entry_threshold", 0.0)),
                    smooth=int(params.get("smooth", 1)))
        if "target_regime" in config:
            strat.target_regime = config["target_regime"]
        return strat

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        if "funding" not in df.columns:
            raise KeyError("FundingCarry requires a 'funding' column (use data.funding.align_funding)")
        funding = df["funding"].astype("float64")
        smoothed = ema(funding, self.smooth) if self.smooth > 1 else funding

        # want_long where funding is at/below the threshold; NaN -> False (fail-flat, no guess)
        want_long = (smoothed <= self.entry_threshold).fillna(False)
        prev = want_long.shift(1, fill_value=False)

        signals = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        signals[want_long & ~prev] = INTENT_ENTER_LONG
        signals[~want_long & prev] = INTENT_EXIT
        return signals
