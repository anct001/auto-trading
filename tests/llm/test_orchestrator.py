"""Tests for src/llm/orchestrator.py — the slow loop that WRITES sentiment_state.json (§3).

The slow loop is off the trading path: it classifies sentiment per pair and persists the state
file the fast loop later reads. It must be robust — one pair's LLM failure or garbage output must
not poison the others or crash the cycle (the fast loop treats a missing pair as neutral).
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.llm.orchestrator import run_sentiment_cycle
from src.llm.sentiment import PairSentiment
from src.llm.state_io import load_sentiment_state

NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


class FakeClassifier:
    def __init__(self, by_pair):
        self._by_pair = by_pair  # pair -> PairSentiment | Exception

    def classify(self, pair, documents):
        v = self._by_pair[pair]
        if isinstance(v, Exception):
            raise v
        return v


def test_cycle_builds_state_for_each_pair():
    clf = FakeClassifier({
        "BTC/JPY": PairSentiment(-0.3, 0.7, "soft"),
        "ETH/JPY": PairSentiment(0.5, 0.9, "strong"),
    })
    state = run_sentiment_cycle(
        llm=clf, documents_by_pair={"BTC/JPY": ["news"], "ETH/JPY": ["news"]},
        ttl_seconds=600, now=NOW,
    )
    assert state.generated_at == NOW and state.ttl_seconds == 600
    assert set(state.pairs) == {"BTC/JPY", "ETH/JPY"}
    assert state.pairs["BTC/JPY"].sentiment == -0.3


def test_one_pair_failure_does_not_break_the_cycle():
    clf = FakeClassifier({
        "BTC/JPY": PairSentiment(-0.3, 0.7, "ok"),
        "ETH/JPY": RuntimeError("ollama timeout"),
    })
    state = run_sentiment_cycle(
        llm=clf, documents_by_pair={"BTC/JPY": ["n"], "ETH/JPY": ["n"]},
        ttl_seconds=600, now=NOW,
    )
    assert set(state.pairs) == {"BTC/JPY"}  # the failed pair is simply omitted → neutral downstream


def test_out_of_range_readings_are_clamped():
    clf = FakeClassifier({"BTC/JPY": PairSentiment(-5.0, 9.0, "x")})
    state = run_sentiment_cycle(
        llm=clf, documents_by_pair={"BTC/JPY": ["n"]}, ttl_seconds=600, now=NOW,
    )
    r = state.pairs["BTC/JPY"]
    assert r.sentiment == -1.0 and r.confidence == 1.0


def test_cycle_writes_loadable_state_file(tmp_path):
    clf = FakeClassifier({"BTC/JPY": PairSentiment(-0.2, 0.6, "meh")})
    out = tmp_path / "sentiment_state.json"
    run_sentiment_cycle(
        llm=clf, documents_by_pair={"BTC/JPY": ["n"]}, ttl_seconds=300, now=NOW, out_path=out,
    )
    loaded = load_sentiment_state(out)
    assert loaded is not None and loaded.is_fresh(NOW)
    assert loaded.pairs["BTC/JPY"].rationale == "meh"
