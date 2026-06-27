"""Tests for src/ui/live.py + DryRunner snapshot — the operator UI over a LIVE dry-run.

After a paper entry, the live OperatorContext must reflect the real account (position + equity),
the preview must run the real engine, and the kill-switch must be the SAME one the loop consults.
Uses the dry-run fakes (no network).
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs
from src.dry_run import PaperAccount, PaperBrokerExchange, build_runner
from src.events.log import EventLog
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.base import INTENT_ENTER_LONG, INTENT_HOLD
from src.ui.live import build_live_context

HOUR_MS = 3_600_000
PAIR = "BTC/USDT"
_MARKET = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)
_COSTS = Costs(taker_fee=0.00075, maker_fee=0.00075, slippage=0.0005)


def _cfg():
    return RiskConfig.from_dict({
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                                "futures_disabled": True, "reduce_only_on_exit": True},
    })


class FakeData:
    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        return [[i * HOUR_MS, 10000 + i, 10100 + i, 9900 + i, 10000 + i, 10.0] for i in range(8)]


class Script:
    target_regime = "any"

    def __init__(self, intents):
        self._x, self._i = intents, 0

    def generate_signals(self, df):
        intent = self._x[min(self._i, len(self._x) - 1)]
        self._i += 1
        return pd.Series([INTENT_HOLD] * (len(df) - 1) + [intent], index=df.index, dtype="object")


def _runner(tmp_path, intents):
    return build_runner(
        data_exchange=FakeData(), paper_exchange=PaperBrokerExchange(),
        events=EventLog(tmp_path / "e.jsonl"), strategy=Script(intents), cfg=_cfg(), costs=_COSTS,
        market=_MARKET, account=PaperAccount(cash=100000.0), pair=PAIR, timeframe="1h",
        atr_period=3, atr_stop_mult=2.0,
    )


def test_dashboard_reflects_live_position_after_entry(tmp_path):
    runner = _runner(tmp_path, [INTENT_ENTER_LONG])
    runner.run_once()  # opens a paper long
    ctx = build_live_context(runner)
    d = ctx.dashboard()
    assert d["positions"] and d["positions"][0]["pair"] == PAIR
    assert d["equity"] > 0
    types = {e.type for e in runner.loop.events.read_all()}
    assert "OrderSubmitted" in types  # the live decision log feeds the dashboard
    assert d["decision_log"]


def test_snapshot_before_any_tick_is_flat(tmp_path):
    runner = _runner(tmp_path, [INTENT_HOLD])
    d = build_live_context(runner).dashboard()
    assert d["positions"] == [] and d["equity"] == 100000.0


def test_preview_runs_engine_over_live_state(tmp_path):
    runner = _runner(tmp_path, [INTENT_HOLD])
    runner.run_once()  # establishes a live mark price
    ctx = build_live_context(runner)
    prev = ctx.preview({"side": "buy", "qty": 0.001, "price": 10000.0})
    assert "allowed" in prev and prev["order"]["source"] == "manual"


def test_orders_panel_reflects_live_events(tmp_path):
    runner = _runner(tmp_path, [INTENT_ENTER_LONG])
    runner.run_once()  # logs RiskPassed/OrderSubmitted/FillReceived
    o = build_live_context(runner).orders()
    assert o["submitted"] and o["fills"]
    assert any(a["approved"] for a in o["attempts"])


def test_killswitch_is_shared_with_the_loop(tmp_path):
    runner = _runner(tmp_path, [INTENT_HOLD])
    ctx = build_live_context(runner)
    assert ctx.killswitch is runner.killswitch  # engaging from UI halts the live loop
    ctx.killswitch.manual_kill()
    assert build_live_context(runner).dashboard()["killswitch_engaged"] is True
