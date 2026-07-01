"""Tests for src/ui/replay_dashboard.py — multi-pair replay comparison model (§12, read-only)."""
from __future__ import annotations

import math

from src.ui.replay_dashboard import (
    ReplayResult,
    build_replay_comparison,
    summarize_result,
)


def _eq(*vals, exposure=None):
    return [{"t": str(i), "equity": v, "exposure": (exposure[i] if exposure else 0.0)}
            for i, v in enumerate(vals)]


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
    assert r.calmar == 0.0 and r.var95 == 0.0 and r.cvar95 == 0.0
    assert r.avg_exposure == 0.0 and r.time_in_market == 0.0
    assert r.performance["trade_count"] == 0


def test_summarize_computes_calmar_var_cvar_and_exposure():
    # equity dips then recovers; exposure held 3 of 5 ticks
    r = summarize_result("X", trades=[],
                         equity_history=_eq(100, 110, 90, 95, 120,
                                            exposure=[0.0, 0.8, 0.8, 0.8, 0.0]),
                         start_equity=100.0)
    assert r.calmar > 0  # positive return over a real drawdown
    assert math.isclose(r.calmar, r.total_return / abs(r.max_drawdown), rel_tol=1e-9)
    assert r.var95 <= 0.0 and r.cvar95 <= r.var95   # CVaR at least as bad as VaR (tail)
    assert math.isclose(r.time_in_market, 3 / 5, rel_tol=1e-9)
    assert 0.0 < r.avg_exposure < 0.8


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
    r = ReplayResult(
        pair="Z", start_equity=10000.0, final_equity=10500.0, total_return=0.05,
        max_drawdown=-0.01, sharpe=0.5, calmar=5.0, var95=-0.02, cvar95=-0.03,
        avg_exposure=0.4, time_in_market=0.6,
        performance={"trade_count": 1, "profit_factor": None, "win_rate": 1.0, "total_pnl": 500.0})
    comp = build_replay_comparison({"Z": r})
    json.dumps(comp)  # must not raise (None, not float('inf'))
    row = comp["table"][0]
    assert row["profit_factor"] is None
    assert row["calmar"] == 5.0 and row["var95"] == -0.02 and row["avg_exposure"] == 0.4


def test_comparison_empty_is_safe():
    comp = build_replay_comparison({})
    assert comp["table"] == [] and comp["best_pair"] is None and comp["pairs"] == []
