"""src/ui/live.py — wire the operator UI to a LIVE dry-run runner (§12).

`build_live_context` turns a running `DryRunner` into an `OperatorContext` (server.py) so the
dashboard, agent decision log, and manual-order preview reflect the *actual* paper portfolio in
real time — not a static demo snapshot. Read-only by construction:

  - the dashboard/preview read a snapshot of the paper account at the latest marks;
  - the manual-order preview still runs the exact `risk.engine.validate` (Inv. 9) — it previews,
    it does not place;
  - the only write is the human kill-switch, the SAME `KillSwitch` the trading loop consults, so
    engaging it from the UI halts the live loop (Inv. 3/5).

The UI runs in a background thread and only *reads* the account; the trading loop is never blocked
or gated by it (Inv. 2). Reads are eventually-consistent (a poll may briefly see an account
mid-update between fill steps); it self-corrects on the next poll and never affects trading.
"""
from __future__ import annotations

from src.ui.api import dashboard_payload, preview_payload
from src.ui.server import OperatorContext

# the exchange-state the dry-run asserts (mirrors dry_run._GOOD_EXCHANGE for the preview ctx)
_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}


def build_live_context(runner) -> OperatorContext:
    """Build an OperatorContext backed by a live ``DryRunner`` (see module docstring)."""
    from src.risk import engine

    cfg = runner.loop.cfg
    market = runner.loop.market

    def _dashboard() -> dict:
        from src.ui.performance import performance_summary
        payload = dashboard_payload(
            state=runner.snapshot_state(), cfg=cfg, prices=runner.marks(),
            killswitch=runner.killswitch, events=runner.loop.events.read_all(),
            equity_history=runner.equity_history(),
        )
        closed = runner.trades()
        payload["trades"] = list(reversed(closed))[:50]   # newest first
        payload["performance"] = performance_summary(closed)
        payload["health"] = runner.health()
        return payload

    def _preview(body: dict) -> dict:
        prices = runner.marks()
        ctx = engine.RiskContext(prices=prices, exchange_state=dict(_GOOD_EXCHANGE),
                                 killswitch=runner.killswitch, market=market)
        default_price = prices.get(runner.pair, 0.0)
        return preview_payload(
            pair=body.get("pair", runner.pair), side=body.get("side", "buy"),
            qty=float(body.get("qty", 0.0) or 0.0),
            price=float(body.get("price", default_price) or default_price),
            state=runner.snapshot_state(), cfg=cfg, ctx=ctx,
        )

    def _orders() -> dict:
        from src.ui.orders_panel import build_order_trade_panel
        return build_order_trade_panel(runner.loop.events.read_all())

    def _coin(p: str, tf: str = "") -> dict:
        from src.data import feed
        from src.ui.coin_detail import build_coin_detail
        empty = {"pair": p, "candles": [], "overlays": {"ema_fast": [], "ema_slow": []},
                 "readouts": {}, "timeframe": tf or runner.timeframe}
        if p != runner.pair:
            return empty
        tf = tf or runner.timeframe
        if tf == runner.timeframe:
            df = runner.current_frame()  # pre-warmed deep buffer for the traded timeframe
        else:
            try:  # other timeframes: pull fresh history (fail-soft so the UI never breaks)
                now = runner.data_exchange.milliseconds()
                since = now - 250 * feed.timeframe_to_ms(tf)
                df = feed.fetch_ohlcv_history(runner.data_exchange, p, tf, since_ms=since,
                                              page_limit=runner.limit, now_ms=now)
            except Exception:
                df = None
        if df is None or len(df) == 0:
            return empty
        out = build_coin_detail(runner.pair, df)
        out["timeframe"] = tf
        return out

    def _trades_tape(p: str) -> dict:
        from src.ui.trades_feed import format_trades
        try:
            raw = runner.data_exchange.fetch_trades(p or runner.pair)
        except Exception:
            return {"trades": []}
        return {"trades": format_trades(raw)}

    def _markets() -> dict:
        from src.ui.markets import build_markets_overview, heatmap_tiles
        df = runner.current_frame()
        if df is None or len(df) == 0:
            return {"overview": [], "heatmap": []}
        overview = build_markets_overview({runner.pair: df})
        return {"overview": overview, "heatmap": heatmap_tiles(overview)}

    def _agentview(p: str) -> dict:
        from datetime import datetime, timezone

        from src.risk import engine
        from src.ui.agent_view import build_agent_view
        if p != runner.pair:
            return {"pair": p, "traded": False}
        df = runner.current_frame()
        if df is None or len(df) == 0:
            return {"pair": p, "traded": True, "ready": False}
        rc = engine.RiskContext(prices=runner.marks(), exchange_state=dict(_GOOD_EXCHANGE),
                                killswitch=runner.killswitch, market=runner.loop.market)
        av = build_agent_view(pair=runner.pair, df=df, strategy=runner.loop.strategy,
                              state=runner.snapshot_state(), cfg=runner.loop.cfg, ctx=rc,
                              now=datetime.now(timezone.utc))
        av["traded"] = True
        av["ready"] = True
        return av

    def _orderbook(p: str) -> dict:
        # read-only depth from the (view) data feed; fail-soft so the UI never breaks on a feed hiccup
        from src.ui.orderbook import build_orderbook_view
        try:
            raw = runner.data_exchange.fetch_order_book(p or runner.pair)
        except Exception:
            return build_orderbook_view({})
        return build_orderbook_view(raw)

    def _place(body: dict) -> dict:
        # the one UI write to the trading path: a MANUAL paper order through the same risk engine
        # + broker as the bot (Inv 3/9), serialized with the loop via the runner lock.
        prices = runner.marks()
        default_price = prices.get(runner.pair, 0.0)
        return runner.place_manual(
            side=body.get("side", "buy"),
            qty=float(body.get("qty", 0.0) or 0.0),
            price=float(body.get("price", default_price) or default_price),
        )

    return OperatorContext(dashboard=_dashboard, preview=_preview, killswitch=runner.killswitch,
                           orders=_orders, place=_place, orderbook=_orderbook, agentview=_agentview,
                           coin=_coin, markets=_markets, trades_tape=_trades_tape)
