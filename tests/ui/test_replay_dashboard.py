"""Tests for src/ui/replay_dashboard.py — multi-pair replay comparison model (§12, read-only)."""
from __future__ import annotations

import math

from src.ui.replay_dashboard import (
    ReplayResult,
    build_replay_comparison,
    summarize_result,
)


def _eq(*vals):
    return [{"t": str(i), "equity": v} for i, v in enumerate(vals)]


def test_summarize_computes_return_drawdown_and_trade_stats():
    trades = [{"return": 0.05, "pnl": 500.0}, {"return": -0.02, "pnl": -200.0}]
    r = summarize_result("BTC/USDT", trades=trades,
                         equity_history=_eq(10000, 10500, 10290, 10600), start_equity=10000.0)
    assert math.isclose(r.final_equity, 10600.0)
    assert math.isclose(r.total_return, 0.06, rel_tol=1e-9)
    assert r.max_drawdown < 0  # there was a dip (10500 -> 10290)
    assert r.performance["trade_count"] == 2 and r.performance["wins"] == 1


def test_max_drawdown_captures_worst_peak_to_trough():
    r = summarize_result("X", trades=[], equity_history=_eq(100, 120, 60, 90), start_equity=100.0)
    # peak 120 -> trough 60 = -50%
    assert math.isclose(r.max_drawdown, -0.5, rel_tol=1e-9)


def test_summarize_handles_empty_equity_and_trades():
    r = summarize_result("Y", trades=[], equity_history=[], start_equity=10000.0)
    assert r.final_equity == 10000.0 and r.total_return == 0.0
    assert r.max_drawdown == 0.0 and r.sharpe == 0.0
    assert r.performance["trade_count"] == 0


def test_comparison_sorts_by_return_and_flags_best_worst():
    results = {
        "BTC/USDT": summarize_result("BTC/USDT", trades=[{"return": 0.1, "pnl": 1000.0}],
                                     equity_history=_eq(10000, 11000), start_equity=10000.0),
        "ETH/USDT": summarize_result("ETH/USDT", trades=[{"return": -0.05, "pnl": -500.0}],
                                     equity_history=_eq(10000, 9500), start_equity=10000.0),
        "SOL/USDT": summarize_result("SOL/USDT", trades=[{"return": 0.03, "pnl": 300.0}],
                                     equity_history=_eq(10000, 10300), start_equity=10000.0),
    }
    comp = build_replay_comparison(results)
    assert [row["pair"] for row in comp["table"]] == ["BTC/USDT", "SOL/USDT", "ETH/USDT"]
    assert comp["best_pair"] == "BTC/USDT" and comp["worst_pair"] == "ETH/USDT"
    assert comp["pairs"] == ["BTC/USDT", "SOL/USDT", "ETH/USDT"]
    assert set(comp["detail"]) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}


def test_comparison_is_json_safe_infinite_profit_factor_stays_none():
    import json
    r = ReplayResult("Z", 10000.0, 10500.0, 0.05, -0.01, 0.5,
                     {"trade_count": 1, "profit_factor": None, "win_rate": 1.0, "total_pnl": 500.0},
                     trades=[], equity_curve=[])
    comp = build_replay_comparison({"Z": r})
    json.dumps(comp)  # must not raise (None, not float('inf'))
    assert comp["table"][0]["profit_factor"] is None


def test_comparison_empty_is_safe():
    comp = build_replay_comparison({})
    assert comp["table"] == [] and comp["best_pair"] is None and comp["pairs"] == []
