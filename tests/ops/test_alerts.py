"""Tests for src/ops/alerts.py — §12/§15 operator alerting (edge-triggered, pluggable sinks)."""
from __future__ import annotations

from src.ops.alerts import Alerter, evaluate_alerts

_BASE = {
    "killswitch_engaged": False, "day_return_pct": 0.0, "daily_soft_pct": -2.0,
    "daily_hard_pct": -4.0, "drawdown_pct": 0.0, "killswitch_pct": -12.0,
    "gross_exposure_pct": 10.0, "gross_cap_pct": 100.0, "health": {"last_error": None},
}


def _d(**over):
    return {**_BASE, **over}


def test_healthy_state_has_no_alerts():
    assert evaluate_alerts(_d()) == []


def test_each_breach_raises_its_alert():
    def codes(d):
        return {a.code for a in evaluate_alerts(d)}
    assert "kill_switch" in codes(_d(killswitch_engaged=True))
    assert "daily_hard_limit" in codes(_d(day_return_pct=-5.0))
    assert "daily_soft_limit" in codes(_d(day_return_pct=-3.0))
    assert "max_drawdown" in codes(_d(drawdown_pct=-13.0))
    assert "gross_exposure" in codes(_d(gross_exposure_pct=120.0))
    assert "loop_error" in codes(_d(health={"last_error": "ConnectionError"}))


def test_severity_levels():
    hard = [a for a in evaluate_alerts(_d(day_return_pct=-5.0)) if a.code == "daily_hard_limit"][0]
    soft = [a for a in evaluate_alerts(_d(day_return_pct=-3.0)) if a.code == "daily_soft_limit"][0]
    assert hard.level == "critical" and soft.level == "warn"


def test_alerter_is_edge_triggered_and_dispatches():
    sent = []
    a = Alerter(sinks=[sent.append])
    new = a.update(_d(killswitch_engaged=True))
    assert {x.code for x in new} == {"kill_switch"} and len(sent) == 1
    # same condition next tick → no new alert, no duplicate dispatch (no spam)
    assert a.update(_d(killswitch_engaged=True)) == []
    assert len(sent) == 1
    # condition clears then re-fires → alerts again
    assert a.update(_d(killswitch_engaged=False)) == []
    assert {x.code for x in a.update(_d(killswitch_engaged=True))} == {"kill_switch"}
    assert len(sent) == 2


def test_sink_exception_does_not_break_dispatch():
    def boom(_a):
        raise RuntimeError("sink down")
    a = Alerter(sinks=[boom])
    a.update(_d(killswitch_engaged=True))  # must not raise
