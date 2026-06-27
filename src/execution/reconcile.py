"""src/execution/reconcile.py — §9 restart-safe reconciliation vs exchange truth.

MONEY CODE (TDD mandatory). After a restart (or every cycle) the **exchange is the source of
truth**. Reconciliation:

  - **adopts** positions the exchange reports that local state missed (an entry filled while the
    bot was down), and takes the exchange's quantity when they differ (e.g. a partial fill);
  - **drops local ghosts** — open orders local state believes in but the exchange no longer has
    (filled/canceled while down);
  - **never re-submits** an order that already exists on the exchange (no double-trade);
  - **flags naked positions** — a non-zero position with no protective (reduceOnly) stop on the
    exchange — so a stop can be re-attached (§4).

This is pure: it computes the corrected view + actions; the caller applies them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LocalState:
    positions: dict[str, float] = field(default_factory=dict)
    open_order_ids: set[str] = field(default_factory=set)


@dataclass
class ExchangeTruth:
    positions: dict[str, float] = field(default_factory=dict)
    # each order: {"clientOrderId": str, "pair": str, "reduceOnly": bool}
    open_orders: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileResult:
    positions: dict[str, float]          # corrected positions (exchange truth)
    adopted_positions: list[str]         # present on exchange, unknown locally
    orphan_local_order_ids: list[str]    # local open ids no longer on the exchange → drop
    naked_positions: list[str]           # non-zero positions with no protective stop


def reconcile(local: LocalState, exchange: ExchangeTruth) -> ReconcileResult:
    """Reconcile local state against exchange truth. See module docstring."""
    positions = dict(exchange.positions)  # exchange wins
    adopted = [pair for pair in exchange.positions if pair not in local.positions]

    exchange_open_ids = {o["clientOrderId"] for o in exchange.open_orders}
    orphans = [oid for oid in local.open_order_ids if oid not in exchange_open_ids]

    # sum protective (reduceOnly) order amounts per pair; a position is naked if its quantity
    # exceeds the covered quantity (a stop covering only PART of the position leaves a remainder
    # unprotected — §4).
    covered_qty: dict[str, float] = {}
    for o in exchange.open_orders:
        if o.get("reduceOnly"):
            covered_qty[o["pair"]] = covered_qty.get(o["pair"], 0.0) + float(o.get("amount", 0.0))
    naked = [
        pair for pair, qty in exchange.positions.items()
        if qty > 1e-12 and covered_qty.get(pair, 0.0) + 1e-9 < qty
    ]

    return ReconcileResult(
        positions=positions,
        adopted_positions=sorted(adopted),
        orphan_local_order_ids=sorted(orphans),
        naked_positions=sorted(naked),
    )
