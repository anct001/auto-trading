"""Tests for src/llm/ollama_client.py — the Ollama sentiment backend (§3).

No network: the HTTP transport is injected. We pin the prompt construction (pair + documents
present, JSON-only instruction) and the response parsing (valid JSON → clamped PairSentiment;
garbage → ValueError so the orchestrator omits that pair → neutral downstream).
"""
from __future__ import annotations

import json

import pytest

from src.llm.ollama_client import (
    OllamaSentimentClassifier,
    build_sentiment_prompt,
    parse_sentiment_response,
)


def test_prompt_includes_pair_and_documents():
    prompt = build_sentiment_prompt("BTC/JPY", ["ETF inflows surge", "miner selling"])
    assert "BTC/JPY" in prompt
    assert "ETF inflows surge" in prompt and "miner selling" in prompt
    assert "JSON" in prompt or "json" in prompt  # instructs structured output


def test_parse_valid_json():
    r = parse_sentiment_response('{"sentiment": -0.4, "confidence": 0.8, "rationale": "weak"}')
    assert (r.sentiment, r.confidence, r.rationale) == (-0.4, 0.8, "weak")


def test_parse_clamps_out_of_range():
    r = parse_sentiment_response('{"sentiment": -3, "confidence": 5, "rationale": "x"}')
    assert r.sentiment == -1.0 and r.confidence == 1.0


def test_parse_tolerates_surrounding_text():
    r = parse_sentiment_response('here: {"sentiment": 0.2, "confidence": 0.5} trailing')
    assert r.sentiment == 0.2 and r.confidence == 0.5 and r.rationale == ""


def test_parse_rejects_garbage():
    for bad in ("not json at all", "{}", '{"confidence": 0.5}'):
        with pytest.raises(ValueError):
            parse_sentiment_response(bad)


def test_classify_uses_transport_and_returns_reading():
    captured = {}

    def fake_transport(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return json.dumps({"response": '{"sentiment": -0.6, "confidence": 0.9, "rationale": "fear"}'})

    clf = OllamaSentimentClassifier(model="llama3.1", transport=fake_transport)
    r = clf.classify("BTC/JPY", ["bad news"])
    assert (r.sentiment, r.confidence) == (-0.6, 0.9)
    assert captured["payload"]["model"] == "llama3.1"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"]["temperature"] == 0.0  # deterministic
