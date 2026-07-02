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


def test_sortino_and_mc_drawdown_are_computed_and_deterministic():
    eqh = _eq(100, 105, 98, 103, 96, 110, 108, 115)
    r1 = summarize_result("X", trades=[], equity_history=eqh, start_equity=100.0)
    r2 = summarize_result("X", trades=[], equity_history=eqh, start_equity=100.0)
    assert r1.sortino != 0.0                          # a downside exists → defined, non-zero
    assert r1.mc_dd_p99 <= r1.mc_dd_p95 <= 0.0        # p99 is the deeper (worse) tail
    # bootstrap is seeded → reproducible (§8)
    assert (r1.sortino, r1.mc_dd_p95, r1.mc_dd_p99) == (r2.sortino, r2.mc_dd_p95, r2.mc_dd_p99)


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


def test_comparison_strategy_dimension_labels_rows_by_strategy():
    results = {
        "ema_12_26": summarize_result("ema_12_26", trades=[{"return": 0.02, "pnl": 200.0}],
                                      equity_history=_eq(10000, 10200), start_equity=10000.0),
        "rsi_14": summarize_result("rsi_14", trades=[{"return": -0.01, "pnl": -100.0}],
                                   equity_history=_eq(10000, 9900), start_equity=10000.0),
    }
    comp = build_replay_comparison(results, dimension="strategy", coin="BTC/USDT")
    assert comp["dimension"] == "strategy" and comp["label"] == "Strategy"
    assert comp["coin"] == "BTC/USDT"
    assert comp["best_pair"] == "ema_12_26"           # row key holds the strategy name
    assert [r["pair"] for r in comp["table"]] == ["ema_12_26", "rsi_14"]


def test_comparison_default_dimension_is_pair():
    comp = build_replay_comparison({
        "BTC/USDT": summarize_result("BTC/USDT", trades=[], equity_history=_eq(10000, 10100),
                                     start_equity=10000.0)})
    assert comp["dimension"] == "pair" and comp["label"] == "Pair" and comp["coin"] is None


def test_comparison_is_json_safe_infinite_profit_factor_stays_none():
    import json
    r = ReplayResult(
        pair="Z", start_equity=10000.0, final_equity=10500.0, total_return=0.05,
        max_drawdown=-0.01, sharpe=0.5, sortino=0.7, calmar=5.0, var95=-0.02, cvar95=-0.03,
        mc_dd_p95=-0.05, mc_dd_p99=-0.08, avg_exposure=0.4, time_in_market=0.6,
        performance={"trade_count": 1, "profit_factor": None, "win_rate": 1.0, "total_pnl": 500.0})
    comp = build_replay_comparison({"Z": r})
    json.dumps(comp)  # must not raise (None, not float('inf'))
    row = comp["table"][0]
    assert row["profit_factor"] is None
    assert row["calmar"] == 5.0 and row["var95"] == -0.02 and row["avg_exposure"] == 0.4
    assert row["sortino"] == 0.7 and row["mc_dd_p95"] == -0.05 and row["mc_dd_p99"] == -0.08


def test_comparison_empty_is_safe():
    comp = build_replay_comparison({})
    assert comp["table"] == [] and comp["best_pair"] is None and comp["pairs"] == []
