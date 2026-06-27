"""Tests for backtest/parity.py — §8.9 dry-run vs backtest SIGNAL parity.

The dry-run and the backtest both run the same deterministic strategy on closed candles, so on
candle timestamps they share the *intent* must be identical (fills differ — §2 — signals do not).
This pins the comparison: log parsing, exact-match accounting, and mismatch reporting.
"""
from __future__ import annotations

import pandas as pd

from backtest import parity
from src.events.log import MARKET_RECEIVED, SIGNAL_GENERATED, Event
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

PAIR = "BTC/USDT"


def _market(ts: str, close=100.0, pair=PAIR) -> Event:
    return Event(MARKET_RECEIVED, "2026-01-01T00:00:00+00:00",
                 {"pair": pair, "close": close, "timestamp": ts})


def _signal(intent: str, pair=PAIR) -> Event:
    return Event(SIGNAL_GENERATED, "2026-01-01T00:00:00+00:00", {"pair": pair, "intent": intent})


# ---- event-log → signal series ---------------------------------------------------------------

def test_dryrun_signals_pairs_market_with_following_signal():
    events = [
        _market("2026-01-01 00:00:00+00:00"), _signal(INTENT_HOLD),
        _market("2026-01-01 01:00:00+00:00"), _signal(INTENT_ENTER_LONG),
        _market("2026-01-01 02:00:00+00:00"), _signal(INTENT_EXIT),
    ]
    sig = parity.dryrun_signals(events, PAIR)
    assert sig[pd.Timestamp("2026-01-01 01:00:00+00:00")] == INTENT_ENTER_LONG
    assert sig[pd.Timestamp("2026-01-01 02:00:00+00:00")] == INTENT_EXIT
    assert len(sig) == 3


def test_dryrun_signals_filters_by_pair():
    events = [
        _market("2026-01-01 00:00:00+00:00", pair="BTC/JPY"), _signal(INTENT_ENTER_LONG, pair="BTC/JPY"),
        _market("2026-01-01 00:00:00+00:00"), _signal(INTENT_HOLD),
    ]
    sig = parity.dryrun_signals(events, PAIR)
    assert list(sig.values()) == [INTENT_HOLD]


# ---- data + strategy → signal series ---------------------------------------------------------

class _ScriptedStrategy:
    """Emits a fixed intent per row, in order."""

    def __init__(self, intents):
        self._intents = intents

    def generate_signals(self, df):
        return pd.Series(self._intents[: len(df)], index=df.index, dtype="object")


def _frame(timestamps):
    return pd.DataFrame({"timestamp": pd.to_datetime(timestamps, utc=True),
                         "close": [100.0] * len(timestamps)})


def test_backtest_signals_indexes_by_candle_timestamp():
    df = _frame(["2026-01-01 00:00:00+00:00", "2026-01-01 01:00:00+00:00"])
    strat = _ScriptedStrategy([INTENT_HOLD, INTENT_ENTER_LONG])
    sig = parity.backtest_signals(df, strat)
    assert sig[pd.Timestamp("2026-01-01 01:00:00+00:00")] == INTENT_ENTER_LONG


# ---- comparison ------------------------------------------------------------------------------

def test_compare_signals_full_parity():
    ts = ["2026-01-01 00:00:00+00:00", "2026-01-01 01:00:00+00:00", "2026-01-01 02:00:00+00:00"]
    intents = [INTENT_HOLD, INTENT_ENTER_LONG, INTENT_EXIT]
    dryrun = {pd.Timestamp(t): i for t, i in zip(ts, intents)}
    backtest = dict(dryrun)
    rep = parity.compare_signals(dryrun, backtest)
    assert rep.overlap == 3
    assert rep.matches == 3
    assert rep.mismatches == []
    assert rep.match_rate == 1.0
    assert rep.is_parity


def test_compare_signals_reports_mismatch():
    t0, t1 = pd.Timestamp("2026-01-01 00:00:00+00:00"), pd.Timestamp("2026-01-01 01:00:00+00:00")
    dryrun = {t0: INTENT_HOLD, t1: INTENT_ENTER_LONG}
    backtest = {t0: INTENT_HOLD, t1: INTENT_EXIT}  # diverges at t1
    rep = parity.compare_signals(dryrun, backtest)
    assert rep.overlap == 2
    assert rep.matches == 1
    assert rep.mismatches == [(t1, INTENT_ENTER_LONG, INTENT_EXIT)]
    assert rep.match_rate == 0.5
    assert not rep.is_parity


def test_compare_signals_counts_non_overlapping_candles():
    t0, t1, t2 = (pd.Timestamp("2026-01-01 00:00:00+00:00"),
                  pd.Timestamp("2026-01-01 01:00:00+00:00"),
                  pd.Timestamp("2026-01-01 02:00:00+00:00"))
    dryrun = {t0: INTENT_HOLD, t1: INTENT_HOLD}      # t1 not in backtest
    backtest = {t0: INTENT_HOLD, t2: INTENT_HOLD}    # t2 not seen by dry-run
    rep = parity.compare_signals(dryrun, backtest)
    assert rep.overlap == 1
    assert rep.dryrun_only == 1
    assert rep.backtest_only == 1
    assert rep.is_parity  # the shared candle agrees


def test_compare_signals_no_overlap_is_not_parity():
    rep = parity.compare_signals({}, {pd.Timestamp("2026-01-01 00:00:00+00:00"): INTENT_HOLD})
    assert rep.overlap == 0
    assert not rep.is_parity  # nothing proven → not parity
