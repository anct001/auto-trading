"""Tests for src/ui/server.py — the operator HTTP API router (§12).

Pure request→response routing over the tested service layer, so it needs no sockets. Pins the
§12 contract: read-only by default; the only writes are the kill-switch and the risk-gated
manual order (here surfaced as a *preview* — placing routes through the engine elsewhere).
"""
from __future__ import annotations

import json

from src.risk.killswitch import KillSwitch
from src.ui.server import OperatorContext, dashboard_sse_frame, handle_request


def _ctx():
    ks = KillSwitch()
    return OperatorContext(
        dashboard=lambda: {"equity": 1_000_000.0, "killswitch_engaged": ks.is_halted},
        preview=lambda body: {"allowed": body.get("qty", 0) > 0, "reasons": []},
        killswitch=ks,
    )


def test_dashboard_is_readable():
    r = handle_request("GET", "/api/dashboard", None, _ctx())
    assert r.status == 200 and r.body["equity"] == 1_000_000.0


def test_preview_passes_body_through():
    r = handle_request("POST", "/api/preview", {"qty": 0.01}, _ctx())
    assert r.status == 200 and r.body["allowed"] is True


def test_killswitch_engage_then_rearm():
    ctx = _ctx()
    assert handle_request("POST", "/api/killswitch/engage", None, ctx).body["engaged"] is True
    r = handle_request("POST", "/api/killswitch/rearm", {"operator": "anct"}, ctx)
    assert r.status == 200 and r.body["engaged"] is False


def test_rearm_without_operator_is_400():
    ctx = _ctx()
    handle_request("POST", "/api/killswitch/engage", None, ctx)
    r = handle_request("POST", "/api/killswitch/rearm", {"operator": ""}, ctx)
    assert r.status == 400 and "error" in r.body


def test_unknown_path_is_404():
    assert handle_request("GET", "/api/nope", None, _ctx()).status == 404


def test_write_to_read_only_path_is_405():
    # the dashboard is read-only; a POST to it must be refused, not silently accepted
    assert handle_request("POST", "/api/dashboard", {}, _ctx()).status == 405


def test_demo_context_endpoints_work_end_to_end():
    # build_demo_context wires the REAL service layer (dashboard_payload / preview_payload)
    from src.ui.server import build_demo_context
    ctx = build_demo_context()
    dash = handle_request("GET", "/api/dashboard", None, ctx)
    assert dash.status == 200 and dash.body["equity"] == 1_000_000.0 and dash.body["positions"]
    prev = handle_request("POST", "/api/preview", {"side": "buy", "qty": 0.001}, ctx)
    assert prev.status == 200 and "allowed" in prev.body and prev.body["order"]["source"] == "manual"


def test_sse_frame_format():
    frame = dashboard_sse_frame({"equity": 1.0})
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert json.loads(frame[len("data: "):].strip())["equity"] == 1.0
