"""src/ui/preview.py — manual-order pre-trade risk preview (§12, Invariant 9).

The operator UI may submit a manual order, but there is **no backdoor around the limits**: the
preview runs the *exact same* `risk.engine.validate` the engine applies on send, so what the
preview shows is what the engine will do. The place button is enabled only when ``allowed`` is
true; a hard-limit failure cannot be overridden from the UI.

This module computes:
  - the authoritative verdict (``allowed`` + ``reasons``) straight from the engine, and
  - informational ``metrics`` (notional, exposure-after, per-asset-after, daily headroom,
    drawdown) for the pass/fail badges — display only, never the decision.

Malformed inputs fail closed (``allowed=False``, ``order=None``) instead of raising, so a typo in
the UI can never crash the operator surface.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.types import Order, PortfolioState


@dataclass(frozen=True)
class OrderPreview:
    allowed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float]
    order: Order | None


def _gross_exposure(state: PortfolioState, prices: dict[str, float]) -> float:
    total = 0.0
    for pair, pos in state.positions.items():
        total += pos.value(prices.get(pair, pos.entry_price))
    return total


def _metrics(order: Order, state: PortfolioState, cfg: RiskConfig,
             prices: dict[str, float]) -> dict[str, float]:
    eq = state.equity
    held = state.positions.get(order.pair)
    held_val = held.value(prices.get(order.pair, order.price)) if held else 0.0
    signed = order.notional if order.side == "buy" else -order.notional
    gross_after = _gross_exposure(state, prices) + (order.notional if order.side == "buy" else 0.0)
    per_asset_after = max(0.0, held_val + signed)
    return {
        "notional": order.notional,
        "notional_pct_equity": 100.0 * order.notional / eq,
        "gross_exposure_after_pct": 100.0 * gross_after / eq,
        "per_asset_after_pct": 100.0 * per_asset_after / eq,
        "per_asset_cap_pct": float(cfg.per_asset_cap_pct),
        "gross_exposure_cap_pct": float(cfg.gross_exposure_pct),
        "day_return_pct": 100.0 * state.day_return(),
        "daily_soft_pct": float(cfg.daily_soft_pct),
        "daily_hard_pct": float(cfg.daily_hard_pct),
        "drawdown_pct": 100.0 * state.drawdown(),
        "killswitch_pct": float(cfg.max_drawdown_killswitch_pct),
    }


def preview_manual_order(
    *,
    pair: str,
    side: str,
    qty: float,
    price: float,
    state: PortfolioState,
    cfg: RiskConfig,
    ctx: engine.RiskContext,
) -> OrderPreview:
    """Preview a manual order through the real risk gate (Inv. 9). Fail-closed on bad input."""
    try:
        order = Order(pair=pair, side=side, qty=float(qty), price=float(price), source="manual")
    except (ValueError, TypeError) as e:
        return OrderPreview(allowed=False, reasons=(f"invalid_order:{e}",), metrics={}, order=None)

    decision = engine.validate(order, state, cfg, ctx)
    return OrderPreview(
        allowed=decision.approved,
        reasons=tuple(decision.reasons),
        metrics=_metrics(order, state, cfg, ctx.prices),
        order=order,
    )
