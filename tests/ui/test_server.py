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


def test_index_serves_self_contained_html():
    r = handle_request("GET", "/", None, _ctx())
    assert r.status == 200 and r.content_type.startswith("text/html")
    assert isinstance(r.body, str)
    assert "/api/dashboard" in r.body and "kill-switch" in r.body.lower()
    assert "://" not in r.body  # fully self-contained: no external CDN / remote resource


def test_index_rejects_write_verb():
    assert handle_request("POST", "/", {}, _ctx()).status == 405


def test_json_routes_keep_json_content_type():
    assert handle_request("GET", "/api/dashboard", None, _ctx()).content_type == "application/json"


def test_markets_api_returns_overview_and_heatmap():
    ctx = _ctx()
    ctx.markets = lambda: {"overview": [{"pair": "BTC/JPY", "change_pct": 1.2}], "heatmap": []}
    r = handle_request("GET", "/api/markets", None, ctx)
    assert r.status == 200 and r.body["overview"][0]["pair"] == "BTC/JPY"


def test_markets_api_empty_when_unconfigured():
    r = handle_request("GET", "/api/markets", None, _ctx())  # no markets provider
    assert r.status == 200 and r.body == {"overview": [], "heatmap": []}


def test_markets_page_served():
    r = handle_request("GET", "/markets", None, _ctx())
    assert r.status == 200 and r.content_type.startswith("text/html")
    assert "/api/markets" in r.body and "://" not in r.body


def test_demo_markets_provider_works():
    from src.ui.server import build_demo_context
    m = build_demo_context().markets()
    assert {"overview", "heatmap"} <= m.keys() and len(m["overview"]) == 3


def test_coin_api_uses_query_pair():
    ctx = _ctx()
    ctx.coin = lambda pair: {"pair": pair, "candles": [], "overlays": {}, "readouts": {}}
    r = handle_request("GET", "/api/coin?pair=BTC%2FJPY", None, ctx)
    assert r.status == 200 and r.body["pair"] == "BTC/JPY"


def test_coin_api_empty_when_unconfigured():
    r = handle_request("GET", "/api/coin?pair=X", None, _ctx())
    assert r.status == 200 and r.body == {}


def test_coin_page_served():
    r = handle_request("GET", "/coin", None, _ctx())
    assert r.status == 200 and r.content_type.startswith("text/html")
    assert "/api/coin" in r.body
    # only external reference allowed is the SVG namespace (no remote scripts/CDN)
    assert "http" not in r.body.replace("http://www.w3.org/2000/svg", "")


def test_demo_coin_provider_returns_candles():
    from src.ui.server import build_demo_context
    d = build_demo_context().coin("ETH/JPY")
    assert d["pair"] == "ETH/JPY" and len(d["candles"]) > 0
    assert len(d["overlays"]["ema_fast"]) == len(d["candles"])


def test_orders_api_and_page():
    ctx = _ctx()
    ctx.orders = lambda: {"attempts": [{"pair": "BTC/JPY", "approved": True}], "submitted": [], "fills": []}
    r = handle_request("GET", "/api/orders", None, ctx)
    assert r.status == 200 and r.body["attempts"][0]["pair"] == "BTC/JPY"
    empty = handle_request("GET", "/api/orders", None, _ctx())
    assert empty.body == {"attempts": [], "submitted": [], "fills": []}
    page = handle_request("GET", "/orders", None, _ctx())
    assert page.status == 200 and "/api/orders" in page.body


def test_demo_orders_provider():
    from src.ui.server import build_demo_context
    o = build_demo_context().orders()
    assert o["attempts"] and o["fills"] and o["submitted"][0]["client_order_id"] == "demo-1"


def test_order_place_disabled_by_default_is_404():
    # placing is a trading-path write; the read-only demo context leaves it unconfigured
    r = handle_request("POST", "/api/order", {"side": "buy", "qty": 1.0}, _ctx())
    assert r.status == 404


def test_order_place_routes_to_provider_when_enabled():
    ctx = _ctx()
    ctx.place = lambda body: {"placed": True, "filled": body["qty"], "reasons": []}
    r = handle_request("POST", "/api/order", {"side": "buy", "qty": 0.5}, ctx)
    assert r.status == 200 and r.body["placed"] is True and r.body["filled"] == 0.5


def test_order_place_rejects_get():
    ctx = _ctx()
    ctx.place = lambda body: {"placed": True}
    assert handle_request("GET", "/api/order", None, ctx).status == 405


def test_orderbook_api_uses_query_pair():
    ctx = _ctx()
    ctx.orderbook = lambda pair: {"pair": pair, "bids": [], "asks": [], "mid": None}
    r = handle_request("GET", "/api/orderbook?pair=ETH%2FJPY", None, ctx)
    assert r.status == 200 and r.body["pair"] == "ETH/JPY"
    assert handle_request("GET", "/api/orderbook?pair=X", None, _ctx()).body == {}


def test_demo_orderbook_provider_has_depth():
    from src.ui.server import build_demo_context
    b = build_demo_context().orderbook("BTC/JPY")
    assert b["best_bid"] < b["best_ask"] and b["mid"] > 0
    assert b["bids"][-1]["cum"] > b["bids"][0]["cum"]  # cumulative grows


def test_sse_frame_format():
    frame = dashboard_sse_frame({"equity": 1.0})
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert json.loads(frame[len("data: "):].strip())["equity"] == 1.0
