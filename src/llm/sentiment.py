"""src/llm/sentiment.py — §3 bounded position-size haircut (FLOOR ≥ 0.5).

The slow-loop LLM classifies sentiment; the fast loop may use it ONLY as a position-size
**haircut**: a multiplier in `[FLOOR, 1.0]`, `FLOOR ≥ 0.5`. The contract is absolute (Inv. 1/2):

  - it may **only shrink** an entry the deterministic strategy already decided — never grow it
    (non-negative sentiment → 1.0), never trigger an entry, never flip direction, never veto
    (the multiplier is ≥ FLOOR ≥ 0.5 > 0, so it can't zero a trade out);
  - **absent / stale / unknown pair → 1.0** (neutral) — the fast loop must run correctly without
    the slow loop (Inv. 2);
  - **Default `FLOOR = 1.0`** makes the whole feature a strict no-op (logged, not acting) until it
    is forward-validated by ablation (§6, P1 DONE-GATE).

This module is pure and deterministic — it never calls the LLM (that's the slow loop, async, off
the trading path). It only turns an already-produced sentiment reading into a bounded multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PairSentiment:
    """One pair's slow-loop reading. ``sentiment`` ∈ [-1, 1] (negative = bearish), ``confidence``
    ∈ [0, 1]. Out-of-range values are clamped at use (fail-safe), never trusted blindly."""

    sentiment: float
    confidence: float
    rationale: str = ""


@dataclass(frozen=True)
class SentimentState:
    """The slow-loop output contract (`sentiment_state.json`, §3)."""

    schema_version: int
    generated_at: datetime
    ttl_seconds: float
    pairs: dict[str, PairSentiment] = field(default_factory=dict)

    def is_fresh(self, now: datetime) -> bool:
        """Fresh while age ≤ TTL. At exactly TTL it is still considered fresh."""
        return (now - self.generated_at).total_seconds() <= self.ttl_seconds


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def size_haircut(
    state: SentimentState | None, pair: str, *, now: datetime, floor: float = 1.0
) -> float:
    """Return the bounded size multiplier in ``[floor, 1.0]`` for ``pair`` (see module docstring).

    ``floor`` must be in [0.5, 1.0] (§3). Returns 1.0 (neutral) whenever the slow loop has nothing
    trustworthy to say: no state, stale state, or no reading for this pair.
    """
    if not (0.5 <= floor <= 1.0):
        raise ValueError(f"sentiment floor must be in [0.5, 1.0] (§3), got {floor}")
    if floor == 1.0:
        return 1.0  # feature disabled: strict no-op
    if state is None or not state.is_fresh(now):
        return 1.0
    reading = state.pairs.get(pair)
    if reading is None:
        return 1.0

    s = _clamp(reading.sentiment, -1.0, 1.0)
    c = _clamp(reading.confidence, 0.0, 1.0)
    severity = max(0.0, -s) * c  # only bearish sentiment shrinks; ∈ [0, 1]
    multiplier = 1.0 - severity * (1.0 - floor)
    return _clamp(multiplier, floor, 1.0)
