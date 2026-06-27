"""Tests for backtest/fill_parity.py — §2 fill-realism / dry-run parity.

Dry-run (and our paper broker) fills at the mark — systematically optimistic. This harness
quantifies the gap vs a realistic slippage/latency reference (the role Freqtrade's dry-run plays
at P3). Pins the fill models and the optimism-gap accounting.
"""
from __future__ import annotations

import math

from backtest.fill_parity import (
    MarkFill,
    SlippageLatencyFill,
    compare_fills,
)


def test_mark_fill_is_optimistic_no_slippage():
    f = MarkFill().fill(side="buy", ref_price=100.0, qty=2.0)
    assert f.price == 100.0 and f.filled_qty == 2.0


def test_slippage_moves_against_you():
    m = SlippageLatencyFill(slippage_bps=50)  # 0.50%
    buy = m.fill(side="buy", ref_price=100.0, qty=1.0)
    sell = m.fill(side="sell", ref_price=100.0, qty=1.0)
    assert math.isclose(buy.price, 100.5)   # buy fills higher
    assert math.isclose(sell.price, 99.5)   # sell fills lower


def test_partial_fill_ratio():
    m = SlippageLatencyFill(slippage_bps=0, fill_ratio=0.5)
    assert m.fill(side="buy", ref_price=100.0, qty=4.0).filled_qty == 2.0


def test_zero_slippage_has_no_optimism_gap():
    intents = [{"side": "buy", "ref_price": 100.0, "qty": 1.0},
               {"side": "sell", "ref_price": 110.0, "qty": 1.0}]
    rep = compare_fills(intents, MarkFill(), SlippageLatencyFill(slippage_bps=0))
    assert rep.n_fills == 2
    assert math.isclose(rep.total_optimism, 0.0, abs_tol=1e-9)
    assert math.isclose(rep.optimism_pct_of_volume, 0.0, abs_tol=1e-9)


def test_optimism_gap_equals_hidden_slippage_cost():
    # paper ignores slippage; the gap = sum(qty*ref_price*s) the dry-run P&L overstates
    intents = [{"side": "buy", "ref_price": 100.0, "qty": 1.0},
               {"side": "sell", "ref_price": 200.0, "qty": 0.5}]
    s = 0.001  # 10 bps
    rep = compare_fills(intents, MarkFill(), SlippageLatencyFill(slippage_bps=10))
    expected = 100.0 * 1.0 * s + 200.0 * 0.5 * s
    assert math.isclose(rep.total_optimism, expected, rel_tol=1e-9)
    assert rep.mean_slippage_pct > 0
    assert math.isclose(rep.avg_fill_ratio, 1.0)


def test_report_summary_is_readable():
    rep = compare_fills([{"side": "buy", "ref_price": 100.0, "qty": 1.0}],
                        MarkFill(), SlippageLatencyFill(slippage_bps=25))
    s = rep.summary()
    assert "optimism" in s.lower() and "%" in s
