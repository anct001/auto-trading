"""Tests for src/ui/api.py — transport-agnostic JSON service layer (§12).

The read model + risk preview turned into JSON-safe dicts a FastAPI/SSE handler can return as-is.
No web dependency yet — this is the seam the transport will call.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.risk.config import RiskConfig
from src.risk import engine
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import PortfolioState, Position
from src.ui.api import dashboard_payload, preview_payload

PAIR = "BTC/JPY"
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
_GOOD = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
         "futures_disabled": True, "reduce_only_on_exit": True}


def _cfg():
    return RiskConfig.from_dict({
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": dict(_GOOD),
    })


def _state():
    return PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0,
                          positions={PAIR: Position(PAIR, 0.001, 9_000_000.0)})


def test_dashboard_payload_is_json_serializable():
    p = dashboard_payload(state=_state(), cfg=_cfg(), prices={PAIR: 10_000_000.0}, now=NOW)
    assert json.dumps(p)  # no datetimes/dataclasses leak through
    assert p["equity"] == 1e6
    assert isinstance(p["positions"], list) and p["positions"][0]["pair"] == PAIR
    assert "killswitch_engaged" in p and "decision_log" in p


def test_killswitch_engage_and_rearm_roundtrip():
    from src.ui.api import killswitch_status, rearm_kill, engage_kill
    ks = KillSwitch()
    s = engage_kill(ks)
    assert s["engaged"] is True and s["reason"] == "manual_kill" and s["allows_trading"] is False
    assert json.dumps(s)
    s2 = rearm_kill(ks, operator="anct")
    assert s2["engaged"] is False and s2["allows_trading"] is True
    assert killswitch_status(ks)["engaged"] is False


def test_rearm_requires_operator():
    from src.ui.api import rearm_kill
    ks = KillSwitch()
    engage = __import__("src.ui.api", fromlist=["engage_kill"]).engage_kill
    engage(ks)
    try:
        rearm_kill(ks, operator="")
        assert False, "empty operator should be rejected"
    except ValueError:
        pass


def test_preview_payload_carries_verdict_and_is_json():
    ctx = engine.RiskContext(prices={PAIR: 10_000_000.0}, exchange_state=dict(_GOOD),
                             killswitch=KillSwitch(),
                             market=MarketConstraints(min_notional=500.0, lot_step=1e-6, tick_size=1.0))
    p = preview_payload(pair=PAIR, side="buy", qty=0.001, price=10_000_000.0,
                        state=_state(), cfg=_cfg(), ctx=ctx)
    assert json.dumps(p)
    assert p["allowed"] is True and p["reasons"] == []
    assert "metrics" in p and p["order"]["source"] == "manual"
