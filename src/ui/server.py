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
    coin: Callable[[str], dict] | None = None  # pair -> coin_detail payload
    orders: Callable[[], dict] | None = None   # () -> {"attempts", "submitted", "fills"}
    place: Callable[[dict], dict] | None = None  # body -> place a manual order (Inv 9); None = disabled
    orderbook: Callable[[str], dict] | None = None  # pair -> order-book depth view


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
        return Response(200, ctx.coin(pair) if ctx.coin else {})

    if path == "/api/orderbook":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        pair = (query.get("pair") or [""])[0]
        return Response(200, ctx.orderbook(pair) if ctx.orderbook else {})

    if path == "/orders":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        return Response(200, orders_html(), content_type="text/html; charset=utf-8")

    if path == "/api/orders":
        if method != "GET":
            return Response(405, {"error": "read-only endpoint"})
        empty = {"attempts": [], "submitted": [], "fills": []}
        return Response(200, ctx.orders() if ctx.orders else empty)

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
</style></head><body>
<h1>Operator dashboard <a href="/markets">· markets</a> <a href="/orders">· orders</a> <span id="ks" class="muted"></span></h1>
<div class="grid" id="cards"></div>
<div class="card" style="min-width:100%"><div class="lbl">Equity curve</div><svg id="eq" viewBox="0 0 900 130" preserveAspectRatio="none"></svg></div>
<div class="grid" id="perf"></div>
<div class="card" style="min-width:100%"><div class="lbl">Open positions</div><table id="pos"><thead>
<tr><th>Pair</th><th>Qty</th><th>Value</th><th>Unrealized</th><th>Exposure %</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="min-width:100%"><div class="lbl">Closed trades</div><table id="trades"><thead>
<tr><th>Exit time</th><th>Pair</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Return %</th><th>P&L</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="min-width:100%"><div class="lbl">Decision log</div><div id="log"></div></div>
<div style="margin-top:12px"><button class="kill" onclick="engage()">Engage kill-switch</button>
<button onclick="rearm()">Re-arm</button> <span id="msg" class="muted"></span></div>
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
async function refresh(){
 try{const d=await (await fetch("/api/dashboard")).json();
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
    """Coin-detail page (§12): SVG candlestick + EMA overlays + readouts over /api/coin?pair=."""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coin detail</title>
<style>
 body{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dbe0;margin:0;padding:16px}
 h1{font-size:16px;margin:0 0 12px} a{color:#6ea8fe} .lbl{color:#8b93a1;font-size:11px;text-transform:uppercase}
 #ro span{margin-right:16px} svg{background:#171a21;border:1px solid #232833;border-radius:8px}
</style></head><body>
<h1>Coin detail: <span id="pair"></span> <a href="/markets">· markets</a> <a href="/">· dashboard</a></h1>
<div id="ro" class="lbl"></div>
<svg id="chart" viewBox="0 0 900 360" width="100%" height="360"></svg>
<h2 class="lbl" style="margin-top:14px">Order book <span id="spread"></span></h2>
<table id="ob" style="width:auto;border-collapse:collapse"><tbody></tbody></table>
<script>
const params=new URLSearchParams(location.search);const pair=params.get("pair")||"BTC/JPY";
document.getElementById("pair").textContent=pair;
const fmtn=(n)=>n==null?"—":n.toLocaleString(undefined,{maximumFractionDigits:6});
async function drawBook(){
 try{const b=await (await fetch("/api/orderbook?pair="+encodeURIComponent(pair))).json();
  document.getElementById("spread").textContent=b.mid==null?"":`· mid ${fmtn(b.mid)} · spread ${fmtn(b.spread_pct)}%`;
  const asks=(b.asks||[]).slice().reverse(),bids=b.bids||[];
  const row=(side,r)=>`<tr><td style="color:${side==='ask'?'#f06a6a':'#46d17f'};padding:2px 12px;text-align:right">${fmtn(r.price)}</td><td style="padding:2px 12px;text-align:right;color:#8b93a1">${fmtn(r.amount)}</td><td style="padding:2px 12px;text-align:right;color:#6b7280">${fmtn(r.cum)}</td></tr>`;
  document.querySelector("#ob tbody").innerHTML=asks.map(r=>row("ask",r)).join("")+bids.map(r=>row("bid",r)).join("")||"<tr><td class=lbl>no book</td></tr>";
 }catch(e){}}
const NS="http://www.w3.org/2000/svg";
function line(pts,color){const p=document.createElementNS(NS,"polyline");p.setAttribute("points",pts);
 p.setAttribute("fill","none");p.setAttribute("stroke",color);p.setAttribute("stroke-width","1.2");return p;}
async function draw(){
 const d=await (await fetch("/api/coin?pair="+encodeURIComponent(pair))).json();
 const c=d.candles||[];const svg=document.getElementById("chart");svg.innerHTML="";
 if(!c.length){svg.innerHTML='<text x=20 y=30 fill="#8b93a1">no data</text>';return;}
 const W=900,H=360,pad=30;const lo=Math.min(...c.map(x=>x.l)),hi=Math.max(...c.map(x=>x.h));
 const x=i=>pad+i*(W-2*pad)/(c.length-1||1);const y=v=>H-pad-(v-lo)/((hi-lo)||1)*(H-2*pad);
 const cw=Math.max(1,(W-2*pad)/c.length*0.6);
 c.forEach((k,i)=>{const up=k.c>=k.o;const col=up?"#46d17f":"#f06a6a";
  const wick=document.createElementNS(NS,"line");wick.setAttribute("x1",x(i));wick.setAttribute("x2",x(i));
  wick.setAttribute("y1",y(k.h));wick.setAttribute("y2",y(k.l));wick.setAttribute("stroke",col);svg.appendChild(wick);
  const r=document.createElementNS(NS,"rect");r.setAttribute("x",x(i)-cw/2);r.setAttribute("width",cw);
  r.setAttribute("y",y(Math.max(k.o,k.c)));r.setAttribute("height",Math.max(1,Math.abs(y(k.o)-y(k.c))));
  r.setAttribute("fill",col);svg.appendChild(r);});
 const ef=d.overlays.ema_fast,es=d.overlays.ema_slow;
 const mk=arr=>arr.map((v,i)=>v==null?null:x(i)+","+y(v)).filter(Boolean).join(" ");
 svg.appendChild(line(mk(ef),"#e6a23c"));svg.appendChild(line(mk(es),"#6ea8fe"));
 const r=d.readouts||{};document.getElementById("ro").innerHTML=
  `<span>close ${r.close?.toFixed?.(2)}</span><span>EMA fast(orange)/slow(blue)</span>`+
  `<span>ATR% ${r.atr_pct?.toFixed?.(3)}</span><span>trend ${r.ema_fast_above_slow?'▲':'▽'}</span>`;
}
draw();setInterval(draw,5000);drawBook();setInterval(drawBook,5000);
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

    def _coin(p: str) -> dict:
        from src.ui.coin_detail import build_coin_detail
        uni = _universe()
        name = p if p in uni else next(iter(uni))
        return build_coin_detail(name, uni[name])

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
    )


def main() -> None:
    from src.core.console import force_utf8_stdio
    force_utf8_stdio()
    print("[ui] operator API on http://127.0.0.1:8787  (GET /api/dashboard, POST /api/preview, "
          "POST /api/killswitch/engage|rearm) — read-only demo, paper only")
    serve(build_demo_context())


if __name__ == "__main__":
    main()
