"""Tests for backtest/scenario.py — adversarial stress scenarios (§15).

The point a pro desk cares about: under a flash crash / gap, the **protective stop must cap the
realized loss** near the modeled per-trade risk — not the full market move. These pin that.
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs, RiskSizing
from backtest.scenario import flash_crash, gap_down, run_stress, vol_spike
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.base import INTENT_ENTER_LONG, INTENT_HOLD

_MARKET = MarketConstraints(min_notional=1.0, lot_step=1e-6, tick_size=0.01)


def _cfg():
    return RiskConfig.from_dict({
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    })


class EnterEarly:
    """Enters long near the start and holds — so a later shock hits an open position."""

    def generate_signals(self, df):
        s = pd.Series(INTENT_HOLD, index=df.index, dtype="object")
        if len(df) > 2:
            s.iloc[2] = INTENT_ENTER_LONG
        return s


def _risk():
    return RiskSizing(cfg=_cfg(), market=_MARKET, pair="BTC/USDT", atr_period=3, atr_stop_mult=2.0)


def test_scenario_generators_shape_and_shock():
    fc = flash_crash(n=60, crash_at=40, drop=0.30)
    assert list(fc.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert fc["close"].pct_change().min() <= -0.25      # the crash candle really dropped
    assert gap_down(n=40).low.min() > 0 and len(vol_spike(n=40)) == 40


def test_protective_stop_caps_flash_crash_loss():
    df = flash_crash(n=60, crash_at=40, drop=0.30)
    rep = run_stress(df, EnterEarly(), costs=Costs(), risk=_risk(), initial_equity=1_000_000.0)
    assert rep["trade_count"] >= 1
    assert rep["max_single_candle_drop"] <= -0.25        # market fell hard
    # but the stop bounded our realized worst trade well above the market drop
    assert rep["worst_trade_return"] > -0.15
    assert rep["max_drawdown"] > -0.15


def test_run_stress_reproducible():
    df = vol_spike(n=80)
    a = run_stress(df, EnterEarly(), costs=Costs(), risk=_risk())
    b = run_stress(df, EnterEarly(), costs=Costs(), risk=_risk())
    assert a == b
