"""src/risk/depeg.py — §4 quote-stablecoin de-peg guard.

MONEY CODE (TDD mandatory). Equity and P&L are denominated in the quote stablecoin (USDT/USDC),
implicitly valued at $1. If the quote de-pegs, every risk and P&L number is silently wrong. On a
de-peg beyond the configured threshold the guard:

  - **blocks new entries** (do not open fresh risk on a corrupted unit of account),
  - **flags a re-mark** of equity (the caller must re-value at the real quote price), and
  - leaves **exits allowed** — we can always leave a position, we just don't enter (§13.16).

The threshold is strict: a deviation exactly at the threshold is still considered pegged.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.risk.config import RiskConfig
from src.risk.limits import CheckResult


@dataclass(frozen=True)
class DepegStatus:
    depegged: bool
    deviation: float  # |quote_price - 1.0|, as a fraction
    remark_needed: bool


def assess_depeg(quote_price: float, cfg: RiskConfig) -> DepegStatus:
    """Measure the quote's deviation from $1 and decide whether it is de-pegged."""
    deviation = abs(quote_price - 1.0)
    # epsilon so float noise (e.g. 0.99 → 0.010000000000000009) doesn't flip an at-threshold value
    depegged = deviation > cfg.depeg_threshold_pct / 100.0 + 1e-9
    return DepegStatus(depegged=depegged, deviation=deviation, remark_needed=depegged)


def check_depeg(quote_price: float, cfg: RiskConfig, *, is_entry: bool) -> CheckResult:
    """Gate an order: block entries during a de-peg; always allow exits."""
    status = assess_depeg(quote_price, cfg)
    if status.depegged and is_entry:
        return CheckResult(False, "quote_depeg")
    return CheckResult(True)
