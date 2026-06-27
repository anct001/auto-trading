"""src/llm/journal.py — §15 AI trade journal / hypothesis memory (P2).

Append-only, file-based memory of every strategy hypothesis tried and its outcome, read at the
start of each research cycle. Off the trading path. Its load-bearing job for §5 is an honest
**count of hypotheses tried**: that count is the denominator of the multiple-testing correction
(`backtest/multiple_testing.py`), so losing a trial would inflate apparent edge. Hence append-only
JSONL — same discipline as the event log: the file only grows, no update/delete API.

Record statuses:
  - ``proposed``  — the LLM suggested it; a human has not yet approved it for backtest (Inv. 1/8).
  - ``validated`` — backtested + walk-forward passed (a TRIAL).
  - ``rejected``  — backtested but failed (also a TRIAL — it still counts against §5).
Only ``validated``/``rejected`` count as trials; ``proposed`` does not (it was never tested).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROPOSED = "proposed"
VALIDATED = "validated"
REJECTED = "rejected"
_TRIAL_STATUSES = frozenset({VALIDATED, REJECTED})


@dataclass(frozen=True)
class HypothesisRecord:
    name: str
    params: dict[str, Any]
    target_regime: str
    generated_by: str        # e.g. "llm:llama3.1" or "human"
    proposed_at: datetime
    status: str              # proposed | validated | rejected
    outcome: dict[str, Any] = field(default_factory=dict)  # metrics: sharpe, trade_count, ...

    def is_trial(self) -> bool:
        return self.status in _TRIAL_STATUSES


class HypothesisJournal:
    """Append-only JSONL journal of hypothesis trials. No update/delete by construction."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, rec: HypothesisRecord) -> HypothesisRecord:
        line = {
            "name": rec.name,
            "params": rec.params,
            "target_regime": rec.target_regime,
            "generated_by": rec.generated_by,
            "proposed_at": rec.proposed_at.isoformat(),
            "status": rec.status,
            "outcome": rec.outcome,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        return rec

    def all(self) -> list[HypothesisRecord]:
        if not self.path.exists():
            return []
        out: list[HypothesisRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(HypothesisRecord(
                name=d["name"], params=dict(d.get("params", {})),
                target_regime=d.get("target_regime", ""), generated_by=d.get("generated_by", ""),
                proposed_at=datetime.fromisoformat(d["proposed_at"]),
                status=d["status"], outcome=dict(d.get("outcome", {})),
            ))
        return out

    def count_trials(self) -> int:
        """Number of hypotheses actually put through backtest — the §5 correction denominator."""
        return sum(1 for r in self.all() if r.is_trial())

    def validated(self) -> list[HypothesisRecord]:
        return [r for r in self.all() if r.status == VALIDATED]
