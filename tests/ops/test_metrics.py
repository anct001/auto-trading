"""Tests for src/ops/metrics.py — Prometheus text exposition from the dashboard payload."""
from __future__ import annotations

from src.ops.metrics import render_metrics

_D = {
    "equity": 10000.0, "day_return_pct": -1.5, "drawdown_pct": -2.0,
    "gross_exposure_pct": 12.5, "killswitch_engaged": True,
    "positions": [{"pair": "BTC/JPY"}],
    "performance": {"trade_count": 4, "win_rate": 0.5, "profit_factor": 2.0, "total_pnl": 60.0},
    "health": {"tick_count": 42},
}


def _vals(text):
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            k, v = line.split(" ", 1)
            out[k] = float(v)
    return out


def test_renders_prometheus_lines():
    text = render_metrics(_D)
    assert "# TYPE trading_equity gauge" in text
    v = _vals(text)
    assert v["trading_equity"] == 10000.0
    assert v["trading_day_return_pct"] == -1.5
    assert v["trading_open_positions"] == 1.0
    assert v["trading_killswitch_engaged"] == 1.0   # bool -> 1/0
    assert v["trading_tick_count"] == 42.0
    assert v["trading_win_rate"] == 0.5 and v["trading_profit_factor"] == 2.0


def test_profit_factor_omitted_when_undefined():
    text = render_metrics({**_D, "performance": {"trade_count": 2, "win_rate": 1.0,
                                                 "profit_factor": None, "total_pnl": 5.0}})
    assert "trading_profit_factor" not in _vals(text)  # None (∞) not representable -> omit


def test_empty_dashboard_is_safe():
    text = render_metrics({})
    assert "trading_equity" in _vals(text)  # defaults to 0, still valid exposition
