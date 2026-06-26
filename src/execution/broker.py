"""src/execution/broker.py — §9 idempotent orders, precision, TIF.

MONEY CODE (TDD mandatory). The broker is strictly downstream of the risk gate: it submits ONLY
approved `RiskDecision`s (Invariant 3/9) — there is no way to reach the exchange without an
approval. It is idempotent (a repeated client order id never double-trades, §9), rounds qty/price
to the exchange's lot-step/tick-size before sending (shared precision with sizing, §9), and sets
an explicit time-in-force on resting orders so a stale limit doesn't rot.

The exchange is injected (ccxt-like) so all of this is testable against a mock — no network, no
real capital. Real fills are only observed at P4 (§2 parity caveat).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.risk.sizing import MarketConstraints, floor_to_step
from src.risk.types import RiskDecision


class SupportsExchange(Protocol):
    def create_order(
        self, symbol: str, type: str, side: str, amount: float, price: float, params: dict
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SubmitResult:
    client_order_id: str
    exchange_id: str
    status: str
    amount: float
    filled: float
    remaining: float


class Broker:
    """Submits risk-approved orders to the exchange, idempotently and with correct precision."""

    def __init__(self, exchange: SupportsExchange, markets: dict[str, MarketConstraints]):
        self._exchange = exchange
        self._markets = markets
        self._submitted: dict[str, SubmitResult] = {}

    def submit(
        self,
        decision: RiskDecision,
        *,
        client_order_id: str,
        order_type: str = "limit",
        tif: str = "GTC",
    ) -> SubmitResult:
        """Submit an approved order. Raises on an unapproved decision; idempotent per id."""
        if not decision.approved or decision.sized is None:
            raise ValueError("broker refuses to submit an unapproved order (Invariant 3)")

        # idempotency: a repeated client order id never reaches the exchange twice (§9)
        if client_order_id in self._submitted:
            return self._submitted[client_order_id]

        order = decision.sized
        market = self._markets[order.pair]
        amount = floor_to_step(order.qty, market.lot_step)
        price = floor_to_step(order.price, market.tick_size)
        params = {"clientOrderId": client_order_id, "timeInForce": tif}

        reply = self._exchange.create_order(order.pair, order_type, order.side, amount, price, params)

        filled = float(reply.get("filled", 0.0))
        result = SubmitResult(
            client_order_id=client_order_id,
            exchange_id=str(reply["id"]),
            status=str(reply.get("status", "open")),
            amount=amount,
            filled=filled,
            remaining=amount - filled,
        )
        self._submitted[client_order_id] = result
        return result
