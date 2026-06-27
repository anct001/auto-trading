"""Tests for src/core/clock.py — NTP-synced clock; reject on skew (§9).

A skewed clock corrupts candle-close alignment, order timestamps, and TIF — so clock skew is a
circuit breaker: pause and alert, never trade. The reference time source is injected so the skew
check is testable without real NTP.
"""
from __future__ import annotations

import pytest

from src.core import clock


def test_within_skew_returns_offset():
    assert clock.check_skew(local_ms=1000, reference_ms=1200, max_skew_ms=500) == -200


def test_skew_beyond_threshold_raises():
    with pytest.raises(clock.ClockSkewError):
        clock.check_skew(local_ms=2000, reference_ms=1000, max_skew_ms=500)


def test_negative_skew_beyond_threshold_raises():
    with pytest.raises(clock.ClockSkewError):
        clock.check_skew(local_ms=1000, reference_ms=2000, max_skew_ms=500)


def test_boundary_skew_is_allowed():
    # exactly at the threshold is tolerated; beyond is not
    assert clock.check_skew(local_ms=1500, reference_ms=1000, max_skew_ms=500) == 500


def test_assert_clock_sane_uses_injected_sources():
    skew = clock.assert_clock_sane(
        reference_now_ms=lambda: 10_000, local_now_ms=lambda: 10_100, max_skew_ms=500
    )
    assert skew == 100


def test_assert_clock_sane_raises_on_drift():
    with pytest.raises(clock.ClockSkewError):
        clock.assert_clock_sane(
            reference_now_ms=lambda: 10_000, local_now_ms=lambda: 12_000, max_skew_ms=500
        )


def test_now_ms_is_positive_int():
    assert isinstance(clock.now_ms(), int) and clock.now_ms() > 0
