"""Tests for src/core/status.py — the '/status' health snapshot (read-only, fail-soft)."""
from __future__ import annotations

from src.core.status import gather_status, read_current_phase


def test_gather_status_on_the_real_repo():
    # runs against the actual repo files; probe injected so no network
    s = gather_status(root=".", env={}, ollama_probe=lambda: False)
    assert s["paper_mode"] is True and s["version"]
    assert s["phase"].startswith("P0")                      # parsed from PROGRESS.md
    assert s["config"]["venue"] == "bitbank"
    assert s["config"]["pairs"] == ["BTC/JPY"]
    assert s["config"]["leverage"] == 0
    assert s["auto_checks_total"] > 0
    assert s["auto_checks_pass"] == s["auto_checks_total"]  # repo is healthy
    assert s["ready_for_real_capital"] is False             # manual §14 gates pending — correct
    assert s["manual_items_pending"] > 0
    assert s["ollama_reachable"] is False
    # with no env keys, only the local provider is available
    av = {p["name"]: p["available"] for p in s["chat_providers"]}
    assert av["ollama"] is True and av["anthropic"] is False


def test_gather_status_sees_cloud_keys_and_ollama():
    s = gather_status(root=".", env={"OPENAI_API_KEY": "k"}, ollama_probe=lambda: True)
    av = {p["name"]: p["available"] for p in s["chat_providers"]}
    assert av["openai"] is True and av["gemini"] is False
    assert s["ollama_reachable"] is True
    # key VALUES never enter the payload
    import json
    assert "k" != json.dumps(s).count  # trivial guard; real check below
    assert '"k"' not in json.dumps(s)


def test_gather_status_is_fail_soft_on_missing_root(tmp_path):
    s = gather_status(root=tmp_path, env={}, ollama_probe=lambda: False)
    assert s["phase"] == "unknown"
    assert s["config_error"]                                # broken config = red light, not a crash
    assert s["ready_for_real_capital"] is False


def test_read_current_phase_missing_file():
    assert read_current_phase("/nonexistent/PROGRESS.md") == "unknown"
