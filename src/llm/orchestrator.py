"""src/llm/orchestrator.py — §3 slow loop: classify sentiment, write the state file.

OUT of the trading path (Inv. 1/2). Runs async on a minutes cadence in its own process; its only
side effect the fast loop sees is the `sentiment_state.json` file (read there as an optional,
stale-checked, non-vetoing input). This module orchestrates one *cycle*: for each pair, ask an
injected classifier for a sentiment reading, clamp/validate it, and persist the assembled state.

Robustness is the point: a single pair's LLM failure or out-of-range output must never poison the
others or crash the cycle — that pair is simply omitted, and the fast loop treats a missing pair
as neutral. The concrete LLM backend (Ollama) is injected via the ``SentimentClassifier``
protocol, so this is fully testable offline and the backend is swappable.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from src.llm.sentiment import PairSentiment, SentimentState
from src.llm.state_io import write_sentiment_state

_MAX_RATIONALE = 500  # keep the audit/state file bounded (context-token-efficiency)


class SentimentClassifier(Protocol):
    """The slice of an LLM backend the slow loop needs (Ollama implements this)."""

    def classify(self, pair: str, documents: list[str]) -> PairSentiment: ...


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def run_sentiment_cycle(
    *,
    llm: SentimentClassifier,
    documents_by_pair: dict[str, list[str]],
    ttl_seconds: float,
    now: datetime,
    out_path: str | Path | None = None,
    schema_version: int = 1,
) -> SentimentState:
    """Run one slow-loop cycle and return (optionally persist) the resulting SentimentState.

    Per-pair failures are swallowed (the pair is omitted). Readings are clamped to the documented
    ranges before they are trusted. If ``out_path`` is given, the state is written there.
    """
    pairs: dict[str, PairSentiment] = {}
    for pair, documents in documents_by_pair.items():
        try:
            reading = llm.classify(pair, list(documents))
        except Exception:
            # one bad pair must not break the cycle; missing pair → neutral downstream
            continue
        pairs[pair] = PairSentiment(
            sentiment=_clamp(float(reading.sentiment), -1.0, 1.0),
            confidence=_clamp(float(reading.confidence), 0.0, 1.0),
            rationale=str(reading.rationale)[:_MAX_RATIONALE],
        )

    state = SentimentState(
        schema_version=schema_version, generated_at=now, ttl_seconds=ttl_seconds, pairs=pairs
    )
    if out_path is not None:
        write_sentiment_state(out_path, state)
    return state
