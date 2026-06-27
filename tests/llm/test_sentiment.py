"""Tests for src/llm/sentiment.py — the bounded position-size haircut (§3, P1).

The haircut is money-adjacent: it scales an entry the strategy already decided. Its hard contract
(it may only SHRINK, never grow/trigger/flip/veto a trade; FLOOR=1.0 is a no-op) is pinned here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.llm.sentiment import PairSentiment, SentimentState, size_haircut

PAIR = "BTC/JPY"
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def _state(sentiment, confidence, *, generated_at=NOW, ttl=600) -> SentimentState:
    return SentimentState(
        schema_version=1, generated_at=generated_at, ttl_seconds=ttl,
        pairs={PAIR: PairSentiment(sentiment=sentiment, confidence=confidence, rationale="x")},
    )


# ---- neutral / fail-to-neutral cases ---------------------------------------------------------

def test_absent_state_is_neutral():
    assert size_haircut(None, PAIR, now=NOW, floor=0.5) == 1.0


def test_missing_pair_is_neutral():
    st = _state(-1.0, 1.0)
    assert size_haircut(st, "ETH/JPY", now=NOW, floor=0.5) == 1.0


def test_stale_state_is_neutral():
    st = _state(-1.0, 1.0, generated_at=NOW - timedelta(seconds=601), ttl=600)
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 1.0


def test_fresh_at_exactly_ttl_is_still_used():
    st = _state(-1.0, 1.0, generated_at=NOW - timedelta(seconds=600), ttl=600)
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 0.5


# ---- the FLOOR=1.0 default is a strict no-op (logged, not acting) -----------------------------

@pytest.mark.parametrize("sentiment", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_floor_1_is_always_neutral(sentiment):
    st = _state(sentiment, 1.0)
    assert size_haircut(st, PAIR, now=NOW, floor=1.0) == 1.0


# ---- only bearish sentiment shrinks; never grows ---------------------------------------------

@pytest.mark.parametrize("sentiment", [0.0, 0.3, 1.0])
def test_non_negative_sentiment_never_increases_size(sentiment):
    st = _state(sentiment, 1.0)
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 1.0


def test_full_bearish_high_confidence_hits_floor():
    st = _state(-1.0, 1.0)
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 0.5


def test_partial_bearish_scales_between_floor_and_one():
    st = _state(-0.5, 1.0)  # severity 0.5 → 1 - 0.5*(1-0.5) = 0.75
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 0.75


def test_confidence_scales_the_haircut():
    st = _state(-1.0, 0.5)  # severity 0.5 → 0.75
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 0.75


# ---- invariants: result is always a shrink that can never veto -------------------------------

@pytest.mark.parametrize("sentiment,confidence", [(-1.0, 1.0), (-0.3, 0.9), (-1.0, 0.1), (0.2, 1.0)])
def test_result_always_in_floor_one_band(sentiment, confidence):
    st = _state(sentiment, confidence)
    m = size_haircut(st, PAIR, now=NOW, floor=0.5)
    assert 0.5 <= m <= 1.0


def test_out_of_range_inputs_are_clamped_not_crashed():
    st = _state(-5.0, 9.0)  # absurd values → clamped, still hits floor, no crash
    assert size_haircut(st, PAIR, now=NOW, floor=0.5) == 0.5


def test_invalid_floor_rejected():
    st = _state(-1.0, 1.0)
    for bad in (0.4, 0.0, 1.1, -1.0):
        with pytest.raises(ValueError):
            size_haircut(st, PAIR, now=NOW, floor=bad)
