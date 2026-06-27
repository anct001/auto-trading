"""src/ui/server.py — operator HTTP API transport (§12), stdlib only.

A thin delivery layer over the tested service functions in `ui.api`. Kept dependency-free
(stdlib `http.server`) per the project's minimal-deps ethos; FastAPI/SSE frameworks remain
optional and unneeded for this surface.

§12 contract enforced by the router:
  - **read-only by default** — `GET /api/dashboard` is the only read;
  - the **only writes** are the human kill-switch (`/api/killswitch/{engage,rearm}`) and the
    manual-order **preview** (`/api/preview`, which runs the exact risk engine — Inv. 9; the
    actual place routes through the engine elsewhere). A write verb on a read-only path is 405.

`handle_request` is a pure function (method, path, body, ctx) → `Response`, so the whole routing
contract is unit-tested without sockets. `serve()` is the thin socket adapter (not unit-tested).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from src.risk.killswitch import KillSwitch
from src.ui.api import engage_kill, killswitch_status, rearm_kill


@dataclass
class OperatorContext:
    """Live providers the API reads from — injected so the router stays pure and testable."""

    dashboard: Callable[[], dict]          # () -> dashboard_payload(...)
    preview: Callable[[dict], dict]        # body -> preview_payload(...)
    killswitch: KillSwitch


@dataclass(frozen=True)
class Response:
    status: int
    body: dict | str
    content_type: str = "application/json"


def handle_request(method: str, path: str, body: dict | None, ctx: OperatorContext) -> Response:
    """Route one request. Pure: no I/O. See module docstring for the §12 contract."""
    path = path.split("?", 1)[0].rstrip("/") or "/"

    if path == "/":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, index_html(), content_type="text/html; charset=utf-8")

    if path == "/api/dashboard":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.dashboard())

    if path == "/api/preview":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        return Response(200, ctx.preview(body or {}))

    if path == "/api/killswitch/engage":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        return Response(200, engage_kill(ctx.killswitch))

    if path == "/api/killswitch/rearm":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        try:
            return Response(200, rearm_kill(ctx.killswitch, operator=(body or {}).get("operator", "")))
        except ValueError as e:
            return Response(400, {"error": str(e)})

    if path == "/api/killswitch":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, killswitch_status(ctx.killswitch))

    return Response(404, {"error": "not found"})


def dashboard_sse_frame(payload: dict) -> str:
    """Format one Server-Sent-Events frame for a dashboard snapshot."""
    return f"data: {json.dumps(payload)}\n\n"


def index_html() -> str:
    """The operator dashboard page — self-contained (no external CDN), polls /api/dashboard.

    Read-only view; the only write control is the kill-switch (engage / re-arm). Renders equity,
    today's P&L vs the soft/hard daily limits, drawdown vs the kill-switch, exposure, open
    positions, and the decision log — the §12 control dashboard over the JSON API.
    """
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Operator dashboard</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 12px} .grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
 .card{background:#171a21;border:1px solid #232833;border-radius:8px;padding:12px;min-width:150px}
 .lbl{color:#8b93a1;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
 .val{font-size:20px;margin-top:4px} .ok{color:#46d17f} .warn{color:#e6a23c} .bad{color:#f06a6a}
 table{border-collapse:collapse;width:100%;margin-top:6px} th,td{text-align:right;padding:4px 8px;border-bottom:1px solid #232833}
 th:first-child,td:first-child{text-align:left} button{background:#2a2f3a;color:#d7dbe0;border:1px solid #3a4151;border-radius:6px;padding:8px 14px;cursor:pointer}
 button.kill{background:#5a1f24;border-color:#7a2a30} #log div{font-family:monospace;font-size:12px;color:#9aa3b2;padding:2px 0}
 .muted{color:#6b7280;font-size:12px}
</style></head><body>
<h1>Operator dashboard <span id="ks" class="muted"></span></h1>
<div class="grid" id="cards"></div>
<div class="card" style="min-width:100%"><div class="lbl">Open positions</div><table id="pos"><thead>
<tr><th>Pair</th><th>Qty</th><th>Value</th><th>Unrealized</th><th>Exposure %</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="min-width:100%"><div class="lbl">Decision log</div><div id="log"></div></div>
<div style="margin-top:12px"><button class="kill" onclick="engage()">Engage kill-switch</button>
<button onclick="rearm()">Re-arm</button> <span id="msg" class="muted"></span></div>
<script>
const fmt=(n)=>typeof n==="number"?n.toLocaleString(undefined,{maximumFractionDigits:4}):n;
function cls(v,soft,hard){if(v<=hard)return"bad";if(v<=soft)return"warn";return"ok";}
async function refresh(){
 try{const d=await (await fetch("/api/dashboard")).json();
  const cards=[
   ["Equity",fmt(d.equity),""],
   ["Day P&L %",fmt(d.day_return_pct),cls(d.day_return_pct,d.daily_soft_pct,d.daily_hard_pct)],
   ["Drawdown %",fmt(d.drawdown_pct),cls(d.drawdown_pct,d.killswitch_pct/2,d.killswitch_pct)],
   ["Gross exp %",fmt(d.gross_exposure_pct),d.gross_exposure_pct>d.gross_cap_pct?"bad":"ok"],
   ["Sentiment fresh",String(d.sentiment_fresh),""]];
  document.getElementById("cards").innerHTML=cards.map(c=>
   `<div class="card"><div class="lbl">${c[0]}</div><div class="val ${c[2]}">${c[1]}</div></div>`).join("");
  const ks=d.killswitch_engaged?`<span class="bad">KILLED (${d.killswitch_reason||""})</span>`:`<span class="ok">armed</span>`;
  document.getElementById("ks").innerHTML="· "+ks;
  document.querySelector("#pos tbody").innerHTML=(d.positions||[]).map(p=>
   `<tr><td>${p.pair}</td><td>${fmt(p.qty)}</td><td>${fmt(p.value)}</td><td class="${p.unrealized_pnl>=0?'ok':'bad'}">${fmt(p.unrealized_pnl)}</td><td>${fmt(p.exposure_pct)}</td></tr>`).join("")||"<tr><td class=muted>flat</td></tr>";
  document.getElementById("log").innerHTML=(d.decision_log||[]).map(e=>
   `<div>${e.timestamp} <b>${e.type}</b> ${e.pair} ${e.detail}</div>`).join("")||"<div class=muted>no events</div>";
 }catch(e){document.getElementById("msg").textContent="fetch error: "+e;}
}
async function engage(){await fetch("/api/killswitch/engage",{method:"POST"});refresh();}
async function rearm(){const op=prompt("Operator identity (human re-enable):");if(!op)return;
 const r=await fetch("/api/killswitch/rearm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operator:op})});
 document.getElementById("msg").textContent=r.ok?"re-armed":"re-arm rejected";refresh();}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def serve(ctx: OperatorContext, *, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Thin stdlib HTTP adapter around handle_request (not unit-tested — sockets).

    Read-only operator surface on localhost. Bodies are JSON. The kill-switch and preview are the
    only writes (§12). Intended for the solo operator's own machine, not public exposure.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = None
            resp = handle_request(method, self.path, body, ctx)
            payload = resp.body if isinstance(resp.body, str) else json.dumps(resp.body)
            out = payload.encode("utf-8")
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self) -> None:   # noqa: N802 (stdlib API)
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, *args) -> None:  # silence default stderr logging
            pass

    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def build_demo_context() -> OperatorContext:
    """A self-contained OperatorContext over a fixed paper snapshot — for `python -m src.ui.server`.

    Read-only demo: the dashboard/preview reflect a static paper portfolio so the operator can see
    the surface working without a live loop. The real wiring (live state from the dry-run) is a
    follow-up; nothing here touches real capital or a real key.
    """
    from src.risk import engine
    from src.risk.config import RiskConfig
    from src.risk.sizing import MarketConstraints
    from src.risk.types import PortfolioState, Position
    from src.ui.api import dashboard_payload, preview_payload

    pair = "BTC/JPY"
    price = 10_000_000.0
    cfg = RiskConfig.load("config/risk/default.json")
    ks = KillSwitch()
    state = PortfolioState(equity=1_000_000.0, peak_equity=1_050_000.0, day_start_equity=1_010_000.0,
                           quote_price=1.0, positions={pair: Position(pair, 0.02, 9_500_000.0)})
    market = MarketConstraints(min_notional=500.0, lot_step=1e-6, tick_size=1.0)
    good = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
            "futures_disabled": True, "reduce_only_on_exit": True}

    def _ctx_obj():
        return engine.RiskContext(prices={pair: price}, exchange_state=dict(good),
                                  killswitch=ks, market=market)

    return OperatorContext(
        dashboard=lambda: dashboard_payload(state=state, cfg=cfg, prices={pair: price}, killswitch=ks),
        preview=lambda body: preview_payload(
            pair=body.get("pair", pair), side=body.get("side", "buy"),
            qty=float(body.get("qty", 0.0) or 0.0), price=float(body.get("price", price) or price),
            state=state, cfg=cfg, ctx=_ctx_obj()),
        killswitch=ks,
    )


def main() -> None:
    from src.core.console import force_utf8_stdio
    force_utf8_stdio()
    print("[ui] operator API on http://127.0.0.1:8787  (GET /api/dashboard, POST /api/preview, "
          "POST /api/killswitch/engage|rearm) — read-only demo, paper only")
    serve(build_demo_context())


if __name__ == "__main__":
    main()
