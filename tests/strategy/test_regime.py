"""Tests for src/strategy/regime.py — deterministic regime gating (§5).

A strategy declares a target_regime; the slow loop classifies the current regime into
regime_state.json; DETERMINISTIC code (not the LLM) disables the strategy when the live regime
doesn't match. It may only DISABLE — never trigger/flip/size a trade. Unknown/absent/stale regime
→ enabled (the fast loop must run without the file, Inv. 2).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.strategy.regime import RegimeState, is_strategy_enabled

PAIR = "BTC/JPY"
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def _state(regime, *, generated_at=NOW, ttl=600):
    return RegimeState(schema_version=1, generated_at=generated_at, ttl_seconds=ttl,
                       regimes={PAIR: regime})


def test_any_regime_strategy_is_always_enabled():
    assert is_strategy_enabled(_state("range"), PAIR, "any", now=NOW) is True


def test_matching_regime_enabled():
    assert is_strategy_enabled(_state("trend"), PAIR, "trend", now=NOW) is True


def test_mismatched_regime_disabled():
    assert is_strategy_enabled(_state("range"), PAIR, "trend", now=NOW) is False


def test_absent_state_is_enabled():
    assert is_strategy_enabled(None, PAIR, "trend", now=NOW) is True


def test_stale_state_is_enabled():
    stale = _state("range", generated_at=NOW - timedelta(seconds=601), ttl=600)
    assert is_strategy_enabled(stale, PAIR, "trend", now=NOW) is True  # don't trust stale → don't disable


def test_unknown_pair_is_enabled():
    assert is_strategy_enabled(_state("range"), "ETH/JPY", "trend", now=NOW) is True
