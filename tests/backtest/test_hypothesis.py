"""Tests for backtest/hypothesis.py — offline hypothesis validation with §5 correction (P2).

Pins the decision logic: a real edge with enough OOS trades validates on the first trial; the
sample-size gate and the multiple-testing correction both gate it; and every attempt is recorded
to the journal so the trial count grows (raising the bar for later candidates).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from backtest.hypothesis import validate_hypothesis
from src.llm.journal import REJECTED, VALIDATED, HypothesisJournal, HypothesisRecord
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


class Zigzag:
    """Round-trips every 2 bars: enter on even bars, exit on odd — to generate many trades."""

    def generate_signals(self, df):
        s = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        s.iloc[::2] = INTENT_ENTER_LONG
        s.iloc[1::2] = INTENT_EXIT
        return s


def _uptrend_frame(n):
    # ~0.8% up per bar with a small deterministic wobble → every round trip is net positive
    # (clears costs) but per-trade returns still vary (std > 0, so the Sharpe is finite).
    rows = []
    price = 100.0
    for i in range(n):
        price *= 1.008 * (1.0 + 0.0005 * ((i % 5) - 2))
        rows.append([T0 + i * HOUR, price, price * 1.002, price * 0.998, price, 10.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _validate(journal, data, *, min_trades, dsr_threshold=0.95):
    return validate_hypothesis(
        name="zigzag", params={"period": 2}, target_regime="trend", generated_by="llm:test",
        data=data, build_strategy=lambda _train: Zigzag(), journal=journal, now=NOW,
        train_size=20, test_size=60, step=60, min_trades=min_trades, dsr_threshold=dsr_threshold,
    )


def test_records_trial_and_increments_count(tmp_path):
    j = HypothesisJournal(tmp_path / "j.jsonl")
    rec = _validate(j, _uptrend_frame(400), min_trades=50)
    assert j.count_trials() == 1
    assert "dsr" in rec.outcome and "sharpe" in rec.outcome
    assert rec.outcome["n_trials"] == 1


def test_strong_consistent_edge_validates_on_first_trial(tmp_path):
    j = HypothesisJournal(tmp_path / "j.jsonl")
    rec = _validate(j, _uptrend_frame(400), min_trades=50)
    assert rec.outcome["trade_count"] >= 50
    assert rec.status == VALIDATED
    assert rec.outcome["dsr"] >= 0.95


def test_insufficient_sample_is_rejected(tmp_path):
    j = HypothesisJournal(tmp_path / "j.jsonl")
    rec = _validate(j, _uptrend_frame(120), min_trades=100)  # too few OOS trades
    assert rec.status == REJECTED
    assert "insufficient_sample" in rec.outcome["reason"]


def test_more_prior_trials_lower_the_deflated_sharpe(tmp_path):
    data = _uptrend_frame(400)
    # fresh journal → low N, low dispersion
    fresh = HypothesisJournal(tmp_path / "fresh.jsonl")
    dsr_fresh = _validate(fresh, data, min_trades=50).outcome["dsr"]

    # a journal already holding many dispersed trials → higher bar
    seeded = HypothesisJournal(tmp_path / "seeded.jsonl")
    for i in range(40):
        seeded.record(HypothesisRecord(
            name=f"prior{i}", params={}, target_regime="trend", generated_by="llm:test",
            proposed_at=NOW, status=REJECTED, outcome={"sharpe": 0.05 * (i % 7), "trade_count": 120},
        ))
    dsr_seeded = _validate(seeded, data, min_trades=50).outcome["dsr"]

    assert dsr_seeded < dsr_fresh  # same edge, judged more harshly after many trials (§5)
    assert math.isclose(dsr_fresh, dsr_fresh)  # finite, no NaN


def test_threshold_controls_pass_fail(tmp_path):
    # an unreachable threshold rejects even a real edge (the bar is a knob, §5)
    j = HypothesisJournal(tmp_path / "j.jsonl")
    rec = _validate(j, _uptrend_frame(400), min_trades=50, dsr_threshold=1.0)
    assert rec.status == REJECTED
