"""backtest/fill_parity.py — §2 fill-realism / dry-run parity harness.

Our paper broker (and any dry-run) fills at the mark with no slippage, latency, or own-impact —
**systematically optimistic** (§2). Signal parity (`backtest/parity.py`) proves the *decisions*
match the backtest; this module quantifies what the *fills* hide: the optimism gap between a
mark fill and a realistic slippage/latency fill.

This is the role the spec assigns Freqtrade's dry-run at P3 (PLAN.md Slice 6): a more realistic
fill engine to compare against. Freqtrade isn't a dependency here; instead a `FillModel` seam lets
any reference plug in — `SlippageLatencyFill` is the built-in reference, and a future
`FreqtradeFill` (adapting Freqtrade dry-run fills) would implement the same protocol. The harness
and accounting are pure/testable regardless of which reference is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Fill:
    price: float
    filled_qty: float


class FillModel(Protocol):
    """How a venue/model fills an order at a reference price (Freqtrade adapter would implement this)."""

    def fill(self, *, side: str, ref_price: float, qty: float) -> Fill: ...


class MarkFill:
    """Optimistic fill at the mark — what the paper broker / a naive dry-run does (§2)."""

    def fill(self, *, side: str, ref_price: float, qty: float) -> Fill:
        return Fill(price=ref_price, filled_qty=qty)


@dataclass(frozen=True)
class SlippageLatencyFill:
    """Realistic reference: price slips against you by ``slippage_bps``; optional partial fill.

    ``slippage_bps`` is basis points (50 = 0.50%). A buy fills higher, a sell lower. ``fill_ratio``
    (≤ 1.0) models a partial fill from latency/limited depth.
    """

    slippage_bps: float = 0.0
    fill_ratio: float = 1.0

    def fill(self, *, side: str, ref_price: float, qty: float) -> Fill:
        s = self.slippage_bps / 10_000.0
        price = ref_price * (1.0 + s) if side == "buy" else ref_price * (1.0 - s)
        return Fill(price=price, filled_qty=qty * self.fill_ratio)


@dataclass(frozen=True)
class FillParityReport:
    n_fills: int
    total_optimism: float          # currency the paper P&L overstates by ignoring slippage
    optimism_pct_of_volume: float  # total_optimism / total notional, %
    mean_slippage_pct: float       # mean |price difference| / ref, %
    avg_fill_ratio: float          # reference fill_qty / intended qty (1.0 = full)

    def summary(self) -> str:
        return (f"fill parity: {self.n_fills} fills · optimism gap {self.total_optimism:.2f} "
                f"({self.optimism_pct_of_volume:.4f}% of volume) · mean slippage "
                f"{self.mean_slippage_pct:.4f}% · avg fill ratio {self.avg_fill_ratio:.3f}")


def compare_fills(intents: list[dict], optimistic: FillModel, reference: FillModel) -> FillParityReport:
    """Compare an optimistic fill model vs a realistic reference over a list of intended orders.

    Each intent is ``{"side", "ref_price", "qty"}``. The optimism gap is the cash the optimistic
    fills save vs the reference (slippage the paper P&L never paid): for a buy, the reference costs
    more; for a sell, it nets less — both inflate the optimistic result by ``qty·ref·slip``.
    """
    total_optimism = 0.0
    total_volume = 0.0
    slip_pcts: list[float] = []
    fill_ratios: list[float] = []
    for it in intents:
        side = it["side"]
        ref_price = float(it["ref_price"])
        qty = float(it["qty"])
        opt = optimistic.fill(side=side, ref_price=ref_price, qty=qty)
        ref = reference.fill(side=side, ref_price=ref_price, qty=qty)
        notional = qty * ref_price
        total_volume += notional
        # optimism: paper buys cheaper / sells richer than the realistic reference
        if side == "buy":
            total_optimism += (ref.price - opt.price) * qty
        else:
            total_optimism += (opt.price - ref.price) * qty
        slip_pcts.append(abs(ref.price - opt.price) / ref_price * 100.0 if ref_price else 0.0)
        fill_ratios.append(ref.filled_qty / qty if qty else 1.0)

    n = len(intents)
    return FillParityReport(
        n_fills=n,
        total_optimism=total_optimism,
        optimism_pct_of_volume=(total_optimism / total_volume * 100.0) if total_volume else 0.0,
        mean_slippage_pct=(sum(slip_pcts) / n) if n else 0.0,
        avg_fill_ratio=(sum(fill_ratios) / n) if n else 1.0,
    )
