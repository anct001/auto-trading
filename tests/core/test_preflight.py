"""Tests for src/core/preflight.py — §14 go-live readiness gate (never trades)."""
from __future__ import annotations

import json
from pathlib import Path

from src.core.preflight import (
    FAIL,
    MANUAL,
    PASS,
    SIGNED,
    Check,
    is_ready,
    manual_slugs,
    record_signoff,
    run_preflight,
)

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


# ---- sign-off registry (audit #8) --------------------------------------------------------------

def _repo_with_signoffs(tmp_path, signoffs: dict) -> Path:
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "signoff.json").write_text(
        json.dumps({"signoffs": signoffs}), encoding="utf-8")
    return tmp_path


def test_manual_slugs_are_stable_and_unique():
    slugs = manual_slugs()
    assert len(slugs) == len(set(slugs)) == 13
    assert "human-signoff" in slugs and "forward-dry-run" in slugs


def test_signed_items_show_who_and_when(tmp_path):
    _repo_with_signoffs(tmp_path, {"forward-dry-run": {"operator": "anct", "date": "2026-07-02"}})
    checks = run_preflight(tmp_path)
    by_slug = {c.slug: c for c in checks if c.slug}
    assert by_slug["forward-dry-run"].status == SIGNED
    assert "anct" in by_slug["forward-dry-run"].detail
    assert "2026-07-02" in by_slug["forward-dry-run"].detail
    # everything not signed stays MANUAL (pending)
    assert by_slug["human-signoff"].status == MANUAL


def test_unknown_or_malformed_signoffs_are_ignored(tmp_path):
    _repo_with_signoffs(tmp_path, {"not-a-real-slug": {"operator": "x", "date": "y"},
                                   "human-signoff": "just a string"})
    checks = run_preflight(tmp_path)
    assert all(c.status == MANUAL for c in checks if c.slug)  # nothing validly signed


def test_corrupt_signoff_file_fails_soft_to_all_manual(tmp_path):
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "signoff.json").write_text("{nope", encoding="utf-8")
    checks = run_preflight(tmp_path)
    assert all(c.status == MANUAL for c in checks if c.slug)


def test_ready_requires_all_auto_pass_and_all_manual_signed():
    auto_pass = [Check("a", PASS)]
    all_signed = [Check(s, SIGNED, slug=s) for s in manual_slugs()]
    assert is_ready(auto_pass + all_signed) is True
    # one manual item left unsigned → NOT ready (Inv 4/5)
    one_pending = all_signed[:-1] + [Check("x", MANUAL, slug=manual_slugs()[-1])]
    assert is_ready(auto_pass + one_pending) is False
    # an auto FAIL can never be signed away
    assert is_ready([Check("a", FAIL)] + all_signed) is False


def test_record_signoff_roundtrip_and_validation(tmp_path):
    (tmp_path / "ops").mkdir()
    record_signoff(tmp_path, "risk-drills", "anct", date="2026-07-02")
    data = json.loads((tmp_path / "ops" / "signoff.json").read_text(encoding="utf-8"))
    assert data["signoffs"]["risk-drills"] == {"operator": "anct", "date": "2026-07-02"}
    # unknown slug refused; empty operator refused
    import pytest
    with pytest.raises(ValueError):
        record_signoff(tmp_path, "nope", "anct")
    with pytest.raises(ValueError):
        record_signoff(tmp_path, "risk-drills", "  ")
    # re-signing overwrites, other entries preserved
    record_signoff(tmp_path, "naked-stop", "partner", date="2026-07-03")
    data = json.loads((tmp_path / "ops" / "signoff.json").read_text(encoding="utf-8"))
    assert set(data["signoffs"]) == {"risk-drills", "naked-stop"}


def test_real_repo_registry_present_but_nothing_presigned():
    # the committed template must never ship pre-signed items (Inv 5)
    checks = run_preflight(ROOT)
    assert sum(1 for c in checks if c.status == MANUAL) == 13
    assert not any(c.status == SIGNED for c in checks)
    assert is_ready(checks) is False
