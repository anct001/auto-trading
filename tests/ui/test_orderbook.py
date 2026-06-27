"""Tests for src/ui/orderbook.py — §12 order-book depth view (read-only)."""
from __future__ import annotations

import math

from src.ui.orderbook import build_orderbook_view


def _book():
    return {
        "bids": [[100.0, 1.0], [99.0, 2.0], [98.0, 3.0]],   # desc
        "asks": [[101.0, 1.5], [102.0, 2.5], [103.0, 4.0]],  # asc
    }


def test_cumulative_depth_and_top_of_book():
    v = build_orderbook_view(_book(), levels=3)
    assert [b["cum"] for b in v["bids"]] == [1.0, 3.0, 6.0]
    assert [a["cum"] for a in v["asks"]] == [1.5, 4.0, 8.0]
    assert v["best_bid"] == 100.0 and v["best_ask"] == 101.0


def test_spread_and_mid():
    v = build_orderbook_view(_book())
    assert v["mid"] == 100.5 and v["spread"] == 1.0
    assert math.isclose(v["spread_pct"], 1.0 / 100.5 * 100.0)


def test_levels_cap():
    v = build_orderbook_view(_book(), levels=2)
    assert len(v["bids"]) == 2 and len(v["asks"]) == 2


def test_empty_book_is_safe():
    v = build_orderbook_view({"bids": [], "asks": []})
    assert v["bids"] == [] and v["asks"] == []
    assert v["mid"] is None and v["spread"] is None and v["spread_pct"] is None


def test_missing_keys_tolerated():
    v = build_orderbook_view({})
    assert v["bids"] == [] and v["best_bid"] is None
