"""Tests for src/ui/orders_panel.py — §12 order & trade panel read model (read-only).

Derives order attempts (with source + approve/reject reasons), submissions, and fills from the
append-only event log. Pure; never trades.
"""
from __future__ import annotations

from src.events.log import (
    FILL_RECEIVED,
    ORDER_SUBMITTED,
    RISK_PASSED,
    RISK_REJECTED,
    SIGNAL_GENERATED,
    EventLog,
)
from src.ui.orders_panel import build_order_trade_panel

PAIR = "BTC/JPY"


def _log(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    log.append(SIGNAL_GENERATED, {"pair": PAIR, "intent": "enter_long"})
    log.append(RISK_PASSED, {"pair": PAIR, "side": "buy", "qty": 0.01, "price": 1e7,
                             "source": "strategy", "reasons": []})
    log.append(ORDER_SUBMITTED, {"pair": PAIR, "side": "buy", "qty": 0.01, "client_order_id": "c1"})
    log.append(FILL_RECEIVED, {"pair": PAIR, "filled": 0.01})
    log.append(RISK_REJECTED, {"pair": PAIR, "side": "buy", "qty": 9.0, "price": 1e7,
                               "source": "manual", "reasons": ["per_asset_cap"]})
    return log


def test_attempts_carry_source_and_verdict(tmp_path):
    panel = build_order_trade_panel(_log(tmp_path).read_all())
    attempts = panel["attempts"]
    assert len(attempts) == 2
    # newest first → the rejected manual attempt leads
    assert attempts[0]["approved"] is False and attempts[0]["source"] == "manual"
    assert "per_asset_cap" in attempts[0]["reasons"]
    assert attempts[1]["approved"] is True and attempts[1]["source"] == "strategy"


def test_submissions_and_fills(tmp_path):
    panel = build_order_trade_panel(_log(tmp_path).read_all())
    assert panel["submitted"][0]["client_order_id"] == "c1"
    assert panel["fills"][0]["filled"] == 0.01


def test_empty_log():
    panel = build_order_trade_panel([])
    assert panel == {"attempts": [], "submitted": [], "fills": []}


def test_limit_applies(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    for i in range(30):
        log.append(RISK_PASSED, {"pair": PAIR, "side": "buy", "qty": 0.01, "price": 1e7,
                                 "source": "strategy", "reasons": []})
    panel = build_order_trade_panel(log.read_all(), limit=10)
    assert len(panel["attempts"]) == 10
