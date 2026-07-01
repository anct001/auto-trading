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
from urllib.parse import parse_qs, urlsplit

from src.risk.killswitch import KillSwitch
from src.ui.api import engage_kill, killswitch_status, rearm_kill


@dataclass
class OperatorContext:
    """Live providers the API reads from — injected so the router stays pure and testable."""

    dashboard: Callable[[], dict]          # () -> dashboard_payload(...)
    preview: Callable[[dict], dict]        # body -> preview_payload(...)
    killswitch: KillSwitch
    markets: Callable[[], dict] | None = None  # () -> {"overview": [...], "heatmap": [...]}
    coin: Callable[..., dict] | None = None    # (pair, tf) -> coin_detail payload
    orders: Callable[[], dict] | None = None   # () -> {"attempts", "submitted", "fills"}
    place: Callable[[dict], dict] | None = None  # body -> place a manual order (Inv 9); None = disabled
    orderbook: Callable[[str], dict] | None = None  # pair -> order-book depth view
    agentview: Callable[[str], dict] | None = None  # pair -> agent-view overlay (signal/regime/sentiment)
    trades_tape: Callable[[str], dict] | None = None  # pair -> recent public market trades (tape)
    chat: Callable[[dict], dict] | None = None  # body{question,provider?} -> {answer,error} READ-ONLY (Inv 1)
    chat_providers: Callable[[], dict] | None = None  # () -> {default, providers:[{name,model,default}]}
    replay: Callable[[], dict] | None = None  # () -> multi-pair replay comparison model; None = no replay view
    auth_token: str | None = None  # if set, mutating writes require a matching X-Auth-Token; None = open (localhost)


@dataclass(frozen=True)
class Response:
    status: int
    body: dict | str
    content_type: str = "application/json"


# ---- shared page chrome: one nav bar + favicon across every operator page (§12 polish) --------

_NAV_LINKS = [("/", "Dashboard"), ("/markets", "Markets"), ("/coin", "Coin"),
              ("/orders", "Orders"), ("/replay", "Replay"), ("/terminal", "Terminal"),
              ("/pro", "Pro"), ("/chat", "Assistant"), ("/help", "Help")]

_NAV_CSS = """
 .topnav{display:flex;align-items:center;gap:13px;background:#12151b;border:1px solid #1f2530;
  border-radius:8px;padding:8px 14px;margin:0 0 14px;font-size:13px;flex-wrap:wrap}
 .topnav .brand{font-weight:600;color:#e8ecf2;margin-right:4px;letter-spacing:.3px}
 .topnav .tag{background:#274d33;color:#7fe0a1;font-size:10px;padding:2px 7px;border-radius:9px;
  margin-left:6px;letter-spacing:.6px}
 .topnav a{color:#8b93a1;text-decoration:none;padding:3px 1px;border-bottom:2px solid transparent}
 .topnav a:hover{color:#d7dbe0} .topnav a.active{color:#6ea8fe;border-bottom-color:#6ea8fe}
 body.light .topnav{background:#fff;border-color:#d9dee5} body.light .topnav .brand{color:#222}
 body.light .topnav a{color:#667} body.light .topnav a:hover{color:#123}"""

# inline SVG favicon (no file, no CDN): a green candle glyph on the dark card colour
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#12151b"/>'
    '<rect x="7" y="12" width="5" height="11" rx="1" fill="#f06a6a"/>'
    '<rect x="9" y="8" width="1.6" height="19" fill="#f06a6a"/>'
    '<rect x="19" y="7" width="5" height="12" rx="1" fill="#46d17f"/>'
    '<rect x="21" y="4" width="1.6" height="20" fill="#46d17f"/></svg>')


def _nav(active: str = "") -> str:
    parts = []
    for p, label in _NAV_LINKS:
        cls = ' class="active"' if p == active else ""
        parts.append(f'<a href="{p}"{cls}>{label}</a>')
    return ('<nav class="topnav"><span class="brand">📈 AutoTrader'
            '<span class="tag">PAPER</span></span>' + "".join(parts) + "</nav>")


def _with_nav(html: str, active: str = "") -> str:
    """Inject the shared top-nav + its CSS into a page template (idempotent per page)."""
    return (html.replace("</style>", _NAV_CSS + "\n</style>", 1)
                .replace("<body>", "<body>" + _nav(active), 1))


# mutating write endpoints — these can place an order or halt/resume trading, so when an auth
# token is configured they must present it. Read endpoints (GET) and the read-only POSTs
# (/api/preview, /api/chat) stay open under the localhost assumption; the token exists to stop a
# non-operator from reaching the *dangerous* writes if the surface is ever exposed off localhost.
_PROTECTED_WRITES = frozenset({
    "/api/order", "/api/killswitch/engage", "/api/killswitch/rearm",
})


def _auth_ok(path: str, ctx: OperatorContext, headers: dict | None) -> bool:
    """True if the request may proceed: no token configured, or a matching X-Auth-Token present."""
    if not ctx.auth_token or path not in _PROTECTED_WRITES:
        return True
    supplied = ""
    if headers:
        # HTTP headers are case-insensitive; check the common spellings
        supplied = (headers.get("X-Auth-Token") or headers.get("x-auth-token") or "")
    return _consteq(str(supplied), ctx.auth_token)


def _consteq(a: str, b: str) -> bool:
    """Constant-time-ish string compare (avoid leaking token length/prefix via timing)."""
    import hmac
    return hmac.compare_digest(a, b)


