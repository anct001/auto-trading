"""src/llm/state_io.py — read/write sentiment_state.json (§3 slow-loop output contract).

The slow loop (async, off the trading path) WRITES this file; the fast loop READS it every tick.
Reading must never raise — any problem (absent, malformed, wrong schema, bad timestamp) resolves
to ``None`` so the haircut falls back to neutral (Inv. 2). The write is plain JSON with an ISO-8601
``generated_at`` so the file is human-auditable.

On-disk shape:
    {"schema_version": 1, "generated_at": "<ISO-8601>", "ttl_seconds": 600,
     "pairs": {"BTC/JPY": {"sentiment": -0.4, "confidence": 0.8, "rationale": "..."}}}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.llm.sentiment import PairSentiment, SentimentState


def write_sentiment_state(path: str | Path, state: SentimentState) -> None:
    """Write ``state`` as JSON (creating parent dirs). Used by the slow loop / tests."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": state.schema_version,
        "generated_at": state.generated_at.isoformat(),
        "ttl_seconds": state.ttl_seconds,
        "pairs": {
            pair: {"sentiment": r.sentiment, "confidence": r.confidence, "rationale": r.rationale}
            for pair, r in state.pairs.items()
        },
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_sentiment_state(path: str | Path) -> SentimentState | None:
    """Load the slow-loop sentiment state, or ``None`` on ANY problem (fail-to-neutral, Inv. 2)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(raw["generated_at"])
        ttl_seconds = float(raw["ttl_seconds"])
        pairs = {
            pair: PairSentiment(
                sentiment=float(d["sentiment"]),
                confidence=float(d["confidence"]),
                rationale=str(d.get("rationale", "")),
            )
            for pair, d in dict(raw["pairs"]).items()
        }
        return SentimentState(
            schema_version=int(raw["schema_version"]),
            generated_at=generated_at,
            ttl_seconds=ttl_seconds,
            pairs=pairs,
        )
    except (KeyError, ValueError, TypeError, OSError):
        # malformed / wrong schema / unreadable → neutral. Never crash the fast loop.
        return None
