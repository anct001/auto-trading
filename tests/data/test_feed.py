"""Tests for src/data/feed.py — §2 OHLCV feed, closed-candles-only (§8.4 no look-ahead).

Mock-based: no network. The forming-candle drop and UTC/ordering guarantees are the contract
the whole pipeline depends on, so they are pinned here.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data import feed

HOUR_MS = 3_600_000


class FakeExchange:
    """Minimal stand-in for a ccxt exchange — records calls, returns canned OHLCV."""

    def __init__(self, ohlcv):
        self._ohlcv = ohlcv
        self.sandbox = None
        self.calls = []

    def set_sandbox_mode(self, enabled):
        self.sandbox = enabled

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        return list(self._ohlcv)


def _candle(ts_ms, close=100.0):
    # [timestamp, open, high, low, close, volume]
    return [ts_ms, close, close + 1, close - 1, close, 10.0]


# ---- timeframe_to_ms -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tf,expected",
    [("1m", 60_000), ("5m", 300_000), ("1h", HOUR_MS), ("4h", 4 * HOUR_MS), ("1d", 86_400_000)],
)
def test_timeframe_to_ms_valid(tf, expected):
    assert feed.timeframe_to_ms(tf) == expected


@pytest.mark.parametrize("tf", ["", "h", "0h", "-1h", "1y", "1 h", "abc"])
def test_timeframe_to_ms_rejects_garbage(tf):
    with pytest.raises(ValueError):
        feed.timeframe_to_ms(tf)


# ---- closed-candle filtering (the no-look-ahead guarantee) -----------------------------------

def test_drops_still_forming_last_candle():
    # three closed hourly candles + one still-forming (opened this hour, not yet closed)
    raw = [_candle(0), _candle(HOUR_MS), _candle(2 * HOUR_MS), _candle(3 * HOUR_MS)]
    now_ms = 3 * HOUR_MS + 1  # the 3h candle closes at 4h, so it is still forming
    df = feed.to_closed_frame(raw, "1h", now_ms=now_ms)
    assert len(df) == 3
    last_closed = df["timestamp"].iloc[-1]
    assert last_closed == pd.Timestamp(2 * HOUR_MS, unit="ms", tz="UTC")


def test_keeps_candle_exactly_at_close_boundary():
    raw = [_candle(0), _candle(HOUR_MS)]
    # the 1h candle closes exactly at 2h; at now == close-time it IS closed → kept
    df = feed.to_closed_frame(raw, "1h", now_ms=2 * HOUR_MS)
    assert len(df) == 2


def test_timestamps_are_utc_and_strictly_increasing():
    raw = [_candle(2 * HOUR_MS), _candle(0), _candle(HOUR_MS)]  # deliberately unordered
    df = feed.to_closed_frame(raw, "1h", now_ms=10 * HOUR_MS)
    assert str(df["timestamp"].dt.tz) == "UTC"
    ts = df["timestamp"]
    assert ts.is_monotonic_increasing and ts.is_unique


def test_columns_and_numeric_dtypes():
    df = feed.to_closed_frame([_candle(0)], "1h", now_ms=10 * HOUR_MS)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    for col in ["open", "high", "low", "close", "volume"]:
        assert pd.api.types.is_numeric_dtype(df[col])


def test_empty_input_returns_empty_typed_frame():
    df = feed.to_closed_frame([], "1h", now_ms=10 * HOUR_MS)
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


# ---- fetch wiring ----------------------------------------------------------------------------

def test_fetch_closed_ohlcv_calls_exchange_and_filters():
    raw = [_candle(0), _candle(HOUR_MS), _candle(2 * HOUR_MS)]
    ex = FakeExchange(raw)
    df = feed.fetch_closed_ohlcv(ex, "BTC/USDT", "1h", limit=500, now_ms=2 * HOUR_MS + 1)
    assert ex.calls == [("BTC/USDT", "1h", None, 500)]
    assert len(df) == 2  # last candle still forming, dropped