def handle_request(method: str, path: str, body: dict | None, ctx: OperatorContext,
                   headers: dict | None = None) -> Response:
    """Route one request. Pure: no I/O. See module docstring for the §12 contract."""
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    path = parts.path.rstrip("/") or "/"

    if not _auth_ok(path, ctx, headers):
        return Response(401, {"error": "missing or invalid auth token"})

    if path == "/favicon.ico":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _FAVICON_SVG, content_type="image/svg+xml")

    if path == "/help":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(help_html(), "/help"), content_type="text/html; charset=utf-8")

    if path == "/":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(index_html(), "/"), content_type="text/html; charset=utf-8")

    if path == "/coin":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(coin_detail_html(), "/coin"), content_type="text/html; charset=utf-8")

    if path == "/api/coin":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        pair = (query.get("pair") or [""])[0]
        tf = (query.get("tf") or [""])[0]
        return Response(200, ctx.coin(pair, tf) if ctx.coin else {})

    if path == "/api/trades":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        pair = (query.get("pair") or [""])[0]
        return Response(200, ctx.trades_tape(pair) if ctx.trades_tape else {"trades": []})

    if path == "/api/orderbook":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        pair = (query.get("pair") or [""])[0]
        return Response(200, ctx.orderbook(pair) if ctx.orderbook else {})

    if path == "/api/agentview":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        pair = (query.get("pair") or [""])[0]
        return Response(200, ctx.agentview(pair) if ctx.agentview else {})

    if path == "/orders":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(orders_html(), "/orders"), content_type="text/html; charset=utf-8")

    if path == "/api/orders":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        empty = {"attempts": [], "submitted": [], "fills": []}
        return Response(200, ctx.orders() if ctx.orders else empty)

    if path == "/terminal":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, terminal_html(), content_type="text/html; charset=utf-8")

    if path == "/pro":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, pro_html(), content_type="text/html; charset=utf-8")

    if path == "/markets":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(markets_html(), "/markets"), content_type="text/html; charset=utf-8")

    if path == "/api/markets":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.markets() if ctx.markets else {"overview": [], "heatmap": []})

    if path == "/api/dashboard":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.dashboard())

    if path == "/metrics":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        from src.ops.metrics import render_metrics
        return Response(200, render_metrics(ctx.dashboard()),
                        content_type="text/plain; version=0.0.4; charset=utf-8")

    if path == "/api/preview":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        try:
            return Response(200, ctx.preview(body or {}))
        except (ValueError, TypeError) as e:
            return Response(400, {"error": f"invalid request body: {e}"})

    if path == "/api/order":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        if ctx.place is None:
            return Response(404, {"error": "manual order placing not enabled"})
        try:
            return Response(200, ctx.place(body or {}))
        except (ValueError, TypeError) as e:
            return Response(400, {"error": f"invalid request body: {e}"})

    if path == "/replay":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(replay_html(), "/replay"), content_type="text/html; charset=utf-8")

    if path == "/api/replay":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.replay() if ctx.replay else {"table": [], "pairs": []})

    if path == "/chat":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, _with_nav(chat_html(), "/chat"), content_type="text/html; charset=utf-8")

    if path == "/api/chat/providers":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.chat_providers() if ctx.chat_providers
                        else {"default": "ollama", "providers": []})

    if path == "/api/chat":
        # POST carries the operator's question, but the assistant is strictly READ-ONLY (Inv 1):
        # it explains state and cannot place orders or change anything. It reads no UI write path.
        if method != "POST":
            return Response(405, {"error": "use POST"})
        if ctx.chat is None:
            return Response(404, {"error": "chat assistant not enabled"})
        try:
            return Response(200, ctx.chat(body or {}))
        except (ValueError, TypeError) as e:
            return Response(400, {"error": f"invalid request body: {e}"})

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
 h1{font-size:16px;margin:0 0 12px} a{color:#6ea8fe} .grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
 .card{background:#171a21;border:1px solid #232833;border-radius:8px;padding:12px;min-width:150px}
 .lbl{color:#8b93a1;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
 .val{font-size:20px;margin-top:4px} .ok{color:#46d17f} .warn{color:#e6a23c} .bad{color:#f06a6a}
 table{border-collapse:collapse;width:100%;margin-top:6px} th,td{text-align:right;padding:4px 8px;border-bottom:1px solid #232833}
 th:first-child,td:first-child{text-align:left} button{background:#2a2f3a;color:#d7dbe0;border:1px solid #3a4151;border-radius:6px;padding:8px 14px;cursor:pointer}
 button.kill{background:#5a1f24;border-color:#7a2a30} #log div{font-family:monospace;font-size:12px;color:#9aa3b2;padding:2px 0}
 .muted{color:#6b7280;font-size:12px}
 .bar{height:6px;background:#232833;border-radius:3px;margin-top:8px;overflow:hidden}
 .barfill{height:100%;border-radius:3px;transition:width .4s}
 svg#eq{background:#171a21;border:1px solid #232833;border-radius:8px;width:100%;height:130px}
 body.light{background:#f5f6f8;color:#1c2230}
 body.light .card,body.light svg#eq{background:#fff;border-color:#dfe3ea}
 body.light .lbl{color:#6b7280} body.light .muted{color:#9aa3b2}
 body.light th,body.light td{border-color:#e5e8ee} body.light .bar{background:#e5e8ee}
 body.light button{background:#eceef2;color:#1c2230;border-color:#cdd3dd}
</style></head><body>
<h1>Operator dashboard <span id="ks" class="muted"></span></h1>
<div class="grid" id="cards"></div>
<div class="card" style="min-width:100%"><div class="lbl">Equity curve</div><svg id="eq" viewBox="0 0 900 130" preserveAspectRatio="none"></svg></div>
<div class="grid" id="perf"></div>
<div class="card" style="min-width:100%"><div class="lbl">Open positions</div><table id="pos"><thead>
<tr><th>Pair</th><th>Qty</th><th>Value</th><th>Unrealized</th><th>Exposure %</th><th></th></tr></thead><tbody></tbody></table></div>
<div class="card" style="min-width:100%"><div class="lbl">Closed trades</div><table id="trades"><thead>
<tr><th>Exit time</th><th>Pair</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Return %</th><th>P&L</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="min-width:100%"><div class="lbl">Decision log</div><div id="log"></div></div>
<div style="margin-top:12px"><button class="kill" onclick="engage()">Engage kill-switch</button>
<button onclick="rearm()">Re-arm</button> <button onclick="toggleTheme()">Theme</button>
<span id="msg" class="muted"></span></div>
<script>
window.fetch=((of)=>async(u,o)=>{o=o||{};if((o.method||"GET").toUpperCase()==="POST"){o.headers=Object.assign({},o.headers,{"X-Auth-Token":localStorage.getItem("uitoken")||""});}let r=await of(u,o);if(r.status===401){const t=prompt("Operator auth token for writes:");if(t){localStorage.setItem("uitoken",t);o.headers=Object.assign({},o.headers,{"X-Auth-Token":t});r=await of(u,o);}}return r;})(window.fetch);
const COL={ok:"#46d17f",warn:"#e6a23c",bad:"#f06a6a"};
const fmt=(n)=>typeof n==="number"?n.toLocaleString(undefined,{maximumFractionDigits:4}):n;
function cls(v,soft,hard){if(v<=hard)return"bad";if(v<=soft)return"warn";return"ok";}
function gauge(pct,c){pct=Math.max(0,Math.min(100,pct));
 return `<div class="bar"><div class="barfill" style="width:${pct}%;background:${COL[c]}"></div></div>`;}
function drawEquity(curve){
 const svg=document.getElementById("eq");svg.innerHTML="";const W=900,H=130,pad=8;
 if(!curve||curve.length<2){svg.innerHTML='<text x=12 y=24 fill="#6b7280">accumulating…</text>';return;}
 const ys=curve.map(p=>p.equity);const lo=Math.min(...ys),hi=Math.max(...ys);
 const x=i=>pad+i*(W-2*pad)/(curve.length-1),y=v=>H-pad-(v-lo)/((hi-lo)||1)*(H-2*pad);
 const up=ys[ys.length-1]>=ys[0],col=up?COL.ok:COL.bad;
 const pts=curve.map((p,i)=>x(i)+","+y(p.equity)).join(" ");
 const pl=document.createElementNS("http://www.w3.org/2000/svg","polyline");
 pl.setAttribute("points",pts);pl.setAttribute("fill","none");pl.setAttribute("stroke",col);pl.setAttribute("stroke-width","1.5");
 svg.appendChild(pl);}
function render(d){
  const dpl=cls(d.day_return_pct,d.daily_soft_pct,d.daily_hard_pct);
  const ddc=cls(d.drawdown_pct,d.killswitch_pct/2,d.killswitch_pct);
  const grc=d.gross_exposure_pct>d.gross_cap_pct?"bad":(d.gross_exposure_pct>d.gross_cap_pct*0.8?"warn":"ok");
  // value colour is sign-aware (a loss never shows green); the gauge bar keeps limit-distance colour
  const dplv=d.day_return_pct>=0?"ok":(dpl==="ok"?"":dpl);
  const sent=d.sentiment_fresh==null?"off":(d.sentiment_fresh?"fresh":"stale");
  const cards=[
   {l:"Equity",v:fmt(d.equity),c:""},
   {l:"Day P&L %",v:fmt(d.day_return_pct),c:dplv,g:gauge(Math.abs(Math.min(0,d.day_return_pct))/Math.abs(d.daily_hard_pct)*100,dpl)},
   {l:"Drawdown %",v:fmt(d.drawdown_pct),c:ddc,g:gauge(Math.abs(d.drawdown_pct)/Math.abs(d.killswitch_pct)*100,ddc)},
   {l:"Gross exp %",v:fmt(d.gross_exposure_pct),c:grc,g:gauge(d.gross_exposure_pct/d.gross_cap_pct*100,grc)},
   {l:"Sentiment (P1)",v:sent,c:sent==="stale"?"warn":""}];
  document.getElementById("cards").innerHTML=cards.map(c=>
   `<div class="card"><div class="lbl">${c.l}</div><div class="val ${c.c}">${c.v}</div>${c.g||""}</div>`).join("");
  drawEquity(d.equity_curve);
  const ks=d.killswitch_engaged?`<span class="bad">KILLED (${d.killswitch_reason||""})</span>`:`<span class="ok">armed</span>`;
  const h=d.health||{};const htxt=h.last_tick_at?`· ticks ${h.tick_count} · last ${h.last_tick_at.slice(11,19)}Z`:"";
  const herr=h.last_error?` · <span class="warn">⚠ ${h.last_error}</span>`:"";
  document.getElementById("ks").innerHTML="· "+ks+" "+htxt+herr;
  const pf=d.performance||{};
  const pfCell=!pf.trade_count?"–":(pf.profit_factor==null?"∞":pf.profit_factor.toFixed(2));
  const perf=[["Trades",pf.trade_count??0,""],
   ["Win rate %",pf.trade_count?(pf.win_rate*100).toFixed(1):"–",(pf.win_rate>=0.5?"ok":"warn")],
   ["Profit factor",pfCell,(pf.profit_factor==null||pf.profit_factor>=1?"ok":"bad")],
   ["Expectancy %",pf.trade_count?(pf.expectancy*100).toFixed(3):"–",(pf.expectancy>=0?"ok":"bad")],
   ["Realized P&L",fmt(pf.total_pnl||0),(pf.total_pnl>=0?"ok":"bad")]];
  document.getElementById("perf").innerHTML=perf.map(c=>
   `<div class="card"><div class="lbl">${c[0]}</div><div class="val ${c[2]}">${c[1]}</div></div>`).join("");
  document.querySelector("#trades tbody").innerHTML=(d.trades||[]).map(t=>
   `<tr><td>${(t.exit_time||"").slice(0,19).replace("T"," ")}</td><td>${t.pair}</td><td>${fmt(t.qty)}</td><td>${fmt(t.entry_price)}</td><td>${fmt(t.exit_price)}</td><td class="${t.return>=0?'ok':'bad'}">${(t.return*100).toFixed(3)}</td><td class="${t.pnl>=0?'ok':'bad'}">${fmt(t.pnl)}</td></tr>`).join("")||"<tr><td class=muted>none yet</td></tr>";
  document.querySelector("#pos tbody").innerHTML=(d.positions||[]).map(p=>
   `<tr><td>${p.pair}</td><td>${fmt(p.qty)}</td><td>${fmt(p.value)}</td><td class="${p.unrealized_pnl>=0?'ok':'bad'}">${fmt(p.unrealized_pnl)}</td><td>${fmt(p.exposure_pct)}</td><td><button onclick="flatten('${p.pair}',${p.qty},${p.price})">Flatten</button></td></tr>`).join("")||"<tr><td class=muted>flat</td></tr>";
  document.getElementById("log").innerHTML=(d.decision_log||[]).map(e=>{
   const qy=encodeURIComponent(`Explain this decision: ${e.timestamp} ${e.type} ${e.pair} ${e.detail||""}`);
   return `<div>${e.timestamp} <b>${e.type}</b> ${e.pair} ${e.detail} <a href="/chat?q=${qy}" title="Ask the assistant to explain">explain</a></div>`;
  }).join("")||"<div class=muted>no events</div>";
}
async function poll(){try{render(await (await fetch("/api/dashboard")).json());}
 catch(e){document.getElementById("msg").textContent="fetch error: "+e;}}
async function flatten(pair,qty,price){if(!confirm("Flatten "+pair+" ("+qty+")?"))return;
 const r=await fetch("/api/order",{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify({side:"sell",pair:pair,qty:qty,price:price})});const j=await r.json();
 document.getElementById("msg").textContent=j.placed?("flattened "+pair):("flatten failed: "+((j.reasons||[]).join("; ")||j.error||""));poll();}
function toggleTheme(){document.body.classList.toggle("light");
 localStorage.setItem("uitheme",document.body.classList.contains("light")?"light":"dark");}
async function engage(){await fetch("/api/killswitch/engage",{method:"POST"});poll();}
async function rearm(){const op=prompt("Operator identity (human re-enable):");if(!op)return;
 const r=await fetch("/api/killswitch/rearm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operator:op})});
 document.getElementById("msg").textContent=r.ok?"re-armed":"re-arm rejected";poll();}
if(localStorage.getItem("uitheme")==="light")document.body.classList.add("light");
poll();setInterval(poll,5000);  // poll fallback (cheap)
try{const es=new EventSource("/api/stream");es.onmessage=e=>{try{render(JSON.parse(e.data));}catch(_){}};}catch(_){}
</script></body></html>"""


def terminal_html() -> str:
    """Dense single-screen operator terminal (§12) — tiles every read surface in a grid.

    Inspired by multi-widget trading terminals: KPI strip + watchlist + heatmap + candles-with-
    volume + order-book ladder & depth + positions + agent-view, all over the existing JSON APIs
    (no new backend). Self-contained, no CDN; read-only except the kill-switch + flatten (Inv 9).
    """
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Terminal</title>
<style>
 *{box-sizing:border-box} body{font:12px ui-monospace,Menlo,Consolas,monospace;background:#0b0e14;color:#cdd3de;margin:0;padding:8px}
 a{color:#6ea8fe;text-decoration:none} .ok{color:#3fd07f} .bad{color:#f06a6a} .warn{color:#e6a23c} .mut{color:#5f6b7a}
 .grid{display:grid;gap:8px;grid-template-columns:repeat(12,1fr);grid-auto-rows:minmax(40px,auto)}
 .tile{background:#11151c;border:1px solid #1e2530;border-radius:6px;padding:8px;overflow:auto}
 .t{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#7a8699;margin-bottom:6px}
 table{border-collapse:collapse;width:100%} td,th{padding:2px 6px;text-align:right;white-space:nowrap}
 td:first-child,th:first-child{text-align:left} tr:hover{background:#161b24}
 .kpi{display:flex;flex-direction:column} .kpi b{font-size:18px} svg{display:block;width:100%}
 button{background:#222a36;color:#cdd3de;border:1px solid #313b4a;border-radius:4px;padding:3px 8px;cursor:pointer;font:inherit}
 #heat{display:flex;flex-wrap:wrap;gap:3px} .hx{flex:1 1 60px;min-height:46px;border-radius:4px;padding:4px;color:#0b0e14;font-weight:700}
 input{background:#0b0e14;color:#cdd3de;border:1px solid #313b4a;border-radius:4px;padding:3px;font:inherit;width:90px}
</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
 <b>OPERATOR TERMINAL</b> <span id="ks" class="mut"></span>
 <span>pair <input id="pair" value="BTC/JPY"><button onclick="setPair()">go</button>
 · <a href="/">classic</a> <a href="/markets">markets</a> <a href="/orders">orders</a>
 <button class="bad" onclick="engage()">KILL</button></span></div>
<div class="t mut" style="margin-bottom:4px">tiles are draggable — drag to rearrange (order saved)</div>
<div class="grid" id="grid">
 <div class="tile" data-tid="kpis" style="grid-column:span 12" id="kpis"></div>
 <div class="tile" data-tid="watch" style="grid-column:span 3" id="watch"><div class="t">⠿ Watchlist</div><table><tbody></tbody></table></div>
 <div class="tile" data-tid="chart" style="grid-column:span 6"><div class="t">⠿ Chart · <span id="cpair"></span> <span id="tfbtns"></span></div><svg id="chart" viewBox="0 0 600 300"></svg></div>
 <div class="tile" data-tid="book" style="grid-column:span 3"><div class="t">⠿ Order book <span id="spread" class="mut"></span></div><svg id="depth" viewBox="0 0 300 120"></svg><table id="ob"><tbody></tbody></table></div>
 <div class="tile" data-tid="tape" style="grid-column:span 3"><div class="t">⠿ Trades (tape)</div><table id="tape"><tbody></tbody></table></div>
 <div class="tile" data-tid="heat" style="grid-column:span 3"><div class="t">⠿ Heatmap (24h)</div><div id="heat"></div></div>
 <div class="tile" data-tid="pos" style="grid-column:span 3"><div class="t">⠿ Positions</div><table id="pos"><tbody></tbody></table></div>
 <div class="tile" data-tid="agent" style="grid-column:span 3"><div class="t">⠿ Agent view</div><div id="agent"></div><div class="t" style="margin-top:8px">Decision log</div><div id="log" style="font-size:11px"></div></div>
</div>
<script>
window.fetch=((of)=>async(u,o)=>{o=o||{};if((o.method||"GET").toUpperCase()==="POST"){o.headers=Object.assign({},o.headers,{"X-Auth-Token":localStorage.getItem("uitoken")||""});}let r=await of(u,o);if(r.status===401){const t=prompt("Operator auth token for writes:");if(t){localStorage.setItem("uitoken",t);o.headers=Object.assign({},o.headers,{"X-Auth-Token":t});r=await of(u,o);}}return r;})(window.fetch);
const NS="http://www.w3.org/2000/svg",C={ok:"#3fd07f",bad:"#f06a6a",warn:"#e6a23c"};
let PAIR=new URLSearchParams(location.search).get("pair")||"BTC/JPY";
document.getElementById("pair").value=PAIR;
const f=(n,d=2)=>n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:d});
const j=async u=>{try{return await (await fetch(u)).json();}catch(e){return null;}};
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function setPair(){PAIR=document.getElementById("pair").value.toUpperCase();history.replaceState(0,"","?pair="+encodeURIComponent(PAIR));renderCoin();renderTape();}
async function renderTape(){const d=await j("/api/trades?pair="+encodeURIComponent(PAIR));const rows=(d&&d.trades)||[];
 document.querySelector("#tape tbody").innerHTML=rows.map(t=>
  `<tr><td class=mut>${t.time}</td><td class="${t.side==='sell'?'bad':'ok'}">${f(t.price)}</td><td>${f(t.amount,4)}</td></tr>`).join("")||'<tr><td class=mut>no trades</td></tr>';}
async function engage(){await fetch("/api/killswitch/engage",{method:"POST"});renderDash();}
async function flatten(p,q,pr){if(!confirm("Flatten "+p+"?"))return;await fetch("/api/order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({side:"sell",pair:p,qty:q,price:pr})});renderDash();}
function renderDash(d){ if(!d){return j("/api/dashboard").then(renderDash);}
 const grc=d.gross_exposure_pct>d.gross_cap_pct?"bad":"ok";
 const pf=d.performance||{};const h=d.health||{};
 const k=[["Equity",f(d.equity),""],["Day P&L %",f(d.day_return_pct,3),d.day_return_pct>=0?"ok":"bad"],
  ["Drawdown %",f(d.drawdown_pct,3),d.drawdown_pct<=d.killswitch_pct/2?"bad":"warn"],
  ["Gross %",f(d.gross_exposure_pct,2),grc],["Win %",pf.trade_count?f(pf.win_rate*100,1):"—",""],
  ["Prof.factor",pf.trade_count?(pf.profit_factor==null?"∞":f(pf.profit_factor)):"—",""],
  ["Realized P&L",f(pf.total_pnl||0),pf.total_pnl>=0?"ok":"bad"],["Trades",pf.trade_count??0,""]];
 document.getElementById("kpis").innerHTML='<div style="display:flex;gap:18px;flex-wrap:wrap">'+
  k.map(x=>`<div class="kpi"><span class="t">${x[0]}</span><b class="${x[2]}">${x[1]}</b></div>`).join("")+'</div>';
 document.getElementById("ks").innerHTML=(d.killswitch_engaged?'<span class="bad">KILLED</span>':'<span class="ok">armed</span>')+
  (h.last_tick_at?` · ticks ${h.tick_count} · ${h.last_tick_at.slice(11,19)}Z`:"")+
  (h.last_error?` · <span class="warn">⚠ ${h.last_error}</span>`:"");
 document.querySelector("#pos tbody").innerHTML=(d.positions||[]).map(p=>
  `<tr><td>${p.pair}</td><td>${f(p.qty,6)}</td><td class="${p.unrealized_pnl>=0?'ok':'bad'}">${f(p.unrealized_pnl)}</td><td><button onclick="flatten('${p.pair}',${p.qty},${p.price})">×</button></td></tr>`).join("")||'<tr><td class=mut>flat</td></tr>';
 document.getElementById("log").innerHTML=(d.decision_log||[]).slice(0,8).map(e=>`<div class=mut>${(e.timestamp||"").slice(11,19)} <b>${e.type}</b> ${e.detail||""}</div>`).join("");
}
async function renderMarkets(){const m=await j("/api/markets");if(!m)return;
 document.querySelector("#watch tbody").innerHTML=(m.overview||[]).map(r=>
  `<tr onclick="document.getElementById('pair').value='${r.pair}';setPair()" style="cursor:pointer"><td>${r.pair}</td><td>${f(r.close)}</td><td class="${r.change_pct>=0?'ok':'bad'}">${f(r.change_pct,2)}%</td></tr>`).join("");
 const tiles=m.heatmap||[],mx=Math.max(1,...tiles.map(t=>t.size||0));
 document.getElementById("heat").innerHTML=tiles.map(t=>{const c=t.change_pct>=0?C.ok:C.bad;
  return `<div class="hx" style="flex-grow:${Math.max(1,(t.size||0)/mx*5)};background:${c}">${t.pair.split('/')[0]}<br>${f(t.change_pct,1)}%</div>`;}).join("");
}
let TF="";const TFS=["1h","4h","1d"];
function renderTfBtns(){document.getElementById("tfbtns").innerHTML=TFS.map(t=>
 `<button onclick="setTF('${t}')" style="padding:1px 6px;${t===(TF||TFS[0])?'border-color:#6ea8fe;color:#6ea8fe':''}">${t}</button>`).join(" ");}
function setTF(t){TF=t;renderCoin();}
async function renderCoin(){renderTfBtns();document.getElementById("cpair").textContent=PAIR+(TF?(" · "+TF):"");
 const d=await j("/api/coin?pair="+encodeURIComponent(PAIR)+(TF?("&tf="+encodeURIComponent(TF)):""));const c=(d&&d.candles)||[];
 const svg=document.getElementById("chart");svg.innerHTML="";const W=600,H=300,pad=4,vh=60,ch=H-vh-pad;
 if(c.length<2){svg.appendChild(el("text",{x:8,y:20,fill:"#5f6b7a"}));svg.lastChild.textContent="no data";return;}
 const lo=Math.min(...c.map(k=>k.l)),hi=Math.max(...c.map(k=>k.h)),vmax=Math.max(...c.map(k=>k.v||0));
 const x=i=>pad+i*(W-2*pad)/(c.length-1),y=v=>pad+ch-(v-lo)/((hi-lo)||1)*ch,cw=Math.max(1,(W-2*pad)/c.length*0.7);
 c.forEach((k,i)=>{const up=k.c>=k.o,col=up?C.ok:C.bad;
  svg.appendChild(el("line",{x1:x(i),x2:x(i),y1:y(k.h),y2:y(k.l),stroke:col}));
  svg.appendChild(el("rect",{x:x(i)-cw/2,width:cw,y:y(Math.max(k.o,k.c)),height:Math.max(1,Math.abs(y(k.o)-y(k.c))),fill:col}));
  const bh=(k.v||0)/(vmax||1)*vh;svg.appendChild(el("rect",{x:x(i)-cw/2,width:cw,y:H-pad-bh,height:bh,fill:col,opacity:.45}));});
 const ov=d.overlays||{},mk=a=>(a||[]).map((v,i)=>v==null?null:x(i)+","+y(v)).filter(Boolean).join(" ");
 [["ema_fast","#e6a23c"],["ema_slow","#6ea8fe"]].forEach(([kk,cc])=>{const p=el("polyline",{points:mk(ov[kk]),fill:"none",stroke:cc,"stroke-width":1});svg.appendChild(p);});
 renderBook();renderAgent();
}
async function renderBook(){const b=await j("/api/orderbook?pair="+encodeURIComponent(PAIR));if(!b)return;
 document.getElementById("spread").textContent=b.mid==null?"":`mid ${f(b.mid)} · ${f(b.spread_pct,3)}%`;
 const asks=(b.asks||[]).slice().reverse(),bids=b.bids||[];
 document.querySelector("#ob tbody").innerHTML=asks.slice(-6).map(r=>`<tr><td class=bad>${f(r.price)}</td><td>${f(r.amount,4)}</td></tr>`).join("")+
  bids.slice(0,6).map(r=>`<tr><td class=ok>${f(r.price)}</td><td>${f(r.amount,4)}</td></tr>`).join("")||'<tr><td class=mut>no book</td></tr>';
 const svg=document.getElementById("depth");svg.innerHTML="";const W=300,H=120;
 const bc=(b.bids||[]).map(r=>r.cum),ac=(b.asks||[]).map(r=>r.cum),mx=Math.max(1,...bc,...ac);
 (b.bids||[]).forEach((r,i,a)=>svg.appendChild(el("rect",{x:0,y:i*H/Math.max(1,a.length),width:r.cum/mx*W/2,height:H/Math.max(1,a.length)-1,fill:C.ok,opacity:.5})));
 (b.asks||[]).forEach((r,i,a)=>svg.appendChild(el("rect",{x:W/2,y:i*H/Math.max(1,a.length),width:r.cum/mx*W/2,height:H/Math.max(1,a.length)-1,fill:C.bad,opacity:.5})));
}
async function renderAgent(){const a=await j("/api/agentview?pair="+encodeURIComponent(PAIR));const e=document.getElementById("agent");if(!a){e.textContent="";return;}
 if(a.traded===false){e.innerHTML='<span class=mut>not in traded set</span>';return;}
 const act=a.acting?'<b class=ok>ACTING</b>':'<span class=mut>idle</span>';
 e.innerHTML=`signal <b>${a.signal||"?"}</b> · ${act} ${(a.blocked_by||[]).length?'· '+a.blocked_by.join(", "):""}<br>regime ${a.regime_enabled?'on':'OFF'} · sentiment×${f(a.sentiment_haircut??1,2)} · ${a.killswitch_engaged?'<b class=bad>KILLED</b>':'armed'}`;
}
function applyOrder(){const o=JSON.parse(localStorage.getItem("tileorder")||"[]");const g=document.getElementById("grid");
 o.forEach(tid=>{const e=g.querySelector('[data-tid="'+tid+'"]');if(e)g.appendChild(e);});}
function enableDrag(){const g=document.getElementById("grid");let drag=null;
 g.querySelectorAll(".tile").forEach(t=>{t.draggable=true;
  t.addEventListener("dragstart",()=>{drag=t;});
  t.addEventListener("dragover",e=>{e.preventDefault();const tg=e.currentTarget;
   if(drag&&drag!==tg){const r=tg.getBoundingClientRect();
    g.insertBefore(drag,(e.clientY-r.top)/r.height<0.5?tg:tg.nextSibling);}});
  t.addEventListener("drop",e=>{e.preventDefault();
   localStorage.setItem("tileorder",JSON.stringify([...g.children].map(c=>c.dataset.tid)));});});}
applyOrder();enableDrag();
renderDash();renderMarkets();renderCoin();renderTape();
setInterval(()=>{renderMarkets();renderCoin();renderTape();},6000);
try{const es=new EventSource("/api/stream");es.onmessage=ev=>{try{renderDash(JSON.parse(ev.data));}catch(_){}};}catch(_){ setInterval(renderDash,5000);}
</script></body></html>"""


def pro_html() -> str:
    """TradingView-style comprehensive dashboard (§12): Lightweight Charts main pane (candles +
    volume + EMA) with a time-synced RSI sub-pane, KPI strip, order book + depth, watchlist,
    positions (flatten), closed trades, and the agent-view + regime — over the existing JSON APIs.
    Self-hosted; the chart lib is the vendored Apache-2.0 build served at /static/. Read-only
    except the kill-switch + flatten (Inv 9)."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pro terminal</title>
<style>
 *{box-sizing:border-box} body{font:12px ui-monospace,Menlo,Consolas,monospace;background:#0b0e14;color:#cdd3de;margin:0;padding:8px}
 a{color:#6ea8fe;text-decoration:none} .ok{color:#3fd07f} .bad{color:#f06a6a} .warn{color:#e6a23c} .mut{color:#5f6b7a}
 .grid{display:grid;gap:8px;grid-template-columns:repeat(12,1fr)}
 .tile{background:#11151c;border:1px solid #1e2530;border-radius:6px;padding:8px;overflow:auto}
 .t{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#7a8699;margin-bottom:6px}
 table{border-collapse:collapse;width:100%} td,th{padding:2px 6px;text-align:right;white-space:nowrap}
 td:first-child,th:first-child{text-align:left} .kpi{display:inline-flex;flex-direction:column;margin-right:16px}
 .kpi b{font-size:16px} button{background:#222a36;color:#cdd3de;border:1px solid #313b4a;border-radius:4px;padding:3px 8px;cursor:pointer;font:inherit}
 input{background:#0b0e14;color:#cdd3de;border:1px solid #313b4a;border-radius:4px;padding:3px;font:inherit;width:90px}
 #tfbtns button{padding:1px 7px}
</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
 <b>PRO TERMINAL</b> <span id="ks" class="mut"></span>
 <span>pair <input id="pair" value="BTC/USDT"><button onclick="setPair()">go</button>
  <span id="tfbtns"></span> · <a href="/terminal">terminal</a> <a href="/">classic</a>
  <button onclick="toggleTheme()">theme</button> <button class="bad" onclick="engage()">KILL</button></span></div>
<div class="tile" id="kpis" style="margin-bottom:8px"></div>
<div class="grid">
 <div class="tile" style="grid-column:span 8">
   <div class="t">Chart · <span id="cpair"></span> · regime <span id="regime" class="mut"></span></div>
   <div id="chart" style="height:340px"></div>
   <div class="t" style="margin-top:6px">RSI(14)</div><div id="rsi" style="height:120px"></div>
 </div>
 <div class="tile" style="grid-column:span 4"><div class="t">Order book <span id="spread" class="mut"></span></div>
   <div id="depth" style="height:90px"></div><table id="ob"><tbody></tbody></table></div>
 <div class="tile" style="grid-column:span 3"><div class="t">Watchlist</div><table id="watch"><tbody></tbody></table></div>
 <div class="tile" style="grid-column:span 3"><div class="t">Agent view</div><div id="agent"></div></div>
 <div class="tile" style="grid-column:span 3"><div class="t">Positions</div><table id="pos"><tbody></tbody></table></div>
 <div class="tile" style="grid-column:span 3"><div class="t">Closed trades</div><table id="trades"><tbody></tbody></table></div>
</div>
<script src="/static/lightweight-charts.js"></script>
<script>
window.fetch=((of)=>async(u,o)=>{o=o||{};if((o.method||"GET").toUpperCase()==="POST"){o.headers=Object.assign({},o.headers,{"X-Auth-Token":localStorage.getItem("uitoken")||""});}let r=await of(u,o);if(r.status===401){const t=prompt("Operator auth token for writes:");if(t){localStorage.setItem("uitoken",t);o.headers=Object.assign({},o.headers,{"X-Auth-Token":t});r=await of(u,o);}}return r;})(window.fetch);
const NS="http://www.w3.org/2000/svg",C={ok:"#3fd07f",bad:"#f06a6a"};
let PAIR=new URLSearchParams(location.search).get("pair")||"BTC/USDT",TF="";const TFS=["1h","4h","1d"];
document.getElementById("pair").value=PAIR;
const f=(n,d=2)=>n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:d});
const j=async u=>{try{return await (await fetch(u)).json();}catch(e){return null;}};
function setPair(){PAIR=document.getElementById("pair").value.toUpperCase();history.replaceState(0,"","?pair="+encodeURIComponent(PAIR));drawCoin();drawBook();drawAgent();}
function setTF(t){TF=t;tfBtns();drawCoin();}
function tfBtns(){document.getElementById("tfbtns").innerHTML=TFS.map(t=>`<button onclick="setTF('${t}')" style="border-color:${t===(TF||TFS[0])?'#6ea8fe':'#313b4a'}">${t}</button>`).join(" ");}
function toggleTheme(){document.body.style.background=document.body.style.background==="rgb(245, 246, 248)"?"#0b0e14":"#f5f6f8";}
async function engage(){await fetch("/api/killswitch/engage",{method:"POST"});}
async function flatten(p,q,pr){if(!confirm("Flatten "+p+"?"))return;await fetch("/api/order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({side:"sell",pair:p,qty:q,price:pr})});}
let mc,rc,cs,vol,ef,es,rl;
function ensureCharts(){ if(mc)return;
 const o={layout:{background:{color:'#11151c'},textColor:'#cdd3de'},grid:{vertLines:{color:'#1e2530'},horzLines:{color:'#1e2530'}},rightPriceScale:{borderColor:'#1e2530'},timeScale:{borderColor:'#1e2530',timeVisible:true,secondsVisible:false},crosshair:{mode:1}};
 const ce=document.getElementById("chart"),re=document.getElementById("rsi");
 mc=LightweightCharts.createChart(ce,{...o,width:ce.clientWidth,height:340});
 rc=LightweightCharts.createChart(re,{...o,width:re.clientWidth,height:120});
 cs=mc.addCandlestickSeries({upColor:C.ok,downColor:C.bad,borderVisible:false,wickUpColor:C.ok,wickDownColor:C.bad});
 vol=mc.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'v'});mc.priceScale('v').applyOptions({scaleMargins:{top:0.85,bottom:0}});
 ef=mc.addLineSeries({color:'#e6a23c',lineWidth:1,lastValueVisible:false,priceLineVisible:false});
 es=mc.addLineSeries({color:'#6ea8fe',lineWidth:1,lastValueVisible:false,priceLineVisible:false});
 rl=rc.addLineSeries({color:'#b48ead',lineWidth:1,lastValueVisible:false});
 rl.createPriceLine({price:70,color:'#5f6b7a',lineStyle:2,lineWidth:1});
 rl.createPriceLine({price:30,color:'#5f6b7a',lineStyle:2,lineWidth:1});
 const sync=(a,b)=>a.timeScale().subscribeVisibleLogicalRangeChange(r=>{if(r)b.timeScale().setVisibleLogicalRange(r);});
 sync(mc,rc);sync(rc,mc);
 window.addEventListener("resize",()=>{mc.applyOptions({width:ce.clientWidth});rc.applyOptions({width:re.clientWidth});});}
async function drawCoin(){tfBtns();ensureCharts();document.getElementById("cpair").textContent=PAIR+(TF?(" · "+TF):"");
 const d=await j("/api/coin?pair="+encodeURIComponent(PAIR)+(TF?("&tf="+encodeURIComponent(TF)):""));const c=(d&&d.candles)||[];
 document.getElementById("regime").textContent=(d&&d.regime)||"—";
 if(!c.length)return;
 cs.setData(c.map(k=>({time:k.time,open:k.o,high:k.h,low:k.l,close:k.c})));
 vol.setData(c.map(k=>({time:k.time,value:k.v,color:k.c>=k.o?'rgba(63,208,127,.4)':'rgba(240,106,106,.4)'})));
 const ov=d.overlays||{},m=(a)=>c.map((k,i)=>({time:k.time,value:(a||[])[i]})).filter(p=>p.value!=null);
 ef.setData(m(ov.ema_fast));es.setData(m(ov.ema_slow));rl.setData(m(ov.rsi));}
async function drawBook(){const b=await j("/api/orderbook?pair="+encodeURIComponent(PAIR));if(!b)return;
 document.getElementById("spread").textContent=b.mid==null?"":`mid ${f(b.mid)} · ${f(b.spread_pct,3)}%`;
 const asks=(b.asks||[]).slice().reverse(),bids=b.bids||[];
 document.querySelector("#ob tbody").innerHTML=asks.slice(-6).map(r=>`<tr><td class=bad>${f(r.price)}</td><td>${f(r.amount,4)}</td></tr>`).join("")+
  bids.slice(0,6).map(r=>`<tr><td class=ok>${f(r.price)}</td><td>${f(r.amount,4)}</td></tr>`).join("")||'<tr><td class=mut>no book</td></tr>';
 const svg=document.getElementById("depth");svg.innerHTML="";const W=svg.clientWidth||280,H=90,el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
 const s=document.createElementNS(NS,"svg");s.setAttribute("width",W);s.setAttribute("height",H);
 const mx=Math.max(1,...(b.bids||[]).map(r=>r.cum),...(b.asks||[]).map(r=>r.cum));
 (b.bids||[]).forEach((r,i,a)=>s.appendChild(el("rect",{x:0,y:i*H/Math.max(1,a.length),width:r.cum/mx*W/2,height:H/Math.max(1,a.length)-1,fill:C.ok,opacity:.5})));
 (b.asks||[]).forEach((r,i,a)=>s.appendChild(el("rect",{x:W/2,y:i*H/Math.max(1,a.length),width:r.cum/mx*W/2,height:H/Math.max(1,a.length)-1,fill:C.bad,opacity:.5})));
 svg.appendChild(s);}
async function drawAgent(){const a=await j("/api/agentview?pair="+encodeURIComponent(PAIR));const e=document.getElementById("agent");if(!a){e.textContent="";return;}
 if(a.traded===false){e.innerHTML='<span class=mut>not in traded set</span>';return;}
 e.innerHTML=`signal <b>${a.signal||"?"}</b> · ${a.acting?'<b class=ok>ACTING</b>':'<span class=mut>idle</span>'} ${(a.blocked_by||[]).length?'· '+a.blocked_by.join(", "):''}`+
  `<br>regime ${a.regime_enabled?'on':'OFF'} · sentiment×${f(a.sentiment_haircut??1,2)} · ${a.killswitch_engaged?'<b class=bad>KILLED</b>':'armed'}`;}
async function drawMarkets(){const m=await j("/api/markets");if(!m)return;
 document.querySelector("#watch tbody").innerHTML=(m.overview||[]).map(r=>
  `<tr onclick="document.getElementById('pair').value='${r.pair}';setPair()" style="cursor:pointer"><td>${r.pair}</td><td>${f(r.close)}</td><td class="${r.change_pct>=0?'ok':'bad'}">${f(r.change_pct,2)}%</td></tr>`).join("");}
function renderKpis(d){const pf=d.performance||{},h=d.health||{};
 const k=[["Equity",f(d.equity),""],["Day%",f(d.day_return_pct,2),d.day_return_pct>=0?"ok":"bad"],
  ["DD%",f(d.drawdown_pct,2),"warn"],["Win%",pf.trade_count?f(pf.win_rate*100,0):"—",""],
  ["PF",pf.trade_count?(pf.profit_factor==null?"∞":f(pf.profit_factor)):"—",""],["P&L",f(pf.total_pnl||0),(pf.total_pnl>=0?"ok":"bad")]];
 document.getElementById("kpis").innerHTML=k.map(x=>`<span class="kpi"><span class="t">${x[0]}</span><b class="${x[2]}">${x[1]}</b></span>`).join("");
 document.getElementById("ks").innerHTML=(d.killswitch_engaged?'<span class=bad>KILLED</span>':'<span class=ok>armed</span>')+(h.last_tick_at?` · ${h.last_tick_at.slice(11,19)}Z`:"")+(h.last_error?` · <span class=warn>⚠</span>`:"");
 document.querySelector("#pos tbody").innerHTML=(d.positions||[]).map(p=>`<tr><td>${p.pair}</td><td>${f(p.qty,5)}</td><td class="${p.unrealized_pnl>=0?'ok':'bad'}">${f(p.unrealized_pnl)}</td><td><button onclick="flatten('${p.pair}',${p.qty},${p.price})">×</button></td></tr>`).join("")||'<tr><td class=mut>flat</td></tr>';
 document.querySelector("#trades tbody").innerHTML=(d.trades||[]).slice(0,8).map(t=>`<tr><td>${(t.exit_time||"").slice(5,16)}</td><td class="${t.pnl>=0?'ok':'bad'}">${f(t.pnl)}</td></tr>`).join("")||'<tr><td class=mut>none</td></tr>';}
async function pollDash(){const d=await j("/api/dashboard");if(d)renderKpis(d);}
drawCoin();drawBook();drawAgent();drawMarkets();pollDash();
setInterval(()=>{drawCoin();drawBook();drawAgent();drawMarkets();},6000);
try{const ev=new EventSource("/api/stream");ev.onmessage=e=>{try{renderKpis(JSON.parse(e.data));}catch(_){}}}catch(_){setInterval(pollDash,5000);}
</script></body></html>"""


def markets_html() -> str:
    """Markets overview / watchlist + heatmap page (§12, read-only) over /api/markets."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Markets</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 12px} a{color:#6ea8fe}
 table{border-collapse:collapse;width:100%;margin-top:6px}
 th,td{text-align:right;padding:5px 10px;border-bottom:1px solid #232833} th:first-child,td:first-child{text-align:left}
 th{color:#8b93a1;font-size:11px;text-transform:uppercase} .ok{color:#46d17f} .bad{color:#f06a6a}
 #heat{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
 .tile{border-radius:6px;padding:8px;min-width:70px;color:#0f1115;font-weight:600}
</style></head><body>
<h1>Markets</h1>
<div class="lbl">Heatmap (size = volume proxy, color = change)</div><div id="heat"></div>
<table id="ov"><thead><tr><th>Pair</th><th>Close</th><th>Change %</th><th>ATR %</th><th>Trend</th><th>Volume</th></tr></thead><tbody></tbody></table>
<script>
const fmt=(n)=>typeof n==="number"?n.toLocaleString(undefined,{maximumFractionDigits:4}):n;
function bg(c){if(c>=0)return`rgba(70,209,127,${Math.min(0.85,0.25+Math.abs(c)/10)})`;
 return`rgba(240,106,106,${Math.min(0.85,0.25+Math.abs(c)/10)})`;}
async function refresh(){try{const d=await (await fetch("/api/markets")).json();
 document.getElementById("heat").innerHTML=(d.heatmap||[]).map(t=>
  `<div class="tile" style="background:${bg(t.change_pct)}">${t.pair}<br>${fmt(t.change_pct)}%</div>`).join("")||"<span class=lbl>no data</span>";
 document.querySelector("#ov tbody").innerHTML=(d.overview||[]).map(r=>
  `<tr><td>${r.pair}</td><td>${fmt(r.close)}</td><td class="${r.change_pct>=0?'ok':'bad'}">${fmt(r.change_pct)}</td><td>${fmt(r.atr_pct)}</td><td>${r.trend_up?'▲':'▽'}</td><td>${fmt(r.volume)}</td></tr>`).join("");
}catch(e){}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def coin_detail_html() -> str:
    """Coin-detail page (§12): TradingView Lightweight Charts (vendored, Apache-2.0) candlestick +
    volume + EMA overlays + multi-timeframe, over our own /api/coin data (no external data feed)."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coin detail</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 12px} a{color:#6ea8fe} .lbl{color:#8b93a1;font-size:11px;text-transform:uppercase}
 #ro span{margin-right:16px} svg{background:#171a21;border:1px solid #232833;border-radius:8px}
</style></head><body>
<h1>Coin detail: <span id="pair"></span> <span id="tfbtns"></span></h1>
<div id="agent" style="margin:6px 0 10px;padding:8px 12px;background:#171a21;border:1px solid #232833;border-radius:8px"></div>
<div id="ro" class="lbl"></div>
<div id="chart" style="height:380px;border:1px solid #232833;border-radius:8px"></div>
<div class="lbl" style="margin-top:4px">Chart: TradingView Lightweight Charts™ (Apache-2.0, vendored) over our own /api/coin data</div>
<h2 class="lbl" style="margin-top:14px">Order book <span id="spread"></span></h2>
<table id="ob" style="width:auto;border-collapse:collapse"><tbody></tbody></table>
<script src="/static/lightweight-charts.js"></script>
<script>
let TF="";const TFS=["1h","4h","1d"];
function renderTf(){document.getElementById("tfbtns").innerHTML=TFS.map(t=>`<button onclick="setTF('${t}')" style="font-size:11px;padding:1px 7px;background:#222a36;color:#cdd3de;border:1px solid ${t===(TF||TFS[0])?'#6ea8fe':'#313b4a'};border-radius:4px;cursor:pointer">${t}</button>`).join(" ");}
function setTF(t){TF=t;renderTf();draw();}
let _chart,_cs,_vol,_ef,_es;
function ensureChart(){if(_chart)return;const el=document.getElementById("chart");
 _chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:380,
  layout:{background:{color:'#171a21'},textColor:'#cdd3de'},
  grid:{vertLines:{color:'#1e2530'},horzLines:{color:'#1e2530'}},
  rightPriceScale:{borderColor:'#1e2530'},timeScale:{borderColor:'#1e2530',timeVisible:true,secondsVisible:false},crosshair:{mode:1}});
 _cs=_chart.addCandlestickSeries({upColor:'#46d17f',downColor:'#f06a6a',borderVisible:false,wickUpColor:'#46d17f',wickDownColor:'#f06a6a'});
 _vol=_chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'vol'});
 _chart.priceScale('vol').applyOptions({scaleMargins:{top:0.82,bottom:0}});
 _ef=_chart.addLineSeries({color:'#e6a23c',lineWidth:1,priceLineVisible:false,lastValueVisible:false});
 _es=_chart.addLineSeries({color:'#6ea8fe',lineWidth:1,priceLineVisible:false,lastValueVisible:false});
 window.addEventListener("resize",()=>_chart.applyOptions({width:el.clientWidth}));}
const params=new URLSearchParams(location.search);const pair=params.get("pair")||"BTC/JPY";
document.getElementById("pair").textContent=pair;
const fmtn=(n)=>n==null?"—":n.toLocaleString(undefined,{maximumFractionDigits:6});
async function drawAgent(){
 try{const a=await (await fetch("/api/agentview?pair="+encodeURIComponent(pair))).json();
  const el=document.getElementById("agent");
  if(a.traded===false){el.innerHTML='<span class="lbl">Agent: not in the traded set (view-only)</span>';return;}
  if(a.ready===false||a.signal===undefined){el.innerHTML='<span class="lbl">Agent: warming up…</span>';return;}
  const act=a.acting?'<b style="color:#46d17f">ACTING (would enter)</b>':'<b style="color:#8b93a1">not acting</b>';
  const why=(a.blocked_by&&a.blocked_by.length)?' · blocked: '+a.blocked_by.join(", "):'';
  const pos=a.position&&a.position.qty?` · pos ${a.position.qty} (uPnL ${(a.position.unrealized_pnl||0).toFixed(2)})`:' · flat';
  el.innerHTML=`<span class="lbl">Agent view</span> &nbsp; signal <b>${a.signal}</b> · ${act}${why}`+
   ` · regime ${a.regime_enabled?'on':'OFF'} · sentiment×${(a.sentiment_haircut??1).toFixed(2)}`+
   ` · ${a.killswitch_engaged?'<b style="color:#f06a6a">KILLED</b>':'armed'}${pos}`;
 }catch(e){}}
async function drawBook(){
 try{const b=await (await fetch("/api/orderbook?pair="+encodeURIComponent(pair))).json();
  document.getElementById("spread").textContent=b.mid==null?"":`· mid ${fmtn(b.mid)} · spread ${fmtn(b.spread_pct)}%`;
  const asks=(b.asks||[]).slice().reverse(),bids=b.bids||[];
  const row=(side,r)=>`<tr><td style="color:${side==='ask'?'#f06a6a':'#46d17f'};padding:2px 12px;text-align:right">${fmtn(r.price)}</td><td style="padding:2px 12px;text-align:right;color:#8b93a1">${fmtn(r.amount)}</td><td style="padding:2px 12px;text-align:right;color:#6b7280">${fmtn(r.cum)}</td></tr>`;
  document.querySelector("#ob tbody").innerHTML=asks.map(r=>row("ask",r)).join("")+bids.map(r=>row("bid",r)).join("")||"<tr><td class=lbl>no book</td></tr>";
 }catch(e){}}
async function draw(){renderTf();
 const d=await (await fetch("/api/coin?pair="+encodeURIComponent(pair)+(TF?("&tf="+encodeURIComponent(TF)):""))).json();
 const c=(d&&d.candles)||[];ensureChart();
 if(c.length){
  _cs.setData(c.map(k=>({time:k.time,open:k.o,high:k.h,low:k.l,close:k.c})));
  _vol.setData(c.map(k=>({time:k.time,value:k.v,color:k.c>=k.o?'rgba(70,209,127,.4)':'rgba(240,106,106,.4)'})));
  const ov=d.overlays||{};
  _ef.setData(c.map((k,i)=>({time:k.time,value:(ov.ema_fast||[])[i]})).filter(p=>p.value!=null));
  _es.setData(c.map((k,i)=>({time:k.time,value:(ov.ema_slow||[])[i]})).filter(p=>p.value!=null));
 }
 const r=(d&&d.readouts)||{};document.getElementById("ro").innerHTML=
  `<span>close ${r.close?.toFixed?.(2)??"—"}</span><span>EMA fast(orange)/slow(blue)</span>`+
  `<span>ATR% ${r.atr_pct?.toFixed?.(3)??"—"}</span><span>trend ${r.ema_fast_above_slow?'▲':'▽'}</span>`+
  (c.length?"":'<span class=bad>no data for this timeframe on this venue</span>');
}
draw();setInterval(draw,5000);drawBook();setInterval(drawBook,5000);drawAgent();setInterval(drawAgent,5000);
</script></body></html>"""


def orders_html() -> str:
    """Order & trade panel page (§12, read-only) over /api/orders."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Orders &amp; trades</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 12px} h2{font-size:13px;color:#8b93a1;margin:16px 0 4px} a{color:#6ea8fe}
 table{border-collapse:collapse;width:100%} th,td{text-align:right;padding:4px 10px;border-bottom:1px solid #232833}
 th:first-child,td:first-child{text-align:left} th{color:#8b93a1;font-size:11px;text-transform:uppercase}
 .ok{color:#46d17f} .bad{color:#f06a6a} .agent{color:#6ea8fe} .manual{color:#e6a23c}
</style></head><body>
<h1>Orders &amp; trades</h1>
<h2>Manual order (routes through the risk engine — Inv 9)</h2>
<div>
 <select id="side"><option>buy</option><option>sell</option></select>
 <input id="qty" type="number" step="any" placeholder="qty" style="width:120px">
 <input id="price" type="number" step="any" placeholder="price" style="width:140px">
 <button onclick="preview()">Preview</button>
 <button id="placeBtn" onclick="place()" disabled>Place</button>
 <span id="pv" class="lbl"></span>
</div>
<h2>Order attempts (risk verdict + source)</h2>
<table id="att"><thead><tr><th>Time</th><th>Pair</th><th>Side</th><th>Qty</th><th>Source</th><th>Verdict</th></tr></thead><tbody></tbody></table>
<h2>Submitted</h2><table id="sub"><thead><tr><th>Time</th><th>Pair</th><th>Side</th><th>Amount</th><th>Client id</th></tr></thead><tbody></tbody></table>
<h2>Fills</h2><table id="fil"><thead><tr><th>Time</th><th>Pair</th><th>Filled</th></tr></thead><tbody></tbody></table>
<script>
window.fetch=((of)=>async(u,o)=>{o=o||{};if((o.method||"GET").toUpperCase()==="POST"){o.headers=Object.assign({},o.headers,{"X-Auth-Token":localStorage.getItem("uitoken")||""});}let r=await of(u,o);if(r.status===401){const t=prompt("Operator auth token for writes:");if(t){localStorage.setItem("uitoken",t);o.headers=Object.assign({},o.headers,{"X-Auth-Token":t});r=await of(u,o);}}return r;})(window.fetch);
const fmt=(n)=>typeof n==="number"?n.toLocaleString(undefined,{maximumFractionDigits:6}):(n??"");
function body(){return {side:document.getElementById("side").value,
 qty:parseFloat(document.getElementById("qty").value),
 price:parseFloat(document.getElementById("price").value)};}
async function preview(){const r=await (await fetch("/api/preview",{method:"POST",
 headers:{"Content-Type":"application/json"},body:JSON.stringify(body())})).json();
 const ok=r.allowed;document.getElementById("placeBtn").disabled=!ok;
 document.getElementById("pv").innerHTML=ok?'<span class="ok">preview PASS</span>':
  '<span class="bad">blocked: '+(r.reasons||[]).join("; ")+'</span>';}
async function place(){const r=await (await fetch("/api/order",{method:"POST",
 headers:{"Content-Type":"application/json"},body:JSON.stringify(body())})).json();
 document.getElementById("pv").innerHTML=r.placed?'<span class="ok">placed, filled '+fmt(r.filled)+'</span>':
  '<span class="bad">not placed: '+((r.reasons||[]).join("; ")||r.error||"")+'</span>';
 document.getElementById("placeBtn").disabled=true;refresh();}
async function refresh(){try{const d=await (await fetch("/api/orders")).json();
 document.querySelector("#att tbody").innerHTML=(d.attempts||[]).map(a=>
  `<tr><td>${a.time}</td><td>${a.pair}</td><td>${a.side}</td><td>${fmt(a.qty)}</td><td class="${a.source}">${a.source}</td><td class="${a.approved?'ok':'bad'}">${a.approved?'passed':'rejected: '+(a.reasons||[]).join(';')}</td></tr>`).join("")||"<tr><td>—</td></tr>";
 document.querySelector("#sub tbody").innerHTML=(d.submitted||[]).map(s=>
  `<tr><td>${s.time}</td><td>${s.pair}</td><td>${s.side}</td><td>${fmt(s.amount)}</td><td>${s.client_order_id}</td></tr>`).join("")||"<tr><td>—</td></tr>";
 document.querySelector("#fil tbody").innerHTML=(d.fills||[]).map(f=>
  `<tr><td>${f.time}</td><td>${f.pair}</td><td>${fmt(f.filled)}</td></tr>`).join("")||"<tr><td>—</td></tr>";
}catch(e){}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def chat_html() -> str:
    """Read-only operator chat page (Inv 1). Asks /api/chat; the assistant only explains state.

    Self-contained, no CDN. A persistent banner states the assistant cannot trade, so the operator
    is never misled into expecting it to act — all trading stays on the deterministic, risk-gated
    manual controls (/orders) and the kill-switch (/)."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Assistant (read-only)</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 8px} a{color:#6ea8fe}
 .banner{background:#2a2030;border:1px solid #4a3a2a;color:#e6a23c;padding:8px 10px;border-radius:6px;margin-bottom:12px;font-size:12px}
 #log{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
 .msg{padding:8px 10px;border-radius:8px;max-width:80%;white-space:pre-wrap;line-height:1.4}
 .you{align-self:flex-end;background:#1b3a5b} .bot{align-self:flex-start;background:#1b2230}
 .meta{font-size:11px;color:#8b93a1;margin-bottom:2px}
 form{display:flex;gap:8px} input{flex:1;padding:8px;background:#161a20;border:1px solid #232833;color:#d7dbe0;border-radius:6px}
 button{padding:8px 14px;background:#2c4a6b;color:#fff;border:0;border-radius:6px;cursor:pointer}
 button:disabled{opacity:.5;cursor:default} .muted{color:#8b93a1}
 #chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
 .chip{font-size:12px;padding:5px 9px;background:#161a20;border:1px solid #232833;color:#9fb4d6;border-radius:14px;cursor:pointer}
 .chip:hover{border-color:#2c4a6b} #bar{display:flex;gap:8px;align-items:center;margin-bottom:8px}
 #clear{background:#2a2230;font-size:12px;padding:5px 9px}
 .cites{margin-top:6px;border-top:1px solid #232833;padding-top:4px}
 .cite{font-size:11px;color:#8b93a1;font-family:monospace} .cite b{color:#6ea8fe}
</style></head><body>
<h1>Assistant</h1>
<div class="banner">⚠ Read-only assistant. It explains the bot's current state — it cannot place orders, change
 limits, or touch the kill-switch. All trading is deterministic and risk-gated.</div>
<div id="chips"></div>
<div id="log"></div>
<div id="bar"><button id="clear" type="button">Clear chat</button>
 <label class="muted" style="font-size:12px">AI: <select id="prov" style="background:#161a20;color:#d7dbe0;border:1px solid #232833;border-radius:5px;padding:3px"></select></label>
 <span id="status" class="muted"></span></div>
<form id="f"><input id="q" placeholder="Ask about equity, positions, why we're flat, the last rejection…" autocomplete="off">
 <button id="send">Ask</button></form>
<script>
const log=document.getElementById("log"),q=document.getElementById("q"),send=document.getElementById("send");
const prov=document.getElementById("prov");
async function loadProviders(){try{const d=await (await fetch("/api/chat/providers")).json();
 const ps=d.providers||[];prov.innerHTML=ps.map(p=>`<option value="${p.name}"${p.name===d.default?" selected":""}>${p.name} (${p.model})</option>`).join("");
 if(ps.length<=1)prov.parentElement.style.display="none";}catch(e){prov.parentElement.style.display="none";}}
loadProviders();
const SUGGEST=["What's my equity and P&L today?","Why are we flat right now?","What's my drawdown vs the kill-switch?",
 "Summarize my recent closed trades.","Explain the last risk rejection.","Is the bot healthy?"];
let hist=[];  // [{role, content}] prior turns, sent for multi-turn context (bounded server-side)
function add(cls,meta,text){const w=document.createElement("div");w.className="msg "+cls;
 const m=document.createElement("div");m.className="meta";m.textContent=meta;
 const b=document.createElement("div");b.textContent=text;w.appendChild(m);w.appendChild(b);
 log.appendChild(w);w.scrollIntoView();return w;}
function renderCites(wrap,cites){if(!cites||!cites.length)return;
 const c=document.createElement("div");c.className="cites";
 cites.forEach(z=>{const r=document.createElement("div");r.className="cite";
  r.innerHTML="<b>["+z.ref+"]</b> "+document.createTextNode((z.timestamp||"")+" "+(z.type||"")+" "+(z.pair||"")+" "+(z.detail||"")).textContent;
  c.appendChild(r);});wrap.appendChild(c);}
async function ask(text){text=(text||"").trim();if(!text)return;add("you","you",text);
 q.value="";send.disabled=true;const wrap=add("bot","assistant","…");const body=wrap.lastChild;
 try{const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({question:text,history:hist,provider:prov.value||undefined})});const j=await r.json();
  const ans=j.answer||j.error||"(no answer)";body.textContent=ans;renderCites(wrap,j.citations);
  hist.push({role:"user",content:text});hist.push({role:"assistant",content:ans});
  if(hist.length>12)hist=hist.slice(-12);
 }catch(err){body.textContent="error: "+err;}
 send.disabled=false;q.focus();}
document.getElementById("f").addEventListener("submit",(e)=>{e.preventDefault();ask(q.value);});
document.getElementById("clear").addEventListener("click",()=>{hist=[];log.innerHTML="";greet();});
const chips=document.getElementById("chips");
SUGGEST.forEach(s=>{const c=document.createElement("span");c.className="chip";c.textContent=s;
 c.addEventListener("click",()=>ask(s));chips.appendChild(c);});
function greet(){add("bot","assistant","Ask me about the current state — equity, drawdown, open positions, recent trades, or why the agent is or isn't trading. I remember this conversation; use the chips for quick questions.");}
greet();
// deep-link: /chat?q=... (e.g. the "explain" link on a decision-log row) auto-asks on load
const preset=new URLSearchParams(location.search).get("q");
if(preset){ask(preset);}
</script></body></html>"""


def replay_html() -> str:
    """Multi-pair dry-run/replay comparison dashboard (§12, read-only): a sortable metrics table
    across coins, a pair selector with a per-pair equity mini-chart + trades, and the read-only AI
    assistant scoped to the whole comparison (analyse/look up across coins). No CDN, no writes."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Replay comparison</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 6px} h2{font-size:13px;color:#8b93a1;margin:16px 0 6px} a{color:#6ea8fe}
 .banner{background:#12202c;border:1px solid #24425a;color:#9fc7e6;padding:6px 10px;border-radius:6px;margin-bottom:12px;font-size:12px}
 table{border-collapse:collapse;width:100%} th,td{text-align:right;padding:5px 10px;border-bottom:1px solid #232833;white-space:nowrap}
 th:first-child,td:first-child{text-align:left} th{color:#8b93a1;font-size:11px;text-transform:uppercase;cursor:pointer}
 tr.sel{background:#16202b} tbody tr{cursor:pointer} .ok{color:#46d17f} .bad{color:#f06a6a} .warn{color:#e6a23c}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px}
 .card{background:#12151b;border:1px solid #1f2530;border-radius:8px;padding:10px}
 .chip{font-size:12px;padding:4px 8px;background:#161a20;border:1px solid #232833;color:#9fb4d6;border-radius:12px;cursor:pointer;margin:2px}
 #chatlog{display:flex;flex-direction:column;gap:6px;max-height:260px;overflow:auto;margin:6px 0}
 .msg{padding:6px 9px;border-radius:8px;max-width:90%;white-space:pre-wrap;line-height:1.35}
 .you{align-self:flex-end;background:#1b3a5b} .bot{align-self:flex-start;background:#1b2230}
 input{flex:1;padding:7px;background:#161a20;border:1px solid #232833;color:#d7dbe0;border-radius:6px}
 button{padding:7px 12px;background:#2c4a6b;color:#fff;border:0;border-radius:6px;cursor:pointer}
 .kpi{font-size:12px;color:#8b93a1} .kpi b{color:#d7dbe0;font-size:15px}
</style></head><body>
<h1><span id="hdr">Replay comparison</span></h1>
<div class="banner">Read-only. Replay P&amp;L is optimistic (§2) and the dumb strategies have no validated edge —
 use it to compare pipeline behaviour across coins, not as an edge claim. The assistant explains this data; it cannot trade.</div>
<h2>Comparison (click a column to sort, a row to inspect)</h2>
<table id="tbl"><thead><tr>
 <th data-k="pair" id="th0">Pair</th>
 <th data-k="trades" title="Number of closed round-trip trades">Trades</th>
 <th data-k="win_rate" title="Share of closed trades that made money">Win%</th>
 <th data-k="profit_factor" title="Gross profit ÷ gross loss. >1 = profitable overall; ∞ = no losing trade">PF</th>
 <th data-k="total_return" title="Final equity vs starting equity">Return</th>
 <th data-k="max_drawdown" title="Worst peak-to-trough fall of equity">MaxDD</th>
 <th data-k="calmar" title="Return ÷ |max drawdown| — reward per unit of worst pain. Higher is better">Calmar</th>
 <th data-k="sharpe" title="Average return ÷ volatility. Rewards steady gains">Sharpe</th>
 <th data-k="sortino" title="Like Sharpe but only punishes downside swings">Sortino</th>
 <th data-k="var95" title="Worst-5% single-tick loss (Value at Risk)">VaR95</th>
 <th data-k="cvar95" title="Average of the worst-5% losses — the honest tail number">CVaR95</th>
 <th data-k="mc_dd_p95" title="Monte-Carlo drawdown, 95%-worst reshuffle of the returns">DD95</th>
 <th data-k="mc_dd_p99" title="Monte-Carlo drawdown, 99%-worst reshuffle (deeper tail)">DD99</th>
 <th data-k="avg_exposure" title="Average share of equity deployed in positions">Exp%</th>
 <th data-k="time_in_market" title="Share of time holding any position">TiM%</th>
 <th data-k="final_equity" title="Equity at the end of the replay">Final eq</th></tr></thead><tbody></tbody></table>
<p class="kpi">Hover a column header for what it means — full glossary on <a href="/help">Help</a>.</p>

<div class="grid">
 <div class="card"><h2 id="detTitle">Select a pair</h2><div id="kpis" class="kpi"></div>
  <svg id="eq" width="100%" height="130" viewBox="0 0 400 130" preserveAspectRatio="none"></svg>
  <h2>Trades</h2><table id="trades"><thead><tr><th>Exit</th><th>Return</th><th>P&amp;L</th></tr></thead><tbody></tbody></table>
 </div>
 <div class="card"><h2>Assistant — analyse across coins <label class="kpi" style="float:right">AI: <select id="prov" style="background:#161a20;color:#d7dbe0;border:1px solid #232833;border-radius:5px;padding:2px"></select></label></h2>
  <div id="chips"></div><div id="chatlog"></div>
  <form id="cf" style="display:flex;gap:6px"><input id="q" placeholder="e.g. which coin did best and why? compare BTC vs ETH"><button>Ask</button></form>
 </div>
</div>
<script>
let DATA=null, SORT={k:"total_return",dir:-1}, SEL=null, hist=[];
const pct=(x)=>(x==null?"—":((x*100).toFixed(2)+"%")), pf=(x)=>x==null?"∞":(typeof x==="number"?x.toFixed(2):"—");
const cls=(x)=>x>=0?"ok":"bad";
const pfcls=(x)=>(x==null||x>=1)?"ok":"bad";            // profit factor: >=1 (or ∞) good
// loss metric (negative; deeper = worse): amber past warn, red past bad
const loss=(x,warn,bad)=>x==null?"":(x<=bad?"bad":(x<=warn?"warn":""));
async function load(){DATA=await (await fetch("/api/replay")).json();
 document.getElementById("th0").textContent=DATA.label||"Pair";
 if(DATA.dimension==="strategy"&&DATA.coin){document.getElementById("hdr").textContent=
   "Strategy comparison on "+DATA.coin;}
 renderTable();
 if((DATA.table||[]).length){select((DATA.best_pair)||DATA.table[0].pair);} }
function renderTable(){const rows=[...(DATA.table||[])].sort((a,b)=>{const v=(a[SORT.k]>b[SORT.k]?1:-1)*SORT.dir;return v;});
 document.querySelector("#tbl tbody").innerHTML=rows.map(r=>`<tr data-p="${r.pair}" class="${r.pair===SEL?'sel':''}">
  <td>${r.pair}</td><td>${r.trades}</td><td>${(r.win_rate*100).toFixed(0)}</td>
  <td class="${pfcls(r.profit_factor)}">${pf(r.profit_factor)}</td>
  <td class="${cls(r.total_return)}">${pct(r.total_return)}</td>
  <td class="${loss(r.max_drawdown,-0.05,-0.10)}">${pct(r.max_drawdown)}</td>
  <td class="${cls(r.calmar)}">${(r.calmar||0).toFixed(2)}</td><td class="${cls(r.sharpe)}">${(r.sharpe||0).toFixed(3)}</td>
  <td class="${cls(r.sortino)}">${(r.sortino||0).toFixed(3)}</td>
  <td class="${loss(r.var95,-0.01,-0.03)}">${pct(r.var95)}</td><td class="${loss(r.cvar95,-0.01,-0.03)}">${pct(r.cvar95)}</td>
  <td class="${loss(r.mc_dd_p95,-0.10,-0.20)}">${pct(r.mc_dd_p95)}</td><td class="${loss(r.mc_dd_p99,-0.10,-0.20)}">${pct(r.mc_dd_p99)}</td>
  <td class="${(r.avg_exposure>0.9)?'warn':''}">${((r.avg_exposure||0)*100).toFixed(0)}</td>
  <td>${((r.time_in_market||0)*100).toFixed(0)}</td>
  <td>${(r.final_equity||0).toFixed(0)}</td></tr>`).join("")
  ||'<tr><td colspan=15 class=kpi>no replay data — run: python -m src.dry_run --replay-days N --pairs A,B,C</td></tr>';
 document.querySelectorAll("#tbl tbody tr").forEach(tr=>tr.onclick=()=>tr.dataset.p&&select(tr.dataset.p));}
document.querySelectorAll("#tbl thead th").forEach(th=>th.onclick=()=>{const k=th.dataset.k;
 SORT=(SORT.k===k)?{k,dir:-SORT.dir}:{k,dir:-1};renderTable();});
function select(p){SEL=p;const d=(DATA.detail||{})[p];renderTable();if(!d)return;
 document.getElementById("detTitle").textContent=p;
 document.getElementById("kpis").innerHTML=`<b>${pct(d.total_return)}</b> return · <b>${d.performance.trade_count}</b> trades ·
  win <b>${(d.performance.win_rate*100).toFixed(0)}%</b> · maxDD <b>${pct(d.max_drawdown)}</b> · final <b>${(d.final_equity||0).toFixed(0)}</b>`;
 drawEq((d.equity_curve||[]).map(e=>e.equity));
 document.querySelector("#trades tbody").innerHTML=(d.trades||[]).slice(-40).reverse().map(t=>
  `<tr><td>${(t.exit_time||"").slice(0,19).replace("T"," ")}</td><td class="${cls(t.return)}">${((t.return||0)*100).toFixed(2)}%</td>
   <td class="${cls(t.pnl)}">${(t.pnl||0).toFixed(2)}</td></tr>`).join("")||"<tr><td class=kpi>no trades</td></tr>";}
function drawEq(v){const el=document.getElementById("eq");if(!v||v.length<2){el.innerHTML="";return;}
 const mn=Math.min(...v),mx=Math.max(...v),rng=(mx-mn)||1;
 const pts=v.map((y,i)=>`${(i/(v.length-1)*400).toFixed(1)},${(120-(y-mn)/rng*110).toFixed(1)}`).join(" ");
 el.innerHTML=`<polyline fill="none" stroke="#6ea8fe" stroke-width="1.5" points="${pts}"/>`;}
// assistant (multi-pair; read-only)
function add(c,m,t){const w=document.createElement("div");w.className="msg "+c;const h=document.createElement("div");
 h.style.cssText="font-size:11px;color:#8b93a1";h.textContent=m;const b=document.createElement("div");b.textContent=t;
 w.appendChild(h);w.appendChild(b);document.getElementById("chatlog").appendChild(w);w.scrollIntoView();return b;}
async function ask(text){text=(text||"").trim();if(!text)return;add("you","you",text);const q=document.getElementById("q");q.value="";
 const b=add("bot","assistant","…");try{const pv=document.getElementById("prov");
  const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify({question:text,history:hist,provider:(pv&&pv.value)||undefined})});const j=await r.json();const a=j.answer||j.error||"(no answer)";b.textContent=a;
  hist.push({role:"user",content:text});hist.push({role:"assistant",content:a});if(hist.length>12)hist=hist.slice(-12);}
 catch(e){b.textContent="error: "+e;}}
["Which coin did best and why?","Compare the top two pairs.","Which coin had the worst drawdown?","Summarize the whole comparison."]
 .forEach(s=>{const c=document.createElement("span");c.className="chip";c.textContent=s;c.onclick=()=>ask(s);document.getElementById("chips").appendChild(c);});
document.getElementById("cf").addEventListener("submit",e=>{e.preventDefault();ask(document.getElementById("q").value);});
(async()=>{try{const d=await (await fetch("/api/chat/providers")).json();const pv=document.getElementById("prov");
 const ps=d.providers||[];pv.innerHTML=ps.map(p=>`<option value="${p.name}"${p.name===d.default?" selected":""}>${p.name}</option>`).join("");
 if(ps.length<=1)pv.parentElement.style.display="none";}catch(e){document.getElementById("prov").parentElement.style.display="none";}})();
load();
</script></body></html>"""


def help_html() -> str:
    """Beginner help page (§12): what each page shows, every metric in plain language (EN + VI),
    and the safety rules. Static, read-only, self-contained — the on-ramp for a new operator."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Help · AutoTrader</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px;max-width:1100px}
 h1{font-size:17px;margin:0 0 10px} h2{font-size:14px;color:#9fc7e6;margin:22px 0 8px;border-bottom:1px solid #232833;padding-bottom:4px}
 table{border-collapse:collapse;width:100%;font-size:13px} th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #1d232e;vertical-align:top}
 th{color:#8b93a1;font-size:11px;text-transform:uppercase} td:first-child{white-space:nowrap;color:#e8ecf2;font-weight:600}
 .vi{color:#8b93a1;display:block;margin-top:2px} code{background:#161a20;border:1px solid #232833;border-radius:4px;padding:1px 6px;font-size:12px}
 .safe{background:#1c1520;border:1px solid #4a2a35;border-radius:8px;padding:10px 14px;margin:10px 0}
 .safe b{color:#f0a0a0} li{margin:4px 0;line-height:1.45} .ok{color:#46d17f}
</style></head><body>
<h1>Help — how to read this software <span class="vi">Trợ giúp — cách đọc phần mềm này</span></h1>

<div class="safe"><b>This is PAPER trading.</b> No real money is used anywhere. Every simulated order still
passes the same risk engine that a real order would; the AI assistant can only <i>explain</i> — it can never trade.
<span class="vi"><b>Đây là giao dịch GIẤY (mô phỏng).</b> Không có tiền thật ở bất kỳ đâu. Mọi lệnh mô phỏng vẫn đi qua
đúng bộ máy kiểm soát rủi ro như lệnh thật; trợ lý AI chỉ <i>giải thích</i> — không bao giờ được đặt lệnh.</span></div>

<h2>The pages · Các trang</h2>
<table>
<tr><th>Page</th><th>What it shows · Nội dung</th></tr>
<tr><td><a href="/">Dashboard</a></td><td>Equity, today's P&amp;L vs limits, drawdown vs kill-switch, open positions, decision log.
 <span class="vi">Vốn, lãi/lỗ hôm nay so với giới hạn, mức sụt giảm so với công tắc dừng khẩn, vị thế đang mở, nhật ký quyết định.</span></td></tr>
<tr><td><a href="/markets">Markets</a></td><td>Watchlist + heatmap across coins. <span class="vi">Danh sách theo dõi + bản đồ nhiệt các coin.</span></td></tr>
<tr><td><a href="/coin">Coin</a></td><td>Candlestick chart with EMA/RSI overlays, order book depth, agent view ("why acting / not").
 <span class="vi">Biểu đồ nến kèm EMA/RSI, độ sâu sổ lệnh, góc nhìn agent ("vì sao hành động / không").</span></td></tr>
<tr><td><a href="/orders">Orders</a></td><td>Every order attempt with its risk verdict; manual order form (still risk-gated).
 <span class="vi">Mọi lần thử đặt lệnh kèm phán quyết rủi ro; form đặt lệnh tay (vẫn qua kiểm soát rủi ro).</span></td></tr>
<tr><td><a href="/replay">Replay</a></td><td>Compare coins or strategies over PAST data — the metric table below explains every column.
 <span class="vi">So sánh coin hoặc chiến lược trên dữ liệu QUÁ KHỨ — bảng chỉ số bên dưới giải thích từng cột.</span></td></tr>
<tr><td><a href="/terminal">Terminal</a> / <a href="/pro">Pro</a></td><td>Single-screen dense views (all widgets tiled).
 <span class="vi">Màn hình gộp mọi widget (cho người dùng thành thạo).</span></td></tr>
<tr><td><a href="/chat">Assistant</a></td><td>Ask questions about the current state in plain language; read-only.
 <span class="vi">Hỏi đáp về trạng thái hiện tại bằng ngôn ngữ tự nhiên; chỉ đọc.</span></td></tr>
</table>

<h2>Reading the numbers · Đọc các chỉ số</h2>
<table>
<tr><th>Metric</th><th>Meaning · Ý nghĩa</th></tr>
<tr><td>Equity</td><td>Total account value (cash + open positions, marked to market).
 <span class="vi">Tổng giá trị tài khoản (tiền mặt + vị thế đang mở, định giá theo thị trường).</span></td></tr>
<tr><td>Return</td><td>Final equity vs. starting equity, as %. <span class="vi">Vốn cuối so với vốn đầu, tính theo %.</span></td></tr>
<tr><td>Win rate</td><td>Share of closed trades that made money. <span class="vi">Tỷ lệ các lệnh đã đóng có lãi.</span></td></tr>
<tr><td>PF (Profit Factor)</td><td>Gross profit ÷ gross loss. &gt;1 = profitable overall; ∞ = no losing trade yet.
 <span class="vi">Tổng lãi ÷ tổng lỗ. &gt;1 = tổng thể có lãi; ∞ = chưa có lệnh lỗ nào.</span></td></tr>
<tr><td>Max DD (Drawdown)</td><td>Worst peak-to-trough fall of equity. −10% means at some point you were down 10% from the best value.
 <span class="vi">Mức sụt giảm sâu nhất từ đỉnh xuống đáy của vốn. −10% nghĩa là có lúc bạn mất 10% so với đỉnh.</span></td></tr>
<tr><td>Calmar</td><td>Return ÷ |max drawdown| — reward earned per unit of worst pain. Higher is better.
 <span class="vi">Lợi nhuận ÷ |mức sụt giảm sâu nhất| — lãi thu được trên mỗi đơn vị "đau" tệ nhất. Càng cao càng tốt.</span></td></tr>
<tr><td>Sharpe</td><td>Average return ÷ volatility of returns. Rewards steady gains, punishes swings (both directions).
 <span class="vi">Lợi nhuận trung bình ÷ độ biến động. Thưởng cho tăng trưởng đều, phạt dao động mạnh (cả hai chiều).</span></td></tr>
<tr><td>Sortino</td><td>Like Sharpe but only punishes DOWNSIDE swings — kinder to strategies that jump up.
 <span class="vi">Giống Sharpe nhưng chỉ phạt dao động GIẢM — công bằng hơn với chiến lược hay bật tăng.</span></td></tr>
<tr><td>VaR 95%</td><td>On a bad day (worst 5%), expect at least this loss per tick.
 <span class="vi">Vào ngày xấu (5% tệ nhất), dự kiến lỗ ít nhất mức này mỗi phiên.</span></td></tr>
<tr><td>CVaR 95%</td><td>The AVERAGE of those worst-5% losses — always at least as bad as VaR; the honest tail number.
 <span class="vi">TRUNG BÌNH của nhóm 5% lỗ tệ nhất — luôn xấu bằng hoặc hơn VaR; con số trung thực về rủi ro đuôi.</span></td></tr>
<tr><td>DD95 / DD99</td><td>Monte-Carlo: reshuffle the returns 500 times; the drawdown you'd expect in the 95%/99% worst run.
 One backtest is one draw of luck — this shows the distribution.
 <span class="vi">Monte-Carlo: xáo trộn chuỗi lợi nhuận 500 lần; mức sụt giảm dự kiến ở kịch bản tệ nhất 95%/99%.
 Một lần backtest chỉ là một lần may rủi — đây là cả phân phối.</span></td></tr>
<tr><td>Exp% (Exposure)</td><td>Average share of equity deployed in positions. <span class="vi">Tỷ lệ vốn trung bình đang nằm trong vị thế.</span></td></tr>
<tr><td>TiM% (Time in Market)</td><td>Share of time holding any position. <span class="vi">Tỷ lệ thời gian đang giữ vị thế.</span></td></tr>
<tr><td>Kill-switch</td><td>The emergency stop. Engaging it halts ALL new entries immediately; only a human can re-arm.
 <span class="vi">Công tắc dừng khẩn cấp. Bật lên là chặn ngay MỌI lệnh vào mới; chỉ con người mới mở lại được.</span></td></tr>
<tr><td>DSR (research)</td><td>Deflated Sharpe: Sharpe corrected for how many strategies were tried. A strategy only counts as a real edge at DSR ≥ 0.95.
 <span class="vi">Sharpe đã khấu trừ theo số chiến lược đã thử. Chỉ được coi là lợi thế thật khi DSR ≥ 0.95.</span></td></tr>
</table>

<h2>Safety rules · Nguyên tắc an toàn</h2>
<ul>
<li>No real capital until every phase gate passes and a human signs off. <span class="vi">Không dùng tiền thật cho tới khi qua đủ các cổng kiểm tra và có người phê duyệt.</span></li>
<li>Every order — bot or manual — passes the same risk engine; there is no backdoor. <span class="vi">Mọi lệnh — bot hay tay — đều qua cùng một bộ kiểm soát rủi ro; không có đường tắt.</span></li>
<li>The AI (local or cloud) is read-only: it explains, it never trades. <span class="vi">AI (local hay cloud) chỉ đọc: giải thích, không bao giờ giao dịch.</span></li>
<li>A green replay/backtest number is NOT proof of an edge — costs, luck and overfitting lie. Trust only walk-forward + DSR.
 <span class="vi">Con số xanh trong replay/backtest KHÔNG chứng minh có lợi thế — phí, may mắn và overfitting đánh lừa. Chỉ tin walk-forward + DSR.</span></li>
</ul>

<h2>Quick start · Bắt đầu nhanh</h2>
<ul>
<li><code>python -m src.dry_run --data-exchange kucoin --pair BTC/USDT --replay-days 30 --serve-ui</code>
 — replay 30 days of real data, then browse the result here. <span class="vi">— chạy lại 30 ngày dữ liệu thật rồi xem kết quả tại đây.</span></li>
<li><code>python -m src.dry_run --pairs "BTC/USDT,ETH/USDT,SOL/USDT" --replay-days 30 --serve-ui</code>
 — compare coins on <a href="/replay">/replay</a>. <span class="vi">— so sánh các coin.</span></li>
<li><code>python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --iterations 0 --serve-ui</code>
 — the live paper loop (the real ≥30-day run). <span class="vi">— vòng lặp giấy chạy thật (đợt ≥30 ngày).</span></li>
</ul>
<p class="ok">Full operator guide: <code>docs/RUNBOOK.md</code></p>
</body></html>"""


def serve(ctx: OperatorContext, *, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Thin stdlib HTTP adapter around handle_request (not unit-tested — sockets).

    Read-only operator surface on localhost. Bodies are JSON. The kill-switch and preview are the
    only writes (§12). Intended for the solo operator's own machine, not public exposure.
    """
    import time as _t
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = None
            resp = handle_request(method, self.path, body, ctx, dict(self.headers))
            payload = resp.body if isinstance(resp.body, str) else json.dumps(resp.body)
            out = payload.encode("utf-8")
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def _stream_dashboard(self) -> None:
            """Server-Sent-Events: push a dashboard snapshot every ~2s until the client leaves."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    frame = dashboard_sse_frame(ctx.dashboard()).encode("utf-8")
                    self.wfile.write(frame)
                    self.wfile.flush()
                    _t.sleep(2.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # client disconnected — end the stream quietly

        def _serve_static(self, name: str) -> None:
            """Serve a vendored asset (e.g. lightweight-charts.js) from src/ui/static, same-origin."""
            import pathlib
            base = pathlib.Path(__file__).parent / "static"
            target = (base / name).resolve()
            if base.resolve() not in target.parents or not target.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = target.read_bytes()
            ct = "application/javascript" if target.suffix == ".js" else "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:   # noqa: N802 (stdlib API)
            p = self.path.split("?", 1)[0].rstrip("/")
            if p == "/api/stream":
                self._stream_dashboard()
                return
            if p.startswith("/static/"):
                self._serve_static(p[len("/static/"):])
                return
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

    def _universe():
        import pandas as pd
        t0 = pd.Timestamp("2026-06-01T00:00:00Z")

        def frame(step, vol):
            rows, p = [], 1_000_000.0
            for i in range(60):
                p *= (1.0 + step)
                rows.append([t0 + pd.Timedelta(hours=i), p, p * 1.002, p * 0.998, p, vol])
            return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return {"BTC/JPY": frame(0.004, 12.0), "ETH/JPY": frame(-0.003, 30.0),
                "XRP/JPY": frame(0.001, 8.0)}

    def _markets():
        from src.ui.markets import build_markets_overview, heatmap_tiles
        overview = build_markets_overview(_universe(), lookback=24)
        return {"overview": overview, "heatmap": heatmap_tiles(overview)}

    def _coin(p: str, tf: str = "") -> dict:
        from src.ui.coin_detail import build_coin_detail
        uni = _universe()
        name = p if p in uni else next(iter(uni))
        out = build_coin_detail(name, uni[name])
        out["timeframe"] = tf or "1h"  # demo: same series for any timeframe
        return out

    def _trades_tape(p: str) -> dict:
        from src.ui.trades_feed import format_trades
        base = price
        raw = [{"timestamp": (1_700_000 + i) * 1000, "price": base + (i % 7 - 3) * 1000,
                "amount": 0.01 * (1 + i % 5), "side": "buy" if i % 2 else "sell"}
               for i in range(40)]
        return {"trades": format_trades(raw)}

    def _agentview(p: str) -> dict:
        if p != pair:
            return {"pair": p, "traded": False}
        return {"pair": pair, "traded": True, "ready": True, "price": price, "signal": "hold",
                "acting": False, "blocked_by": ["intent:hold"], "regime_enabled": True,
                "target_regime": "trend", "sentiment_haircut": 1.0, "sentiment_fresh": None,
                "killswitch_engaged": False,
                "position": {"qty": 0.02, "entry_price": 9.5e6, "unrealized_pnl": 10000.0}}

    def _orderbook(p: str) -> dict:
        from src.ui.orderbook import build_orderbook_view
        mid = price
        book = {"bids": [[mid - i * 1000, 0.5 + i * 0.2] for i in range(1, 11)],
                "asks": [[mid + i * 1000, 0.5 + i * 0.2] for i in range(1, 11)]}
        return build_orderbook_view(book)

    def _orders() -> dict:
        from src.events.log import Event
        from src.ui.orders_panel import build_order_trade_panel
        evs = [
            Event("RiskPassed", "2026-06-28T00:00:01+00:00",
                  {"pair": pair, "side": "buy", "qty": 0.02, "price": price,
                   "source": "strategy", "reasons": []}),
            Event("OrderSubmitted", "2026-06-28T00:00:01+00:00",
                  {"pair": pair, "side": "buy", "qty": 0.02, "client_order_id": "demo-1"}),
            Event("FillReceived", "2026-06-28T00:00:01+00:00", {"pair": pair, "filled": 0.02}),
        ]
        return build_order_trade_panel(evs)

    def _dashboard() -> dict:
        from src.events.log import Event
        from src.ui.performance import performance_summary
        demo_events = [  # chronological (oldest first) — the model reverses to newest-first
            Event("OrderSubmitted", "2026-06-28T09:00:00+00:00",
                  {"pair": pair, "side": "buy", "qty": 0.02}),
            Event("RiskRejected", "2026-06-28T10:00:00+00:00",
                  {"pair": pair, "side": "buy", "reasons": ["below_min_notional"]}),
            Event("SignalGenerated", "2026-06-28T11:59:00+00:00", {"pair": pair, "intent": "hold"}),
        ]
        payload = dashboard_payload(state=state, cfg=cfg, prices={pair: price}, killswitch=ks,
                                    events=demo_events)
        demo_trades = [
            {"exit_time": "2026-06-28T09:00:00+00:00", "pair": pair, "qty": 0.02,
             "entry_price": 9.5e6, "exit_price": 9.9e6, "return": 0.0421, "pnl": 8000.0},
            {"exit_time": "2026-06-28T11:00:00+00:00", "pair": pair, "qty": 0.02,
             "entry_price": 9.9e6, "exit_price": 9.8e6, "return": -0.0101, "pnl": -2000.0},
        ]
        payload["trades"] = list(reversed(demo_trades))
        payload["performance"] = performance_summary(demo_trades)
        payload["health"] = {"running": True, "tick_count": 42,
                             "last_tick_at": "2026-06-28T12:00:00+00:00", "pair": pair,
                             "timeframe": "1h"}
        return payload

    def _chat(body: dict) -> dict:
        # demo assistant: a canned transport so /chat is navigable offline (no Ollama). The real
        # read-only assistant is wired in build_live_context against a local Ollama server.
        from src.llm.chat import OllamaChat
        from src.llm.chat import answer as _ans
        canned = ('{"message":{"content":"(demo assistant) I am read-only — I explain state but '
                  'cannot trade. This demo has fixed data; run --serve-ui with Ollama for live '
                  'answers."}}')
        client = OllamaChat(transport=lambda url, payload: canned)
        return _ans(str(body.get("question", "")), _dashboard(), client=client,
                    history=body.get("history"))

    return OperatorContext(
        dashboard=_dashboard,
        preview=lambda body: preview_payload(
            pair=body.get("pair", pair), side=body.get("side", "buy"),
            qty=float(body.get("qty", 0.0) or 0.0), price=float(body.get("price", price) or price),
            state=state, cfg=cfg, ctx=_ctx_obj()),
        killswitch=ks,
        markets=_markets,
        coin=_coin,
        orders=_orders,
        orderbook=_orderbook,
        agentview=_agentview,
        trades_tape=_trades_tape,
        chat=_chat,
    )


def main() -> None:
    from src.core.console import force_utf8_stdio
    force_utf8_stdio()
    print("[ui] operator API on http://127.0.0.1:8787  (GET /api/dashboard, POST /api/preview, "
          "POST /api/killswitch/engage|rearm) — read-only demo, paper only")
    serve(build_demo_context())


if __name__ == "__main__":
    main()
