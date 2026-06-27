"""Tests for src/events/log.py — append-only, secret-redacted event log (§15, Invariant 6).

The audit substrate: an immutable, replayable stream of state transitions. Two hard properties:
append-only (no update/delete; the file only grows) and **secrets redacted before write**
(append-only means a leaked secret could never be deleted).
"""
from __future__ import annotations

from src.events import log as evlog
from src.events.log import EventLog


def test_append_and_read_roundtrip(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(evlog.MARKET_RECEIVED, {"pair": "BTC/USDT", "close": 100.0})
    log.append(evlog.SIGNAL_GENERATED, {"pair": "BTC/USDT", "intent": "enter_long"})
    events = log.read_all()
    assert [e.type for e in events] == [evlog.MARKET_RECEIVED, evlog.SIGNAL_GENERATED]
    assert events[0].payload["close"] == 100.0


def test_is_append_only_no_mutation_api():
    # the type exposes no way to update or delete a recorded event
    for forbidden in ("update", "delete", "remove", "edit", "truncate"):
        assert not hasattr(EventLog, forbidden)


def test_file_only_grows(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(evlog.MARKET_RECEIVED, {"pair": "BTC/USDT"})
    size1 = path.stat().st_size
    log.append(evlog.MARKET_RECEIVED, {"pair": "ETH/USDT"})
    assert path.stat().st_size > size1


def test_secrets_redacted_before_write(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append("ConnectionOpened", {"api_key": "AKIA-LIVE-123", "endpoint": "binance.jp"})
    # the secret must not be in the raw file at all (append-only → undeletable)
    raw = path.read_text()
    assert "AKIA-LIVE-123" not in raw
    assert "binance.jp" in raw  # non-secret preserved
    assert log.read_all()[0].payload["api_key"] == evlog.REDACTED


def test_redacts_credentials_embedded_in_a_url_value(tmp_path):
    # a secret in a VALUE (not a secret-named field) must still never hit the file
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append("ConnError", {"detail": "GET https://apiuser:s3cr3tpw@api.binance.jp/v3 failed"})
    raw = path.read_text()
    assert "s3cr3tpw" not in raw and "apiuser" not in raw
    assert "api.binance.jp" in raw  # host preserved


def test_redacts_api_key_like_value(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    # an AIza… (Google/Gemini-style) key accidentally captured in a free-text value
    log.append("Note", {"text": "configured key AIzaSyD1234567890abcdefABCDEFGHIJKLMNOP ok"})
    raw = path.read_text()
    assert "AIzaSyD1234567890abcdefABCDEFGHIJKLMNOP" not in raw
    assert evlog.REDACTED in log.read_all()[0].payload["text"]


def test_does_not_overredact_normal_text(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append("Note", {"text": "order filled at 100.5 on BTC/USDT"})
    assert log.read_all()[0].payload["text"] == "order filled at 100.5 on BTC/USDT"


def test_redacts_nested_and_in_lists(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("X", {"creds": {"secret": "s3cr3t", "token": "t0ken"}, "items": [{"password": "p"}]})
    payload = log.read_all()[0].payload
    assert payload["creds"]["secret"] == evlog.REDACTED
    assert payload["creds"]["token"] == evlog.REDACTED
    assert payload["items"][0]["password"] == evlog.REDACTED


def test_persists_across_instances(tmp_path):
    path = tmp_path / "events.jsonl"
    EventLog(path).append(evlog.ORDER_SUBMITTED, {"pair": "BTC/USDT"})
    # a fresh handle on the same immutable file replays history
    assert len(EventLog(path).read_all()) == 1


def test_log_risk_decision_records_reasons(tmp_path):
    from src.risk.types import Order, RiskDecision

    log = EventLog(tmp_path / "events.jsonl")
    order = Order("BTC/USDT", "buy", 1.0, 100.0, "manual")
    evlog.log_risk_decision(log, order, RiskDecision.reject("per_asset_cap", "quote_depeg"))
    event = log.read_all()[0]
    assert event.type == evlog.RISK_REJECTED
    assert event.payload["reasons"] == ["per_asset_cap", "quote_depeg"]
    assert event.payload["source"] == "manual"


def test_log_risk_decision_approved_type(tmp_path):
    from src.risk.types import Order, RiskDecision

    log = EventLog(tmp_path / "events.jsonl")
    order = Order("BTC/USDT", "buy", 1.0, 100.0, "strategy")
    evlog.log_risk_decision(log, order, RiskDecision.approve(order))
    assert log.read_all()[0].type == evlog.RISK_PASSED


def test_timestamp_recorded(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(evlog.MARKET_RECEIVED, {"pair": "BTC/USDT"}, timestamp="2026-06-26T00:00:00+00:00")
    assert log.read_all()[0].timestamp == "2026-06-26T00:00:00+00:00"
