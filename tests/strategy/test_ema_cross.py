"""Tests for the dumb first strategy — EMA crossover (§5).

The strategy proposes intent only (enter_long / exit / hold); it never sizes or executes
(Invariant 3). The critical property is causality: the signal at bar t uses only data up to and
including the closed bar t (no look-ahead, §8.4).
"""
from __future__ import annotations

import pandas as pd

from src.strategy import base
from src.strategy.ema_cross import EmaCross

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _frame(closes):
    rows = []
    for i, c in enumerate(closes):
        rows.append([T0 + i * HOUR, c, c + 0.5, c - 0.5, c, 10.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


# a clear up-move then down-move forces a bullish then bearish crossover
_CLOSES = [100, 100, 100, 100, 100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99, 98]


def test_emits_only_valid_intents():
    sig = EmaCross(ema_fast=3, ema_slow=5).generate_signals(_frame(_CLOSES))
    assert set(sig.unique()).issubset(base.VALID_INTENTS)


def test_first_bar_is_hold():
    sig = EmaCross(ema_fast=3, ema_slow=5).generate_signals(_frame(_CLOSES))
    assert sig.iloc[0] == base.INTENT_HOLD


def test_enter_long_precedes_exit_over_an_up_then_down_move():
    sig = EmaCross(ema_fast=3, ema_slow=5).generate_signals(_frame(_CLOSES))
    enters = [i for i, s in enumerate(sig) if s == base.INTENT_ENTER_LONG]
    exits = [i for i, s in enumerate(sig) if s == base.INTENT_EXIT]
    assert enters and exits
    assert min(enters) < min(exits)


def test_no_lookahead_signal_is_causal():
    # the signal at bar t must be identical whether computed on the full frame or on the
    # prefix ending at t — i.e. it cannot depend on any future bar
    df = _frame(_CLOSES)
    strat = EmaCross(ema_fast=3, ema_slow=5)
    full = strat.generate_signals(df)
    for t in range(len(df)):
        prefix = strat.generate_signals(df.iloc[: t + 1])
        assert prefix.iloc[t] == full.iloc[t]


def test_output_is_intent_only_no_sizing():
    sig = EmaCross(ema_fast=3, ema_slow=5).generate_signals(_frame(_CLOSES))
    # a Series of intent strings aligned to the input — no size/amount/price columns
    assert isinstance(sig, pd.Series)
    assert sig.dtype == object
    assert len(sig) == len(_CLOSES)


def test_declares_target_regime():
    assert EmaCross(ema_fast=3, ema_slow=5).target_regime == "trend"


def test_from_config_dict():
    strat = EmaCross.from_config({"params": {"ema_fast": 12, "ema_slow": 26},
                                  "target_regime": "trend"})
    assert strat.ema_fast == 12 and strat.ema_slow == 26
