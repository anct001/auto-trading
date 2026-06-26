"""Tests for risk-sized backtesting — backtest sizes through the SAME risk path as live.

Integration (PLAN_RISK done-criterion): when a RiskSizing policy is supplied, the backtest sizes
each entry with risk.sizing.compute_size — inverse-ATR, per-trade-capped, and SKIPPED when
sub-minimum — instead of the full-equity placeholder. One sizing path, no train-serve skew.
"""
from __future__ import annotations

import math

import pandas as pd

from backtest.runner import Costs, RiskSizing, run_backtest
from src.features.indicators import atr as atr_fn
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints, compute_size
from src.risk.types import PortfolioState
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _cfg(per_trade=0.5):
    d = {
        "per_trade_risk_pct": per_trade, "position_sizing": "inverse_atr",
        "max_fractional_kelly": 0.5, "max_concurrent_positions": 3, "gross_exposure_pct": 100,
        "per_asset_cap_pct": 25, "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    }
    return RiskConfig.from_dict(d)


class FakeStrategy:
    target_regime = "any"

    def __init__(self, intents):
        self._intents = intents

    def generate_signals(self, df):
        return pd.Series(self._intents[: len(df)], index=df.index, dtype="object")


def _frame(n=12):
    rows = [[T0 + i * HOUR, 100 + i, 102 + i, 98 + i, 100 + i, 10.0] for i in range(n)]
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


# enter signal at index 3 → fills at bar 4 open; exit signal at index 8 → fills at bar 9 open
_INTENTS = [INTENT_HOLD] * 3 + [INTENT_ENTER_LONG] + [INTENT_HOLD] * 4 + [INTENT_EXIT] + [INTENT_HOLD] * 3
_COSTS = Costs(taker_fee=0.00075, maker_fee=0.00075, slippage=0.0005)
_MARKET = MarketConstraints(min_notional=10.0, lot_step=0.0001, tick_size=0.01)


def test_risk_sized_qty_matches_compute_size():
    df = _frame()
    risk = RiskSizing(cfg=_cfg(), market=_MARKET, pair="BTC/USDT", atr_period=3, atr_stop_mult=2.0)
    res = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0, risk=risk)

    entry_bar = 4
    atr_at_entry = float(atr_fn(df, 3).iloc[entry_bar])
    expected = compute_size(
        pair="BTC/USDT", price=float(df["open"].iloc[entry_bar]), atr=atr_at_entry,
        state=PortfolioState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0),
        cfg=_cfg(), market=_MARKET, atr_stop_mult=2.0,
    )
    assert len(res.trades) == 1
    assert math.isclose(res.trades.iloc[0]["qty"], expected.qty, rel_tol=1e-9)


def test_risk_sizing_deploys_less_than_full_equity():
    df = _frame()
    full = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0)
    risk = RiskSizing(cfg=_cfg(), market=_MARKET, pair="BTC/USDT", atr_period=3)
    risked = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0, risk=risk)
    assert risked.trades.iloc[0]["qty"] < full.trades.iloc[0]["qty"]


def test_risk_position_respects_per_trade_budget():
    df = _frame()
    risk = RiskSizing(cfg=_cfg(0.5), market=_MARKET, pair="BTC/USDT", atr_period=3, atr_stop_mult=2.0)
    res = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0, risk=risk)
    qty = res.trades.iloc[0]["qty"]
    stop_distance = 2.0 * float(atr_fn(df, 3).iloc[4])
    assert qty * stop_distance <= 10000.0 * 0.005 + 1e-9


def test_sub_minimum_entry_is_skipped():
    df = _frame()
    big_min = MarketConstraints(min_notional=1e9, lot_step=0.0001, tick_size=0.01)
    risk = RiskSizing(cfg=_cfg(), market=big_min, pair="BTC/USDT", atr_period=3)
    res = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=100.0, risk=risk)
    assert res.trades.empty  # entry skipped, never rounded up to meet the minimum


def test_risk_backtest_is_reproducible():
    df = _frame()
    risk = RiskSizing(cfg=_cfg(), market=_MARKET, pair="BTC/USDT", atr_period=3)
    a = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0, risk=risk)
    b = run_backtest(df, FakeStrategy(_INTENTS), costs=_COSTS, initial_equity=10000.0, risk=risk)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    pd.testing.assert_series_equal(a.equity_curve, b.equity_curve)
