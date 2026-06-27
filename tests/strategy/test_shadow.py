"""Tests for src/strategy/shadow.py — shadow-mode hypothetical P&L (§15, P3).

Shadow mode screens a new/modified strategy by tracking the P&L it WOULD have made — never
trading. Fills are at the mark (systematically optimistic, §2), so it is a relative screen, not
absolute truth. The non-negotiable property pinned here: it never opens a short and never touches
an exchange/broker.
"""
from __future__ import annotations

import pandas as pd

from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD
from src.strategy.shadow import ShadowBook

T0 = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def _observe(book, seq):
    for i, (price, intent) in enumerate(seq):
        book.observe(timestamp=T0 + i * HOUR, price=price, intent=intent)


def test_round_trip_profit_is_tracked():
    book = ShadowBook(initial_equity=1000.0)
    _observe(book, [(100.0, INTENT_ENTER_LONG), (110.0, INTENT_HOLD), (120.0, INTENT_EXIT)])
    # bought all equity at 100 (10 units), sold at 120 → +20% (optimistic, no costs by default)
    assert book.trade_count == 1
    assert book.realized_pnl == 200.0
    assert book.equity(120.0) == 1200.0
    assert not book.in_position


def test_marks_to_market_while_holding():
    book = ShadowBook(initial_equity=1000.0)
    _observe(book, [(100.0, INTENT_ENTER_LONG), (130.0, INTENT_HOLD)])
    assert book.in_position
    assert book.realized_pnl == 0.0          # not closed yet
    assert book.equity(130.0) == 1300.0      # unrealized mark-to-market


def test_enter_while_long_is_ignored():
    book = ShadowBook(initial_equity=1000.0)
    _observe(book, [(100.0, INTENT_ENTER_LONG), (105.0, INTENT_ENTER_LONG)])
    assert book.position_qty == 10.0  # no second entry, no doubling


def test_exit_while_flat_is_ignored_no_short():
    book = ShadowBook(initial_equity=1000.0)
    _observe(book, [(100.0, INTENT_EXIT), (90.0, INTENT_EXIT)])
    assert not book.in_position
    assert book.position_qty == 0.0   # never goes short
    assert book.trade_count == 0


def test_optional_fee_makes_it_less_optimistic():
    free = ShadowBook(initial_equity=1000.0)
    costed = ShadowBook(initial_equity=1000.0, fee=0.001)
    seq = [(100.0, INTENT_ENTER_LONG), (120.0, INTENT_EXIT)]
    _observe(free, seq)
    _observe(costed, seq)
    assert costed.realized_pnl < free.realized_pnl  # fees eat into hypothetical P&L


def test_stats_summary():
    book = ShadowBook(initial_equity=1000.0)
    _observe(book, [(100.0, INTENT_ENTER_LONG), (120.0, INTENT_EXIT),
                    (120.0, INTENT_ENTER_LONG), (108.0, INTENT_EXIT)])
    s = book.stats()
    assert s["trade_count"] == 2
    assert s["wins"] == 1 and s["losses"] == 1
    assert "total_return" in s and "realized_pnl" in s
