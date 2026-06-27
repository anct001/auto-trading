"""Tests for src/ui/dashboard/model.py — operator dashboard read model (§12).

Read-only derivation of operator status from portfolio state + risk config + event log. Pins the
limit/exposure gauges, kill-switch + slow-loop freshness surfacing, and the decision-log "why".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.events.log import EventLog
from src.llm.sentiment import PairSentiment, SentimentState
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.types import PortfolioState, Position
from src.ui.dashboard.model import build_dashboard

PAIR = "BTC/JPY"
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    }
    return RiskConfig.from_dict(d)


def test_healthy_account_no_breaches():
    state = PortfolioState(equity=1_000_000.0, peak_equity=1_000_000.0,
                           day_start_equity=1_000_000.0, quote_price=1.0,
                           positions={PAIR: Position(PAIR, 0.001, 9_000_000.0)})
    d = build_dashboard(state=state, cfg=_cfg(), prices={PAIR: 10_000_000.0}, now=NOW)
    assert d.equity == 1_000_000.0
    assert not d.daily_soft_breached and not d.daily_hard_breached
    assert not d.killswitch_breach
    assert d.positions[0].pair == PAIR
    assert d.positions[0].unrealized_pnl == (10_000_000.0 - 9_000_000.0) * 0.001
    assert d.gross_exposure_pct > 0 and not d.killswitch_engaged


def test_daily_and_drawdown_breaches_flagged():
    # down 5% on the day and 13% from peak → soft+hard daily breach and kill-switch drawdown breach
    state = PortfolioState(equity=870_000.0, peak_equity=1_000_000.0,
                           day_start_equity=950_000.0, quote_price=1.0)
    d = build_dashboard(state=state, cfg=_cfg(), prices={}, now=NOW)
    assert d.daily_soft_breached and d.daily_hard_breached
    assert d.killswitch_breach


def test_killswitch_status_surfaced():
    ks = KillSwitch()
    ks.manual_kill()
    state = PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0)
    d = build_dashboard(state=state, cfg=_cfg(), prices={}, killswitch=ks, now=NOW)
    assert d.killswitch_engaged and d.killswitch_reason == "manual_kill"


def test_sentiment_freshness_surfaced():
    state = PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0)
    fresh = SentimentState(1, NOW, 600, {PAIR: PairSentiment(-0.2, 0.7)})
    stale = SentimentState(1, NOW - timedelta(seconds=601), 600, {})
    assert build_dashboard(state=state, cfg=_cfg(), prices={}, sentiment_state=fresh, now=NOW).sentiment_fresh is True
    assert build_dashboard(state=state, cfg=_cfg(), prices={}, sentiment_state=stale, now=NOW).sentiment_fresh is False
    assert build_dashboard(state=state, cfg=_cfg(), prices={}, now=NOW).sentiment_fresh is None


def test_equity_curve_passthrough():
    state = PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0)
    hist = [{"t": "2026-06-27T00:00:00+00:00", "equity": 100.0},
            {"t": "2026-06-27T01:00:00+00:00", "equity": 101.5}]
    d = build_dashboard(state=state, cfg=_cfg(), prices={}, now=NOW, equity_history=hist)
    assert d.equity_curve == hist
    # defaults to empty when not supplied
    assert build_dashboard(state=state, cfg=_cfg(), prices={}, now=NOW).equity_curve == []


def test_decision_log_from_events(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    log.append("SignalGenerated", {"pair": PAIR, "intent": "enter_long"})
    log.append("RiskRejected", {"pair": PAIR, "side": "buy", "reasons": ["per_asset_cap"]})
    state = PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0)
    d = build_dashboard(state=state, cfg=_cfg(), prices={}, events=log.read_all(), now=NOW)
    assert len(d.decision_log) == 2
    assert d.decision_log[0].type == "RiskRejected"  # newest first
    assert "per_asset_cap" in d.decision_log[0].detail
