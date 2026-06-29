"""Tests for src/core/preflight.py — §14 go-live readiness gate (never trades)."""
from __future__ import annotations

from pathlib import Path

from src.core.preflight import FAIL, MANUAL, PASS, Check, is_ready, run_preflight

ROOT = Path(__file__).resolve().parents[2]


def test_auto_checks_pass_on_the_real_repo():
    checks = run_preflight(ROOT)
    auto = {c.item: c for c in checks if c.status in (PASS, FAIL)}
    # the in-process invariants must hold in the committed repo
    assert auto["Config integrity (hash-lock §15)"].status == PASS
    assert auto["Leverage off pre-P5 (Inv 4)"].status == PASS
    assert auto["Kill-switch arms & halts (Inv 5)"].status == PASS
    assert auto["No secret in tracked .env.example (Inv 6)"].status == PASS


def test_not_ready_until_operational_gates_done():
    checks = run_preflight(ROOT)
    # operator-only gates are present and unconfirmed → never auto-ready (Inv 4/5)
    assert any(c.status == MANUAL for c in checks)
    assert is_ready(checks) is False


def test_is_ready_only_when_all_pass():
    assert is_ready([Check("a", PASS), Check("b", PASS)]) is True
    assert is_ready([Check("a", PASS), Check("b", MANUAL)]) is False
    assert is_ready([]) is False
