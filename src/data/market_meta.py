"""src/data/market_meta.py — real MarketConstraints from ccxt exchange metadata (§4/§9).

The dry-run used hand-typed approximate constraints ("tick 0.1"), which drifts from the real
venue (bitbank BTC/JPY ticks at 1 JPY). This resolves min-notional / lot-step / tick-size from
the exchange's own market metadata, handling ccxt's two precision conventions:

  - ``precisionMode == TICK_SIZE (4)``: precision values ARE the step (0.0001),
  - otherwise (DECIMAL_PLACES, the default): precision values are decimal places (4 → 1e-4).

Fail-soft: any missing/odd metadata falls back to the caller's defaults with a reason string —
paper sizing keeps working, and the operator can see WHY the fallback was used.
"""
from __future__ import annotations

from src.risk.sizing import MarketConstraints

_TICK_SIZE_MODE = 4  # ccxt: DECIMAL_PLACES=2, SIGNIFICANT_DIGITS=3, TICK_SIZE=4


def _step(value, mode: int, default: float) -> float:
    if value is None:
        return default
    v = float(value)
    if v <= 0:
        return default
    if mode == _TICK_SIZE_MODE:
        return v
    return 10.0 ** (-int(v))  # decimal places → step


def market_constraints_from_ccxt(exchange, pair: str,
                                 fallback: MarketConstraints) -> tuple[MarketConstraints, str]:
    """Resolve (constraints, source) for ``pair`` from ccxt metadata; fall back fail-soft."""
    try:
        markets = getattr(exchange, "markets", None)
        if not markets:
            markets = exchange.load_markets()
        m = markets[pair]
        mode = int(getattr(exchange, "precisionMode", 2) or 2)
        precision = m.get("precision") or {}
        limits = m.get("limits") or {}
        min_notional = (limits.get("cost") or {}).get("min")
        constraints = MarketConstraints(
            min_notional=float(min_notional) if min_notional else fallback.min_notional,
            lot_step=_step(precision.get("amount"), mode, fallback.lot_step),
            tick_size=_step(precision.get("price"), mode, fallback.tick_size),
        )
        return constraints, f"{getattr(exchange, 'id', 'exchange')} metadata"
    except Exception as e:  # noqa: BLE001 — metadata problems must not stop a paper run
        return fallback, f"fallback ({type(e).__name__}: {e})"
