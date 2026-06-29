"""Tests for indicators.rsi — Wilder RSI on the single feature path (§15)."""
from __future__ import annotations

import pytest

from src.features.indicators import rsi


def test_rising_only_is_100():
    out = rsi(range(1, 30), period=14)
    assert out.iloc[-1] == 100.0


def test_falling_only_is_0():
    out = rsi(range(30, 1, -1), period=14)
    assert out.iloc[-1] == 0.0


def test_in_range_and_length_preserved():
    prices = [10, 11, 10.5, 12, 11.5, 13, 12, 14, 13.5, 15, 14, 16, 15, 17, 16, 18]
    out = rsi(prices, period=14)
    assert len(out) == len(prices)
    tail = out.dropna()
    assert (tail >= 0).all() and (tail <= 100).all()


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_nonpositive_period(bad):
    with pytest.raises(ValueError):
        rsi([1, 2, 3], period=bad)
