"""Tests for src/data/funding.py — funding-rate history + causal alignment (§8)."""
from __future__ import annotations

import pandas as pd

from src.data import funding

_H = 3_600_000  # 1h in ms
_8H = 8 * _H    # funding settles every 8h on most perps


def _rows(start_ms, n, rate=0.0001, step=_8H):
    return [{"symbol": "BTC/USDT:USDT", "timestamp": start_ms + i * step, "fundingRate": rate}
            for i in range(n)]


def test_to_funding_frame_drops_still_forming_and_dedupes():
    now = 100 * _8H
    rows = _rows(0, 5) + [{"timestamp": 2 * _8H, "fundingRate": 0.5}]  # dup ts 2*8h, last wins
    rows += [{"timestamp": now, "fundingRate": 9.9}]                    # at now -> still forming, drop
    df = funding.to_funding_frame(rows, now_ms=now)
    expected = pd.to_datetime([0, _8H, 2 * _8H, 3 * _8H, 4 * _8H], unit="ms", utc=True)
    assert df["timestamp"].tolist() == list(expected)
    assert df["funding_rate"].iloc[2] == 0.5              # dedup last-wins
    assert str(df["timestamp"].dt.tz) == "UTC"            # tz-aware UTC (resolution venue-agnostic)
    assert 9.9 not in set(df["funding_rate"])             # still-forming dropped


def test_to_funding_frame_empty():
    df = funding.to_funding_frame([], now_ms=_8H)
    assert len(df) == 0 and list(df.columns) == ["timestamp", "funding_rate"]


class _FakeExchange:
    """Serves funding pages honoring `since`/`limit` (like a real venue)."""

    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda r: r["timestamp"])

    def fetch_funding_rate_history(self, symbol, since=None, limit=None):
        rows = [r for r in self._rows if since is None or r["timestamp"] >= since]
        return rows[: (limit or 200)]


def test_fetch_funding_history_paginates_and_dedupes():
    now = 50 * _8H
    ex = _FakeExchange(_rows(0, 30))
    df = funding.fetch_funding_history(ex, "BTC/USDT:USDT", since_ms=0, page_limit=10, now_ms=now)
    assert len(df) == 30                                  # paged past the 10-row cap
    assert df["timestamp"].is_monotonic_increasing
    assert df["timestamp"].is_unique


def test_fetch_funding_history_stops_when_no_progress():
    # exchange ignores `since` and always returns the same first page -> must not spin forever
    class Stuck:
        def fetch_funding_rate_history(self, symbol, since=None, limit=None):
            return _rows(0, 5)
    df = funding.fetch_funding_history(Stuck(), "X", since_ms=0, page_limit=5, now_ms=100 * _8H)
    assert len(df) == 5


def _ohlcv(start_ms, n):
    ts = pd.to_datetime([start_ms + i * _H for i in range(n)], unit="ms", utc=True)
    return pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 1.0})


def test_align_funding_is_causal_backward_join():
    # funding prints at 0 and 8h; candles hourly from 0..11h
    fdf = funding.to_funding_frame(
        [{"timestamp": 0, "fundingRate": -0.001}, {"timestamp": _8H, "fundingRate": 0.002}],
        now_ms=100 * _8H)
    candles = _ohlcv(0, 12)
    out = funding.align_funding(candles, fdf)
    # candle at t=0..7h sees the t=0 print; t=8h..11h sees the t=8h print (latest known <= t)
    assert out["funding"].iloc[0] == -0.001
    assert out["funding"].iloc[7] == -0.001
    assert out["funding"].iloc[8] == 0.002
    assert out["funding"].iloc[11] == 0.002


def test_align_funding_leading_nan_before_first_print():
    fdf = funding.to_funding_frame([{"timestamp": 5 * _H, "fundingRate": 0.01}], now_ms=100 * _8H)
    out = funding.align_funding(_ohlcv(0, 8), fdf)
    assert out["funding"].iloc[0] != out["funding"].iloc[0]  # NaN before the first funding print
    assert out["funding"].iloc[5] == 0.01


def test_align_funding_empty_funding_gives_nan_column():
    out = funding.align_funding(_ohlcv(0, 3), funding.to_funding_frame([], now_ms=_8H))
    assert "funding" in out.columns and out["funding"].isna().all()
