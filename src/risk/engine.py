"""src/risk/engine.py — §4 THE single gate (Invariant 3 / 9).

MONEY CODE (TDD mandatory). Every order — from the strategy, from LLM-influenced sizing, or from
a manual UI entry — passes the SAME `validate()`. There is no backdoor: the §12 pre-trade
preview calls this exact function, so what the operator previews is what the engine enforces.

Semantics (spot-only, long-or-flat — §0):
  - **Entries (buys)** run the full gauntlet, **fail-closed**, with every breach reason
    aggregated (so the preview can show them all): kill-switch → exchange assertions → de-peg →
    daily/drawdown halts → exposure/concurrency/correlation (+ optional beta) → order feasibility.
  - **Exits (sells)** are risk-reducing and are allowed even when halted — otherwise a kill-switch
    "flatten" could not run. But a sell may never exceed the held quantity: selling more than you
    hold would open a short, which is forbidden before P5.

Runtime controls may only tighten or halt (Inv. 3) — enforced in the kill-switch (R5) and the
config loader (R0); this gate never loosens anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.risk import limits
from src.risk.config import RiskConfig
from src.risk.depeg import check_depeg
from src.risk.exchange_assert import exchange_mismatches
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, PortfolioState, RiskDecision

_QTY_EPS = 1e-12


@dataclass
class RiskContext:
    """Everything the gate needs beyond the order + portfolio state + config."""

    prices: dict[str, float]
    exchange_state: dict
    killswitch: KillSwitch | None = None
    correlations: dict[tuple[str, str], float] = field(default_factory=dict)
    betas: dict[str, float] | None = None
    max_beta: float | None = None
    market: MarketConstraints | None = None


def validate(
    order: Order, state: PortfolioState, cfg: RiskConfig, ctx: RiskContext
) -> RiskDecision:
    """Approve or reject ``order``. The one gate every order passes (Inv. 3/9)."""
    if order.side == "sell":
        return _validate_exit(order, state)
    return _validate_entry(order, state, cfg, ctx)


def _validate_exit(order: Order, state: PortfolioState) -> RiskDecision:
    pos = state.positions.get(order.pair)
    held = pos.qty if pos else 0.0
    if held <= 0:
        return RiskDecision.reject("no_position_to_sell")
    if order.qty > held + _QTY_EPS:
        # selling more than held would open a short — forbidden, spot-only (§0)
        return RiskDecision.reject("would_short")
    return RiskDecision.approve(order)


def _validate_entry(
    order: Order, state: PortfolioState, cfg: RiskConfig, ctx: RiskContext
) -> RiskDecision:
    reasons: list[str] = []

    if ctx.killswitch is not None and ctx.killswitch.is_halted:
        reasons.append(f"killswitch:{ctx.killswitch.halt_reason}")

    reasons += exchange_mismatches(ctx.exchange_state, cfg)

    depeg = check_depeg(state.quote_price, cfg, is_entry=True)
    if not depeg.ok:
        reasons.append(depeg.reason)

    # account-level halts (block new entries / flatten)
    for result in (
        limits.check_daily_soft(state, cfg),
        limits.check_daily_hard(state, cfg),
        limits.check_drawdown(state, cfg),
    ):
        if not result.ok:
            reasons.append(result.reason)

    # order-level exposure checks
    for result in (
        limits.check_gross_exposure(order, state, cfg, ctx.prices),
        limits.check_per_asset(order, state, cfg, ctx.prices),
        limits.check_concurrency(order, state, cfg),
        limits.check_correlation_cluster(order, state, cfg, ctx.prices, ctx.correlations),
    ):
        if not result.ok:
            reasons.append(result.reason)

    if ctx.betas is not None and ctx.max_beta is not None:
        beta = limits.check_portfolio_beta(order, state, ctx.prices, ctx.betas, ctx.max_beta)
        if not beta.ok:
            reasons.append(beta.reason)

    if ctx.market is not None:
        feasibility = _feasibility_reason(order, ctx.market)
        if feasibility:
            reasons.append(feasibility)

    if reasons:
        return RiskDecision.reject(*reasons)
    return RiskDecision.approve(order)


def _feasibility_reason(order: Order, market: MarketConstraints) -> str | None:
    """Light feasibility re-check at the gate (sizing R1 owns precise lot/tick rounding)."""
    if order.qty < market.lot_step:
        return "below_lot_step"
    if order.notional < market.min_notional:
        return "below_min_notional"
    return None
