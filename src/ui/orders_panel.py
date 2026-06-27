"""src/ui/orders_panel.py — §12 order & trade panel read model (read-only).

Derives the operator's order/trade history from the append-only event log (§15): every order
*attempt* (with its source — agent vs manual — and the risk engine's approve/reject reasons), the
submissions that reached the broker, and the fills. Pure and deterministic; it never trades and
never places anything.

Note: "open/resting orders" (e.g. exchange-side protective stops) come from exchange reconcile
state (§9), not the event stream, so they are surfaced elsewhere; this panel is the decision +
execution trail. Newest first, capped at ``limit``.
"""
from __future__ import annotations

from src.events.log import (
    FILL_RECEIVED,
    ORDER_SUBMITTED,
    RISK_PASSED,
    RISK_REJECTED,
    Event,
)


def build_order_trade_panel(events: list[Event], *, limit: int = 50) -> dict:
    """Build {attempts, submitted, fills} from the event stream (newest first, capped)."""
    attempts, submitted, fills = [], [], []
    for ev in events:
        p = ev.payload
        if ev.type in (RISK_PASSED, RISK_REJECTED):
            attempts.append({
                "time": ev.timestamp, "pair": p.get("pair", ""), "side": p.get("side", ""),
                "qty": p.get("qty"), "price": p.get("price"), "source": p.get("source", ""),
                "approved": ev.type == RISK_PASSED, "reasons": list(p.get("reasons", [])),
            })
        elif ev.type == ORDER_SUBMITTED:
            submitted.append({
                "time": ev.timestamp, "pair": p.get("pair", ""), "side": p.get("side", ""),
                "amount": p.get("qty"), "client_order_id": p.get("client_order_id", ""),
            })
        elif ev.type == FILL_RECEIVED:
            fills.append({"time": ev.timestamp, "pair": p.get("pair", ""), "filled": p.get("filled")})

    return {
        "attempts": list(reversed(attempts))[:limit],
        "submitted": list(reversed(submitted))[:limit],
        "fills": list(reversed(fills))[:limit],
    }
