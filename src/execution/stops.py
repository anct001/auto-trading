"""src/execution/stops.py — §4 exchange-side protective stops attached at fill.

MONEY CODE (TDD mandatory). The local risk engine vanishes if the process dies, so a protective
stop is placed **exchange-side** at fill time — a crash/disconnect/dead process then never leaves
a naked position. The stop is a **reduceOnly sell**: it can only close the long, never flip to a
short (spot-only, §0). Idempotent: one stop per position (keyed by client order id).

OCO/bracket is used where the venue supports it; here we model the protective-stop leg, which is
the safety-critical part.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.risk.sizing import MarketConstraints, floor_to_step


class SupportsExchange(Protocol):
    def create_order(
        self, symbol: str, type: str, side: str, amount: float, price: float, params: dict
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StopResult:
    client_order_id: str
    exchange_id: str
    stop_price: float
    amount: float
    reduce_only: bool


def protective_stop_price(
    entry_price: float, atr: float, atr_stop_mult: float, tick_size: float
) -> float:
    """Stop price = entry − atr_stop_mult×ATR, floored to the tick. Matches the sizing stop
    distance (R1), so the realized loss at the stop ≈ the per-trade risk budget."""
    return floor_to_step(entry_price - atr_stop_mult * atr, tick_size)


class StopManager:
    """Attaches reduceOnly protective stops exchange-side, idempotently."""

    def __init__(self, exchange: SupportsExchange, markets: dict[str, MarketConstraints]):
        self._exchange = exchange
        self._markets = markets
        self._stops: dict[str, StopResult] = {}

    def attach(self, *, pair: str, qty: float, stop_price: float, client_order_id: str) -> StopResult:
        """Place a reduceOnly sell stop for ``qty`` at ``stop_price`` (idempotent per id)."""
        if client_order_id in self._stops:
            return self._stops[client_order_id]

        market = self._markets[pair]
        amount = floor_to_step(qty, market.lot_step)
        price = floor_to_step(stop_price, market.tick_size)
        if price <= 0:
            # a non-positive stop means volatility exceeded the entry price — an invalid order and
            # effectively no protection. Refuse: the caller must not hold an unprotectable position.
            raise ValueError(f"non-positive protective stop price {price} (vol too high vs entry)")
        params = {"reduceOnly": True, "stopPrice": price, "clientOrderId": client_order_id}

        reply = self._exchange.create_order(pair, "stop", "sell", amount, price, params)
        result = StopResult(
            client_order_id=client_order_id,
            exchange_id=str(reply["id"]),
            stop_price=price,
            amount=amount,
            reduce_only=True,
        )
        self._stops[client_order_id] = result
        return result

    def attach_protective(
        self,
        *,
        pair: str,
        qty: float,
        entry_price: float,
        atr: float,
        atr_stop_mult: float,
        client_order_id: str,
    ) -> StopResult:
        """Compute the stop from entry/ATR and attach it."""
        stop_price = protective_stop_price(
            entry_price, atr, atr_stop_mult, self._markets[pair].tick_size
        )
        return self.attach(pair=pair, qty=qty, stop_price=stop_price, client_order_id=client_order_id)
