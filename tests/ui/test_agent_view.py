"""Tests for src/ui/agent_view.py — the §12 agent-view overlay ("the valuable part").

Per coin, surface the system's own state over the market data so the operator sees *why* the
agent is or isn't acting: strategy signal, regime enablement, sentiment haircut, current position,
and the kill-switch. Read-only and deterministic — it reuses the same logic the loop uses.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.types import PortfolioState, Position
from src.strategy.base import INTENT_ENTER_LONG, INTENT_HOLD
from src.strategy.regime import RegimeState
from src.llm.sentiment import PairSentiment, SentimentState
from src.ui.agent_view import build_agent_view

PAIR = "BTC/JPY"
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
_GOOD = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
         "futures_disabled": True, "reduce_only_on_exit": True}


def _cfg(floor=1.0):
    return RiskConfig.from_dict({
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": dict(_GOOD), "sentiment_floor": floor,
    })


class FakeStrategy:
    def __init__(self, intent, target_regime="any"):
        self._i = intent
        self.target_regime = target_regime

    def generate_signals(self, df):
        return pd.Series([INTENT_HOLD] * (len(df) - 1) + [self._i], index=df.index, dtype="object")


def _df():
    return pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01"], utc=True), "close": [10_000_000.0]})


def _state(positions=None):
    return PortfolioState(equity=1e6, peak_equity=1e6, day_start_equity=1e6, quote_price=1.0,
                          positions=positions or {})


def _ctx(ks=None):
    return engine.RiskContext(prices={PAIR: 10_000_000.0}, exchange_state=dict(_GOOD),
                              killswitch=ks or KillSwitch())


def _view(strategy, state, ctx, **kw):
    return build_agent_view(pair=PAIR, df=_df(), strategy=strategy, state=state, cfg=_cfg(kw.pop("floor", 1.0)),
                            ctx=ctx, now=NOW, **kw)


def test_acting_when_enter_flat_regime_ok_ks_armed():
    v = _view(FakeStrategy(INTENT_ENTER_LONG), _state(), _ctx())
    assert v["signal"] == INTENT_ENTER_LONG
    assert v["acting"] is True and v["blocked_by"] == []


def test_hold_is_not_acting():
    v = _view(FakeStrategy(INTENT_HOLD), _state(), _ctx())
    assert v["acting"] is False and "intent:hold" in v["blocked_by"]


def test_regime_mismatch_blocks_and_is_explained():
    rs = RegimeState(1, NOW, 600, {PAIR: "range"})
    v = _view(FakeStrategy(INTENT_ENTER_LONG, "trend"), _state(), _ctx(), regime_state=rs)
    assert v["acting"] is False and "regime_disabled" in v["blocked_by"]
    assert v["regime_enabled"] is False


def test_killswitch_blocks():
    ks = KillSwitch()
    ks.manual_kill()
    v = _view(FakeStrategy(INTENT_ENTER_LONG), _state(), _ctx(ks=ks))
    assert v["acting"] is False and "killswitch" in v["blocked_by"]
    assert v["killswitch_engaged"] is True


def test_existing_position_surfaced_and_already_long():
    state = _state({PAIR: Position(PAIR, 0.02, 9_500_000.0)})
    v = _view(FakeStrategy(INTENT_ENTER_LONG), state, _ctx())
    assert v["position"]["qty"] == 0.02
    assert v["position"]["unrealized_pnl"] == (10_000_000.0 - 9_500_000.0) * 0.02
    assert "already_long" in v["blocked_by"] and v["acting"] is False


def test_sentiment_haircut_surfaced():
    bearish = SentimentState(1, NOW, 600, {PAIR: PairSentiment(-1.0, 1.0, "fear")})
    v = build_agent_view(pair=PAIR, df=_df(), strategy=FakeStrategy(INTENT_ENTER_LONG),
                         state=_state(), cfg=_cfg(0.5), ctx=_ctx(), now=NOW, sentiment_state=bearish)
    assert v["sentiment_haircut"] == 0.5 and v["sentiment_fresh"] is True
