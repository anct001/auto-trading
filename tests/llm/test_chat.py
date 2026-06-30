"""Tests for src/llm/chat.py — the read-only operator chat assistant (Inv 1/2/6).

No Ollama server: the transport is injected. These pin the read-only contract (system prompt
refuses to act), the whitelisted context summary (no accidental field leak), parsing of the
Ollama reply shapes, and the fail-soft entry point (a backend error never raises).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from src.llm import chat


def _snapshot():
    return {
        "equity": 1_000_000.0,
        "day_return_pct": -1.5,
        "daily_soft_pct": -3.0,
        "daily_hard_pct": -6.0,
        "drawdown_pct": -4.0,
        "killswitch_pct": -12.0,
        "gross_exposure_pct": 18.0,
        "gross_cap_pct": 100.0,
        "killswitch_engaged": False,
        "killswitch_reason": None,
        "positions": [
            {"pair": "BTC/JPY", "qty": 0.02, "entry_price": 9_500_000.0, "value": 200_000.0,
             "unrealized_pnl": 5_000.0, "exposure_pct": 20.0},
        ],
        "decision_log": [
            {"timestamp": "2026-06-30T10:00:00Z", "type": "RiskRejected", "pair": "BTC/JPY",
             "detail": "buy BTC/JPY below_min_notional"},
        ],
    }


def test_context_summary_includes_key_state():
    s = chat.build_context_summary(_snapshot())
    assert "Equity: 1000000.0" in s
    assert "BTC/JPY" in s and "below_min_notional" in s
    assert "Kill-switch: armed" in s


def test_context_summary_handles_flat_and_empty():
    assert "flat (no open positions)" in chat.build_context_summary(
        {**_snapshot(), "positions": []}
    )
    assert chat.build_context_summary({}) == "(no live state available)"


def test_context_summary_is_whitelisted_no_unknown_field_leaks():
    # a secret-looking field smuggled into the snapshot must NOT appear in the prompt context
    snap = {**_snapshot(), "api_secret": "SHOULD_NOT_LEAK", "token": "ghp_xxx"}
    s = chat.build_context_summary(snap)
    assert "SHOULD_NOT_LEAK" not in s and "ghp_xxx" not in s


def test_messages_carry_system_refusal_contract_and_question():
    msgs = chat.build_messages("why are we flat?", _snapshot())
    assert msgs[0]["role"] == "system"
    sysmsg = msgs[0]["content"].lower()
    assert "read-only" in sysmsg and "cannot" in sysmsg
    assert "why are we flat?" in msgs[1]["content"]


def test_long_question_is_trimmed():
    msgs = chat.build_messages("x" * 5000, _snapshot())
    # question is bounded so a huge paste can't blow the context window
    question_part = msgs[1]["content"].split("OPERATOR QUESTION: ", 1)[1]
    assert len(question_part) == chat._MAX_QUESTION_CHARS


def test_parse_chat_response_api_chat_shape():
    raw = '{"message": {"role": "assistant", "content": "  We are flat because no signal.  "}}'
    assert chat.parse_chat_response(raw) == "We are flat because no signal."


def test_parse_chat_response_generate_fallback_and_garbage():
    assert chat.parse_chat_response('{"response": "hi"}') == "hi"
    with pytest.raises(ValueError):
        chat.parse_chat_response("not json")
    with pytest.raises(ValueError):
        chat.parse_chat_response('{"unexpected": 1}')


def test_ollama_chat_sends_expected_payload_and_returns_content():
    sent = {}

    def transport(url, payload):
        sent["url"] = url
        sent["payload"] = payload
        return '{"message": {"content": "answer text"}}'

    client = chat.OllamaChat(model="llama3.1", transport=transport)
    out = client.answer("status?", _snapshot())
    assert out == "answer text"
    assert sent["url"].endswith("/api/chat")
    assert sent["payload"]["model"] == "llama3.1"
    assert sent["payload"]["stream"] is False
    assert sent["payload"]["messages"][0]["role"] == "system"


def test_answer_is_fail_soft_when_backend_raises():
    def boom(url, payload):
        raise ConnectionError("ollama down")

    out = chat.answer("status?", _snapshot(), client=chat.OllamaChat(transport=boom))
    assert out["error"] == "ollama down"
    assert "unavailable" in out["answer"]  # graceful, not an exception


def test_answer_refuses_empty_question_without_calling_model():
    called = {"n": 0}

    def transport(url, payload):
        called["n"] += 1
        return '{"message": {"content": "x"}}'

    out = chat.answer("   ", _snapshot(), client=chat.OllamaChat(transport=transport))
    assert out["answer"] == "" and out["error"] == "empty question"
    assert called["n"] == 0  # the model is never consulted for an empty question


def test_chat_module_imports_nothing_from_the_order_path():
    """Inv 1/3 by construction: the chat module must not import risk/execution/broker/engine —
    so there is provably no code path from a chat reply to an order."""
    src = pathlib.Path(chat.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("risk", "execution", "broker", "engine", "fast_loop")
    leaks = [m for m in imported if any(f in m for f in forbidden)]
    assert leaks == [], f"chat must not import the order path: {leaks}"
