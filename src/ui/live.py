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
        return dashboard_payload(
            state=runner.snapshot_state(), cfg=cfg, prices=runner.marks(),
            killswitch=runner.killswitch, events=runner.loop.events.read_all(),
        )

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

    return OperatorContext(dashboard=_dashboard, preview=_preview, killswitch=runner.killswitch,
                           orders=_orders)
