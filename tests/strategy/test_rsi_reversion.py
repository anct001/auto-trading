"""Tests for RsiReversion + DonchianBreakout — signal correctness (intent-only, causal)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, VALID_INTENTS
from src.strategy.donchian_breakout import DonchianBreakout
from src.strategy.rsi_reversion import RsiReversion

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _frame(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.001 for c in closes]
    lows = lows or [c * 0.999 for c in closes]
    return pd.DataFrame({"timestamp": [T0 + i * HOUR for i in range(n)],
                         "open": closes, "high": highs, "low": lows,
                         "close": closes, "volume": [10.0] * n})


def test_rsi_reversion_enters_oversold_exits_overbought():
    down = list(range(60, 40, -1))   # falling → oversold
    up = list(range(40, 75))         # rising → overbought
    sig = RsiReversion(period=14, low=30, high=70).generate_signals(_frame(down + up))
    assert set(sig.unique()) <= VALID_INTENTS
    assert INTENT_ENTER_LONG in sig.values   # oversold during the decline
    assert INTENT_EXIT in sig.values         # overbought during the rally


def test_rsi_reversion_validates_params():
    with pytest.raises(ValueError):
        RsiReversion(low=70, high=30)


def test_donchian_breakout_enters_on_new_high():
    closes = [10] * 25 + [20]        # flat then a breakout candle
    highs = [10.1] * 25 + [20]
    sig = DonchianBreakout(period=20).generate_signals(_frame(closes, highs=highs))
    assert sig.iloc[-1] == INTENT_ENTER_LONG


def test_donchian_breakout_exits_on_new_low():
    closes = [10] * 25 + [5]
    lows = [9.9] * 25 + [5]
    sig = DonchianBreakout(period=20).generate_signals(_frame(closes, lows=lows))
    assert sig.iloc[-1] == INTENT_EXIT


def test_donchian_no_lookahead_first_window_is_hold():
    sig = DonchianBreakout(period=20).generate_signals(_frame(list(range(1, 15))))
    assert (sig.iloc[:14] == "hold").all()   # not enough prior window → no signal
