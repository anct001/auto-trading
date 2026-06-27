"""src/risk/sizing.py — §4/§9 vol-aware sizing + minNotional/lot/tick feasibility.

MONEY CODE (TDD mandatory). Computes how large a *new long entry* may be, then checks it against
the exchange's order-size constraints.

Sizing (inverse-ATR, volatility-aware):
    risk_amount = equity × per_trade_risk_pct
    stop_distance = atr_stop_mult × ATR          (the protective-stop distance, §4)
    qty = risk_amount / stop_distance            (lose ≈ risk_amount if the stop is hit)
Higher volatility → wider stop → smaller size. The notional is then capped at the
fractional-Kelly ceiling (max_fractional_kelly × equity) — never raw Kelly (§4). NB: we have no
edge/odds estimate here, so this is the conservative *cap* interpretation, not Kelly sizing.

Feasibility (the crypto trap, §4/§9):
  - round price DOWN to tick_size, qty DOWN to lot_step (never up — rounding up would exceed the
    risk cap),
  - if the resulting qty is 0 (below lot_step) or notional < minNotional → **SKIP and flag**.
  - The size is **never** rounded up past the risk cap to meet the exchange minimum. A persistent
    sub-minimum means the account is too small for this risk policy — surfaced, not violated.

This module proposes a size; the engine (R6) still disposes. It never places an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.risk.config import RiskConfig
from src.risk.types import Order, PortfolioState


@dataclass(frozen=True)
class MarketConstraints:
    """Exchange order-size constraints for a pair (from ccxt market metadata)."""

    min_notional: float
    lot_step: float
    tick_size: float


@dataclass(frozen=True)
class SizingResult:
    """Outcome of sizing. ``feasible`` results carry a tradeable qty/price; infeasible ones carry
    only a reason (the engine treats them as SKIP, never as a forced trade)."""

    feasible: bool
    reason: str
    pair: str = ""
    qty: float = 0.0
    price: float = 0.0
    notional: float = 0.0

    def to_order(self, *, source: str = "strategy") -> Order:
        if not self.feasible:
            raise ValueError(f"cannot build an order from an infeasible sizing: {self.reason}")
        return Order(pair=self.pair, side="buy", qty=self.qty, price=self.price, source=source)


def floor_to_step(value: float, step: float) -> float:
    """Floor ``value`` to a multiple of ``step`` exactly (Decimal, no float drift). Shared by the
    broker for order precision (§9), so sizing and submission round identically."""
    if step <= 0:
        return value
    v, s = Decimal(str(value)), Decimal(str(step))
    return float((v // s) * s)


def compute_size(
    *,
    pair: str,
    price: float,
    atr: float,
    state: PortfolioState,
    cfg: RiskConfig,
    market: MarketConstraints,
    atr_stop_mult: float = 2.0,
    size_multiplier: float = 1.0,
) -> SizingResult:
    """Size a new long entry for ``pair`` at ``price``. See module docstring for the model.

    ``size_multiplier`` is an optional bounded *shrink* on the risk budget — the channel the
    slow-loop sentiment haircut (§3, P1) uses. It can only TIGHTEN: values > 1.0 are clamped to
    1.0 so the LLM can never grow a position (Inv. 1/3); a non-positive value is rejected
    (fail-closed). The default 1.0 leaves the deterministic sizing untouched (P0 path). The
    backtest never passes this (the LLM feature is forward-validated only — no look-ahead, §6).
    """
    if price <= 0:
        return SizingResult(feasible=False, reason="invalid_price", pair=pair)
    if size_multiplier <= 0:
        return SizingResult(feasible=False, reason="invalid_size_multiplier", pair=pair)
    stop_distance = atr_stop_mult * atr
    if atr <= 0 or stop_distance <= 0:
        # without a volatility estimate there is no stop distance and no risk-based size
        return SizingResult(feasible=False, reason="no_volatility", pair=pair)

    # haircut may only shrink — clamp to ≤ 1.0 so sentiment can never increase exposure
    risk_amount = state.equity * (cfg.per_trade_risk_pct / 100.0) * min(size_multiplier, 1.0)
    raw_qty = risk_amount / stop_distance

    # fractional-Kelly cap: a single bet may not deploy more than this fraction of equity
    max_qty = (cfg.max_fractional_kelly * state.equity) / price
    capped_qty = min(raw_qty, max_qty)

    price_r = floor_to_step(price, market.tick_size)
    qty_r = floor_to_step(capped_qty, market.lot_step)

    if qty_r <= 0:
        return SizingResult(feasible=False, reason="below_lot_step", pair=pair)

    notional = qty_r * price_r
    if notional < market.min_notional:
        # SKIP — do NOT round qty up to reach min_notional (that would breach the risk cap)
        return SizingResult(feasible=False, reason="below_min_notional", pair=pair)

    return SizingResult(
        feasible=True, reason="ok", pair=pair, qty=qty_r, price=price_r, notional=notional
    )
