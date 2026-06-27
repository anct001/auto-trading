"""Tests for src/llm/journal.py — the AI trade journal / hypothesis memory (§15, P2).

Append-only file memory of every strategy hypothesis tried and its outcome. Its key job for §5
is an honest **count of hypotheses tried** — the denominator of the multiple-testing correction.
It must never silently lose a trial (that would deflate the correction and manufacture edge).
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.llm.journal import HypothesisJournal, HypothesisRecord

NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def _rec(name, *, status="rejected", sharpe=0.1, generated_by="llm:llama3.1"):
    return HypothesisRecord(
        name=name, params={"ema_fast": 12, "ema_slow": 26}, target_regime="trend",
        generated_by=generated_by, proposed_at=NOW, status=status,
        outcome={"sharpe": sharpe, "trade_count": 150},
    )


def test_record_then_read_roundtrips(tmp_path):
    j = HypothesisJournal(tmp_path / "journal.jsonl")
    j.record(_rec("h1"))
    j.record(_rec("h2", status="validated", sharpe=1.4))
    rows = j.all()
    assert [r.name for r in rows] == ["h1", "h2"]
    assert rows[1].status == "validated"
    assert rows[1].outcome["sharpe"] == 1.4
    assert rows[0].proposed_at == NOW


def test_append_only_across_instances(tmp_path):
    path = tmp_path / "journal.jsonl"
    HypothesisJournal(path).record(_rec("h1"))
    HypothesisJournal(path).record(_rec("h2"))  # new instance, same file
    assert len(HypothesisJournal(path).all()) == 2  # nothing overwritten


def test_count_trials_counts_tested_hypotheses(tmp_path):
    j = HypothesisJournal(tmp_path / "journal.jsonl")
    j.record(_rec("h1", status="rejected"))
    j.record(_rec("h2", status="validated"))
    j.record(_rec("h3", status="proposed"))  # proposed but NOT yet tested
    # the multiple-testing denominator counts hypotheses actually put through backtest
    assert j.count_trials() == 2


def test_validated_returns_only_passing(tmp_path):
    j = HypothesisJournal(tmp_path / "journal.jsonl")
    j.record(_rec("h1", status="rejected"))
    j.record(_rec("h2", status="validated", sharpe=1.6))
    assert [r.name for r in j.validated()] == ["h2"]


def test_empty_journal_is_zero(tmp_path):
    j = HypothesisJournal(tmp_path / "missing.jsonl")
    assert j.all() == [] and j.count_trials() == 0
