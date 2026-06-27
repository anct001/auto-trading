"""src/strategy/shadow.py — §15 shadow-mode hypothetical P&L (P3).

Shadow mode screens a new or modified strategy *before* it is allowed to trade: it tracks the P&L
the strategy WOULD have made from its intents, without ever placing an order. Two hard properties:

  - **It never trades** — there is no broker/exchange here, only accounting. It cannot, by
    construction, place or size a live order (Inv. 1/3 are upheld trivially).
  - **It is long-or-flat** (spot-only, §0): an exit while flat or an entry while long is ignored;
    it never opens a short.

Fills are at the observed mark price (optionally minus a fee). This is **systematically
optimistic** — it ignores slippage and the position's own market impact (§2) — so shadow P&L is a
*relative* screen (is variant B better than A under identical optimism?), never absolute truth.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT


@dataclass(frozen=True)
class ShadowFill:
    timestamp: pd.Timestamp
    side: str          # "buy" | "sell"
    price: float
    qty: float


class ShadowBook:
    """Hypothetical long-or-flat book: deploys full equity on entry, marks to market, never trades."""

    def __init__(self, initial_equity: float = 1.0, *, fee: float = 0.0):
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not 0.0 <= fee < 1.0:
            raise ValueError("fee must be in [0, 1)")
        self.initial_equity = float(initial_equity)
        self.fee = float(fee)
        self.cash = float(initial_equity)
        self.position_qty = 0.0
        self._entry_price = 0.0
        self.realized_pnl = 0.0
        self.fills: list[ShadowFill] = []
        self._returns: list[float] = []  # per closed round-trip return (for win/loss stats)

    @property
    def in_position(self) -> bool:
        return self.position_qty > 0.0

    @property
    def trade_count(self) -> int:
        return len(self._returns)

    def observe(self, *, timestamp: pd.Timestamp, price: float, intent: str) -> None:
        """Process one bar's intent at ``price``. Long-or-flat; fills at the mark (optimistic)."""
        if price <= 0:
            return
        if intent == INTENT_ENTER_LONG and not self.in_position:
            qty = (self.cash * (1.0 - self.fee)) / price
            self.cash = 0.0
            self.position_qty = qty
            self._entry_price = price
            self.fills.append(ShadowFill(timestamp, "buy", price, qty))
        elif intent == INTENT_EXIT and self.in_position:
            entry_value = self.position_qty * self._entry_price
            proceeds = self.position_qty * price * (1.0 - self.fee)
            self.realized_pnl += proceeds - entry_value
            self._returns.append(proceeds / entry_value - 1.0)
            self.fills.append(ShadowFill(timestamp, "sell", price, self.position_qty))
            self.cash = proceeds
            self.position_qty = 0.0
            self._entry_price = 0.0
        # any other (intent, state) combination is a no-op — including exit-while-flat (no short)

    def equity(self, price: float) -> float:
        """Mark-to-market hypothetical equity at ``price`` (cash + position value)."""
        return self.cash + self.position_qty * price

    def stats(self) -> dict:
        wins = sum(1 for r in self._returns if r > 0)
        losses = sum(1 for r in self._returns if r < 0)
        # when still holding, mark at entry so the headline figure stays realized-only and stable
        marked = self.equity(self._entry_price) if self.in_position else self.cash
        return {
            "trade_count": self.trade_count,
            "wins": wins,
            "losses": losses,
            "realized_pnl": self.realized_pnl,
            "total_return": marked / self.initial_equity - 1.0,
            "optimistic": True,  # fills at mark, ignores slippage/impact (§2) — a relative screen
        }
