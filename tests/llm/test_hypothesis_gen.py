"""Tests for src/llm/hypothesis_gen.py — offline LLM strategy-hypothesis proposer (P2).

No network (transport injected). Pins prompt construction, JSON-array parsing, and — the §5
discipline — the strict parameter budget: proposals exceeding it are dropped, not trimmed.
The proposer only SUGGESTS; a human approves each before it is backtested (Inv. 1/8).
"""
from __future__ import annotations

import json

import pytest

from src.llm.hypothesis_gen import (
    OllamaHypothesisProposer,
    build_hypothesis_prompt,
    parse_hypotheses_response,
)


def test_prompt_includes_context_and_budget():
    p = build_hypothesis_prompt("BTC/JPY trends weekly", n=3, max_params=2)
    assert "BTC/JPY trends weekly" in p
    assert "3" in p and ("2" in p)
    assert "JSON" in p or "json" in p


def test_parse_valid_array():
    text = json.dumps([
        {"name": "ema_fast_slow", "params": {"ema_fast": 8, "ema_slow": 21},
         "target_regime": "trend", "rationale": "momentum"},
        {"name": "rsi_revert", "params": {"rsi": 30}, "target_regime": "range", "rationale": "mr"},
    ])
    out = parse_hypotheses_response(text, max_params=2)
    assert [h.name for h in out] == ["ema_fast_slow", "rsi_revert"]
    assert out[0].params == {"ema_fast": 8, "ema_slow": 21}
    assert out[1].target_regime == "range"


def test_parse_enforces_parameter_budget():
    text = json.dumps([
        {"name": "ok", "params": {"a": 1, "b": 2}, "target_regime": "trend", "rationale": "x"},
        {"name": "overfit", "params": {"a": 1, "b": 2, "c": 3}, "target_regime": "trend",
         "rationale": "too many knobs"},
    ])
    out = parse_hypotheses_response(text, max_params=2)
    assert [h.name for h in out] == ["ok"]  # the 3-param candidate is dropped (§5 budget)


def test_parse_tolerates_object_wrapper_and_prose():
    text = 'sure! {"hypotheses": [{"name":"h","params":{"p":1},"target_regime":"trend"}]} done'
    out = parse_hypotheses_response(text, max_params=2)
    assert len(out) == 1 and out[0].name == "h" and out[0].rationale == ""


def test_parse_rejects_when_no_valid_hypotheses():
    for bad in ("not json", "[]", json.dumps([{"params": {"a": 1}}])):  # last has no name
        with pytest.raises(ValueError):
            parse_hypotheses_response(bad, max_params=2)


def test_propose_uses_transport():
    captured = {}

    def fake_transport(url, payload):
        captured["payload"] = payload
        return json.dumps({"response": json.dumps(
            [{"name": "h1", "params": {"ema_fast": 5, "ema_slow": 20}, "target_regime": "trend"}]
        )})

    proposer = OllamaHypothesisProposer(model="llama3.1", transport=fake_transport)
    out = proposer.propose("context here", n=1, max_params=2)
    assert out[0].name == "h1"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["options"]["temperature"] > 0  # some creativity for ideation
