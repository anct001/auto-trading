"""Tests for src/ui/markets.py — §12 markets overview / watchlist + heatmap (read-only).

Operator research surfaces: a sortable/filterable multi-asset overview and heatmap tiles. NEVER
a signal path (§12) — pure viewing over OHLCV. Pins the per-pair row, sorting, gainers/losers,
filtering (reusing the screener), and heatmap tile shape.
"""
from __future__ import annotations

import pandas as pd

from src.ui.markets import build_markets_overview, heatmap_tiles, top_gainers, top_losers
from src.ui.screener import Condition

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _frame(step, n=40, vol=10.0):
    rows, price = [], 100.0
    for i in range(n):
        price *= (1.0 + step)
        rows.append([T0 + i * HOUR, price, price * 1.002, price * 0.998, price, vol])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _universe():
    return {"UP": _frame(0.01, vol=5.0), "FLAT": _frame(0.0, vol=20.0), "DOWN": _frame(-0.01, vol=2.0)}


def test_overview_row_shape_and_sort_by_change():
    rows = build_markets_overview(_universe(), lookback=24)
    assert [r["pair"] for r in rows] == ["UP", "FLAT", "DOWN"]  # default sort: change desc
    up = rows[0]
    assert up["change_pct"] > 0 and up["close"] > 0
    assert "atr_pct" in up and "trend_up" in up and "volume" in up


def test_top_gainers_and_losers():
    rows = build_markets_overview(_universe(), lookback=24)
    assert top_gainers(rows, 1)[0]["pair"] == "UP"
    assert top_losers(rows, 1)[0]["pair"] == "DOWN"


def test_filter_with_screener_conditions():
    rows = build_markets_overview(_universe(), lookback=24)
    # only pairs trending up via the screener predicate
    from src.ui.screener import screen
    hits = screen({r["pair"]: r for r in rows}, [Condition("trend_up", "is_true", None)])
    assert hits == ["UP"]


def test_heatmap_tiles_sized_by_volume_colored_by_change():
    rows = build_markets_overview(_universe(), lookback=24)
    tiles = heatmap_tiles(rows)
    # sized by the volume proxy (FLAT has the largest), each carries its change for coloring
    assert tiles[0]["pair"] == "FLAT"
    assert all("size" in t and "change_pct" in t for t in tiles)


def test_sort_by_custom_key():
    rows = build_markets_overview(_universe(), lookback=24, sort_by="volume", descending=True)
    assert rows[0]["pair"] == "FLAT"  # highest volume first
