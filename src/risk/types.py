"""src/risk/types.py — risk-engine core types (PLAN_RISK R0).

The vocabulary the risk layer speaks. Each type validates its own inputs, so a malformed order
or portfolio state cannot exist downstream. Decisions are explainable: a rejection always
carries reasons (feeds the §12 decision log and §15 event log).
"""
from __future__ import annotations

from dataclasses import dataclass, field

_VALID_SIDES = frozenset({"buy", "sell"})
# every order names where it came from; all three pass the SAME engine (Inv. 9)
_VALID_SOURCES = frozenset({"strategy", "manual", "llm"})


@dataclass(frozen=True)
class Order:
    """A concrete intent to trade ``qty`` of ``pair`` at ``price``. Sizing has already run."""

    pair: str
    side: str
    qty: float
    price: float
    source: str

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"order qty must be positive, got {self.qty}")
        if self.price <= 0:
            raise ValueError(f"order price must be positive, got {self.price}")
        if self.side not in _VALID_SIDES:
            raise ValueError(f"order side must be one of {sorted(_VALID_SIDES)}, got {self.side!r}")
        if self.source not in _VALID_SOURCES:
            raise ValueError(
                f"order source must be one of {sorted(_VALID_SOURCES)}, got {self.source!r}"
            )

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass(frozen=True)
class Position:
    """An open spot position (long-only pre-P5)."""

    pair: str
    qty: float
    entry_price: float

    def value(self, price: float) -> float:
        return self.qty * price


@dataclass(frozen=True)
class PortfolioState:
    """A snapshot of the account the risk engine evaluates an order against."""

    equity: float
    peak_equity: float
    day_start_equity: float
    # REQUIRED (no default): the de-peg guard (§4) is blind without a real quote price. Forcing
    # callers to supply it prevents a silent fail-open where a stale/assumed $1 hides a de-peg.
    quote_price: float  # USDT/USDC vs $1
    positions: dict[str, Position] = field(default_factory=dict)
    realized_day_pnl: float = 0.0

    def __post_init__(self) -> None:
        for name in ("equity", "peak_equity", "day_start_equity", "quote_price"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")

    def drawdown(self) -> float:
        """Peak-to-current drawdown as a fraction (≤ 0)."""
        return self.equity / self.peak_equity - 1.0

    def day_return(self) -> float:
        """Today's return vs the day's starting equity (fraction)."""
        return self.equity / self.day_start_equity - 1.0

    def total_position_value(self, prices: dict[str, float]) -> float:
        return sum(p.value(prices[pair]) for pair, p in self.positions.items())


@dataclass(frozen=True)
class RiskDecision:
    """The engine's verdict. ``approved`` orders carry the sized order; rejections carry reasons.

    ``approval`` is an unforgeable capability that ONLY ``risk.engine`` can mint (see
    engine.Approval). The broker requires it, so an order cannot reach the exchange via a
    hand-built ``approved=True`` decision — closing the Invariant-3 backdoor. ``approve()`` below
    is for inspection/tests only and yields no capability (the broker will refuse it)."""

    approved: bool
    reasons: tuple[str, ...] = ()
    sized: Order | None = None
    approval: object | None = None  # engine.Approval capability; None ⇒ not engine-approved

    @classmethod
    def reject(cls, *reasons: str) -> "RiskDecision":
        return cls(approved=False, reasons=tuple(reasons), sized=None)

    @classmethod
    def approve(cls, order: Order) -> "RiskDecision":
        """Inspection-only approval (no capability — the broker refuses it)."""
        return cls(approved=True, reasons=(), sized=order)
