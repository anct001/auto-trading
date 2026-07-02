"""Tests for src/strategy/funding_carry.py — spot long-or-flat funding signal (§5)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD, VALID_INTENTS
from src.strategy.funding_carry import FundingCarry


def _df(funding_values):
    n = len(funding_values)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(range(n), unit="h", utc=True),
        "close": [100.0] * n,
        "funding": funding_values,
    })


def test_enter_when_funding_dips_to_or_below_threshold_exit_when_it_rises():
    # threshold 0.0: want_long while funding <= 0
    df = _df([0.01, -0.01, -0.02, 0.03, 0.01])
    sig = FundingCarry(entry_threshold=0.0).generate_signals(df)
    assert sig.iloc[0] == INTENT_HOLD                 # funding>0, stay flat
    assert sig.iloc[1] == INTENT_ENTER_LONG           # crossed to <=0 -> enter
    assert sig.iloc[2] == INTENT_HOLD                 # still want_long -> hold (already in)
    assert sig.iloc[3] == INTENT_EXIT                 # funding back >0 -> exit
    assert sig.iloc[4] == INTENT_HOLD
    assert set(sig).issubset(VALID_INTENTS)


def test_nan_funding_is_treated_as_no_long_signal():
    df = _df([float("nan"), float("nan"), -0.01])
    sig = FundingCarry(entry_threshold=0.0).generate_signals(df)
    assert sig.iloc[0] == INTENT_HOLD and sig.iloc[1] == INTENT_HOLD  # NaN -> fail-flat
    assert sig.iloc[2] == INTENT_ENTER_LONG


def test_missing_funding_column_raises():
    with pytest.raises(KeyError):
        FundingCarry().generate_signals(pd.DataFrame({"close": [1.0, 2.0]}))


def test_signals_are_causal_prefix_matches_full():
    df = _df([0.01, -0.01, -0.02, 0.03, -0.005, -0.001, 0.02, -0.03])
    full = FundingCarry(entry_threshold=0.0).generate_signals(df)
    for k in range(1, len(df) + 1):
        prefix = FundingCarry(entry_threshold=0.0).generate_signals(df.iloc[:k])
        # the intent at each bar must not change when future bars are revealed (no look-ahead)
        assert list(prefix) == list(full.iloc[:k])


def test_smoothing_is_accepted_and_bounded():
    with pytest.raises(ValueError):
        FundingCarry(smooth=0)
    sig = FundingCarry(entry_threshold=0.0, smooth=3).generate_signals(
        _df([0.02, 0.01, -0.01, -0.02, -0.03]))
    assert set(sig).issubset(VALID_INTENTS)


def test_from_config():
    strat = FundingCarry.from_config({"params": {"entry_threshold": -0.005, "smooth": 2},
                                      "target_regime": "any"})
    assert strat.entry_threshold == -0.005 and strat.smooth == 2


def test_flows_through_backtest_with_funding_column():
    # lock the integration: the funding column survives into run_backtest and produces round-trips
    from backtest.runner import run_backtest
    n = 40
    fund = [(-0.01 if (i // 5) % 2 == 0 else 0.01) for i in range(n)]  # alternates long/flat
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(range(n), unit="h", utc=True),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [1.0] * n, "funding": fund,
    })
    res = run_backtest(df, FundingCarry(entry_threshold=0.0))
    assert len(res.trades) > 0  # the funding signal drove entries/exits through the real backtest
