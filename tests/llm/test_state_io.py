"""Tests for src/llm/state_io.py — read/write sentiment_state.json (§3 output contract).

The fast loop reads this file every tick; it must NEVER crash the loop. Any problem (absent,
malformed, wrong schema) resolves to None → the haircut treats it as neutral (Inv. 2).
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.llm.sentiment import PairSentiment, SentimentState
from src.llm.state_io import load_sentiment_state, write_sentiment_state

NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_write_then_load_roundtrips(tmp_path):
    state = SentimentState(
        schema_version=1, generated_at=NOW, ttl_seconds=600,
        pairs={"BTC/JPY": PairSentiment(-0.4, 0.8, "weak on-chain flows")},
    )
    path = tmp_path / "sentiment_state.json"
    write_sentiment_state(path, state)
    loaded = load_sentiment_state(path)
    assert loaded is not None
    assert loaded.schema_version == 1
    assert loaded.ttl_seconds == 600
    assert loaded.generated_at == NOW
    r = loaded.pairs["BTC/JPY"]
    assert (r.sentiment, r.confidence, r.rationale) == (-0.4, 0.8, "weak on-chain flows")


def test_absent_file_is_none(tmp_path):
    assert load_sentiment_state(tmp_path / "nope.json") is None


def test_malformed_json_is_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_sentiment_state(p) is None


def test_missing_required_fields_is_none(tmp_path):
    p = tmp_path / "partial.json"
    p.write_text('{"schema_version": 1}', encoding="utf-8")  # no generated_at / ttl / pairs
    assert load_sentiment_state(p) is None


def test_loaded_state_freshness_is_usable(tmp_path):
    state = SentimentState(schema_version=1, generated_at=NOW, ttl_seconds=300, pairs={})
    p = tmp_path / "s.json"
    write_sentiment_state(p, state)
    loaded = load_sentiment_state(p)
    assert loaded.is_fresh(NOW) is True
