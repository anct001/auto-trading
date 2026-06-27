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


@dataclass(frozen=True)
class Response:
    status: int
    body: dict | str
    content_type: str = "application/json"


def handle_request(method: str, path: str, body: dict | None, ctx: OperatorContext) -> Response:
    """Route one request. Pure: no I/O. See module docstring for the §12 contract."""
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    path = parts.path.rstrip("/") or "/"

    if path == "/":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, index_html(), content_type="text/html; charset=utf-8")

    if path == "/coin":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, coin_detail_html(), content_type="text/html; charset=utf-8")

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
        return Response(200, orders_html(), content_type="text/html; charset=utf-8")

    if path == "/api/orders":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        empty = {"attempts": [], "submitted": [], "fills": []}
        return Response(200, ctx.orders() if ctx.orders else empty)

    if path == "/terminal":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, terminal_html(), content_type="text/html; charset=utf-8")

    if path == "/markets":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, markets_html(), content_type="text/html; charset=utf-8")

    if path == "/api/markets":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.markets() if ctx.markets else {"overview": [], "heatmap": []})

    if path == "/api/dashboard":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, ctx.dashboard())

    if path == "/api/preview":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        return Response(200, ctx.preview(body or {}))

    if path == "/api/order":
        if method != "POST":
            return Response(405, {"error": "use POST"})
        if ctx.place is None:
            return Response(404, {"error": "manual order placing not enabled"})
        return Response(200, ctx.place(body or {}))

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
<h1>Operator dashboard <a href="/terminal">· terminal</a> <a href="/markets">· markets</a> <a href="/orders">· orders</a> <span id="ks" class="muted"></span></h1>
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
  const cards=[
   {l:"Equity",v:fmt(d.equity),c:""},
   {l:"Day P&L %",v:fmt(d.day_return_pct),c:dpl,g:gauge(Math.abs(Math.min(0,d.day_return_pct))/Math.abs(d.daily_hard_pct)*100,dpl)},
   {l:"Drawdown %",v:fmt(d.drawdown_pct),c:ddc,g:gauge(Math.abs(d.drawdown_pct)/Math.abs(d.killswitch_pct)*100,ddc)},
   {l:"Gross exp %",v:fmt(d.gross_exposure_pct),c:grc,g:gauge(d.gross_exposure_pct/d.gross_cap_pct*100,grc)},
   {l:"Sentiment fresh",v:String(d.sentiment_fresh),c:""}];
  document.getElementById("cards").innerHTML=cards.map(c=>
   `<div class="card"><div class="lbl">${c.l}</div><div class="val ${c.c}">${c.v}</div>${c.g||""}</div>`).join("");
  drawEquity(d.equity_curve);
  const ks=d.killswitch_engaged?`<span class="bad">KILLED (${d.killswitch_reason||""})</span>`:`<span class="ok">armed</span>`;
  const h=d.health||{};const htxt=h.last_tick_at?`· ticks ${h.tick_count} · last ${h.last_tick_at.slice(11,19)}Z`:"";
  document.getElementById("ks").innerHTML="· "+ks+" "+htxt;
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
  document.getElementById("log").innerHTML=(d.decision_log||[]).map(e=>
   `<div>${e.timestamp} <b>${e.type}</b> ${e.pair} ${e.detail}</div>`).join("")||"<div class=muted>no events</div>";
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
  (h.last_tick_at?` · ticks ${h.tick_count} · ${h.last_tick_at.slice(11,19)}Z`:"");
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
<h1>Markets <a href="/">· dashboard</a></h1>
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
<h1>Coin detail: <span id="pair"></span> <span id="tfbtns"></span> <a href="/markets">· markets</a> <a href="/">· dashboard</a></h1>
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
<h1>Orders &amp; trades <a href="/">· dashboard</a></h1>
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
            resp = handle_request(method, self.path, body, ctx)
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
        from src.ui.performance import performance_summary
        payload = dashboard_payload(state=state, cfg=cfg, prices={pair: price}, killswitch=ks)
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
    )


def main() -> None:
    from src.core.console import force_utf8_stdio
    force_utf8_stdio()
    print("[ui] operator API on http://127.0.0.1:8787  (GET /api/dashboard, POST /api/preview, "
          "POST /api/killswitch/engage|rearm) — read-only demo, paper only")
    serve(build_demo_context())


if __name__ == "__main__":
    main()
