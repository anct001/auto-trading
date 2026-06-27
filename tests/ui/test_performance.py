"""Tests for src/ui/performance.py — live performance summary from closed paper trades."""
from __future__ import annotations

import math

from src.ui.performance import performance_summary


def _t(ret, pnl):
    return {"return": ret, "pnl": pnl}


def test_empty():
    s = performance_summary([])
    assert s["trade_count"] == 0 and s["win_rate"] == 0.0 and s["total_pnl"] == 0.0


def test_mixed_wins_losses():
    trades = [_t(0.10, 100.0), _t(-0.05, -50.0), _t(0.02, 20.0), _t(-0.01, -10.0)]
    s = performance_summary(trades)
    assert s["trade_count"] == 4
    assert s["wins"] == 2 and s["losses"] == 2
    assert math.isclose(s["win_rate"], 0.5)
    assert math.isclose(s["total_pnl"], 60.0)
    # profit factor = gross win / gross loss = 120 / 60 = 2.0
    assert math.isclose(s["profit_factor"], 2.0)
    assert math.isclose(s["best"], 0.10) and math.isclose(s["worst"], -0.05)
    assert math.isclose(s["expectancy"], (0.10 - 0.05 + 0.02 - 0.01) / 4)


def test_all_wins_profit_factor_is_none_json_safe():
    import json
    s = performance_summary([_t(0.1, 10.0), _t(0.2, 20.0)])
    assert s["win_rate"] == 1.0
    assert s["profit_factor"] is None  # undefined/infinite — JSON-safe (browser rejects Infinity)
    assert "Infinity" not in json.dumps(s)  # serializes cleanly
    assert s["total_pnl"] == 30.0
