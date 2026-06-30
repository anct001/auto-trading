"""Tests for efficiency_ratio + regime classification + RegimeFiltered (§5, deterministic)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.features.indicators import efficiency_ratio
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD
from src.strategy.regime_classify import RANGE, TREND, RegimeFiltered, latest_regime

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _frame(closes):
    return pd.DataFrame({"timestamp": [T0 + i * HOUR for i in range(len(closes))],
                         "open": closes, "high": [c * 1.001 for c in closes],
                         "low": [c * 0.999 for c in closes], "close": closes,
                         "volume": [10.0] * len(closes)})


def test_efficiency_ratio_trend_vs_chop():
    trend = efficiency_ratio(list(range(1, 40)), period=20)      # straight line → ER ~ 1
    chop = efficiency_ratio([10, 11] * 20, period=20)            # oscillation → ER ~ 0
    assert trend.iloc[-1] > 0.9 and chop.iloc[-1] < 0.1


@pytest.mark.parametrize("bad", [0, -5])
def test_efficiency_ratio_rejects_bad_period(bad):
    with pytest.raises(ValueError):
        efficiency_ratio([1, 2, 3], period=bad)


def test_classify_trend_and_range():
    assert latest_regime(_frame(list(range(1, 60))), period=20) == TREND
    assert latest_regime(_frame([10, 11] * 30), period=20) == RANGE


def test_regime_filter_suppresses_wrong_regime_entries():
    # a strategy that always wants to enter, targeting 'trend'
    class AlwaysEnter:
        target_regime = "trend"

        def generate_signals(self, df):
            return pd.Series(INTENT_ENTER_LONG, index=df.index, dtype="object")

    chop = _frame([10, 11] * 30)                 # range regime
    out = RegimeFiltered(AlwaysEnter(), period=20).generate_signals(chop)
    assert (out.iloc[25:] == INTENT_HOLD).all()  # entries suppressed in the wrong (range) regime


def test_regime_filter_keeps_exits():
    class AlwaysExit:
        target_regime = "trend"

        def generate_signals(self, df):
            return pd.Series(INTENT_EXIT, index=df.index, dtype="object")

    out = RegimeFiltered(AlwaysExit(), period=20).generate_signals(_frame([10, 11] * 30))
    assert (out == INTENT_EXIT).all()            # exits always allowed (flatten)
