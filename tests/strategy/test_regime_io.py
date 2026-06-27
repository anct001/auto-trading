"""Tests for regime_state.json I/O (§3/§5). Fail-to-None on any problem (never crash the loop)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.strategy.regime import RegimeState, load_regime_state, write_regime_state

NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_roundtrip(tmp_path):
    st = RegimeState(1, NOW, 600, {"BTC/JPY": "trend"})
    p = tmp_path / "regime_state.json"
    write_regime_state(p, st)
    out = load_regime_state(p)
    assert out is not None and out.regimes["BTC/JPY"] == "trend"
    assert out.generated_at == NOW and out.is_fresh(NOW)


def test_absent_and_malformed_are_none(tmp_path):
    assert load_regime_state(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_regime_state(bad) is None
