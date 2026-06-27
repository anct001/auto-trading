"""Tests for src/ui/trades_feed.py — public market trade-tape shaper (§12, read-only)."""
from __future__ import annotations

from src.ui.trades_feed import format_trades

HOUR_MS = 3_600_000


def _raw(n):
    # ccxt-style trades, ascending by time
    return [{"timestamp": i * 1000, "price": 100.0 + i, "amount": 0.1 * i,
             "side": "buy" if i % 2 else "sell"} for i in range(n)]


def test_newest_first_and_limited():
    out = format_trades(_raw(50), limit=10)
    assert len(out) == 10
    assert out[0]["price"] == 100.0 + 49  # newest first
    assert {"time", "price", "amount", "side"} <= out[0].keys()


def test_time_formatted_hms():
    out = format_trades([{"timestamp": 3_661_000, "price": 1.0, "amount": 1.0, "side": "buy"}])
    assert out[0]["time"] == "01:01:01"  # 1h1m1s from epoch (UTC)


def test_empty_and_missing_fields_safe():
    assert format_trades([]) == []
    out = format_trades([{"price": 1.0, "amount": 2.0}])  # no timestamp/side
    assert out[0]["side"] == "" and out[0]["time"] == ""
