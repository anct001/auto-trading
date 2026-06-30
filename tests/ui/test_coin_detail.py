"""Tests for src/ui/coin_detail.py — §12 coin-detail K-line + indicator overlays (read-only)."""
from __future__ import annotations

import pandas as pd

from src.ui.coin_detail import build_coin_detail

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _frame(n=300):
    rows, p = [], 100.0
    for i in range(n):
        p *= 1.002
        rows.append([T0 + i * HOUR, p, p * 1.003, p * 0.997, p, 10.0 + i])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_candles_downsampled_to_max():
    d = build_coin_detail("BTC/JPY", _frame(300), max_candles=200)
    assert d["pair"] == "BTC/JPY"
    assert len(d["candles"]) == 200  # last 200
    c = d["candles"][0]
    assert {"t", "o", "h", "l", "c", "v"} <= c.keys()


def test_overlays_align_with_candles():
    d = build_coin_detail("BTC/JPY", _frame(120), max_candles=200)
    assert len(d["candles"]) == 120
    assert len(d["overlays"]["ema_fast"]) == 120
    assert len(d["overlays"]["ema_slow"]) == 120
    assert len(d["overlays"]["rsi"]) == 120          # RSI sub-pane series
    assert d["regime"] in ("trend", "range")          # deterministic regime label


def test_readouts_present():
    d = build_coin_detail("BTC/JPY", _frame(120))
    assert d["readouts"]["close"] > 0 and "atr_pct" in d["readouts"]
    assert d["readouts"]["ema_fast_above_slow"] is True  # uptrend


def test_empty_frame():
    d = build_coin_detail("BTC/JPY", _frame(0))
    assert d["candles"] == [] and d["overlays"]["ema_fast"] == []
