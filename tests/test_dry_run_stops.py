"""Tests for simulated protective stops in the paper loop (audit fix #3).

The backtest already enforces the protective stop (audit #3, 2026-06); the LIVE paper loop and
replay recorded stops as "resting" but never triggered them — a crash-down candle sailed straight
through the supposed protection. These tests pin the simulation: a candle whose low crosses the
stop fills it (at the stop price, or at the open when the bar gaps through), closes the position,
records the trade + events, and stale stops are cancelled once the position is flat.
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs
from src.dry_run import PaperAccount, PaperBrokerExchange, ReplayFeed, build_runner
from src.events.log import EventLog
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.base import INTENT_HOLD

HOUR_MS = 3_600_000
PAIR = "BTC/USDT"
_MARKET = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)
_COSTS = Costs(taker_fee=0.00075, maker_fee=0.00075, slippage=0.0005)


def _cfg():
    return RiskConfig.from_dict({
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                                "futures_disabled": True, "reduce_only_on_exit": True},
    })


class HoldStrategy:
    target_regime = "any"

    def generate_signals(self, df):
        return pd.Series([INTENT_HOLD] * len(df), index=df.index, dtype="object")


def _rows(specs, start_ms=0):
    """specs = [(open, high, low, close), ...] hourly."""
    return [[start_ms + i * HOUR_MS, o, h, lo, c, 10.0] for i, (o, h, lo, c) in enumerate(specs)]


def _runner(tmp_path, rows):
    paper = PaperBrokerExchange()
    r = build_runner(
        data_exchange=ReplayFeed(rows), paper_exchange=paper,
        events=EventLog(str(tmp_path / "ev.jsonl")), strategy=HoldStrategy(),
        cfg=_cfg(), costs=_COSTS, market=_MARKET, account=PaperAccount(cash=10_000.0),
        pair=PAIR, timeframe="1h", atr_period=3)
    return r, paper


def _arm(runner, paper, qty=0.02, entry=10_000.0, stop=9_500.0):
    """Open a paper position and rest a reduceOnly stop under it (as the loop would at fill)."""
    runner.account.apply_buy(PAIR, qty, entry, _COSTS.taker_fee)
    paper.create_order(PAIR, "stop", "sell", qty, stop,
                       {"reduceOnly": True, "stopPrice": stop, "clientOrderId": "stop-1"})


def test_stop_fills_when_low_crosses(tmp_path):
    # candle: open above stop, low pierces it -> stop fills AT the stop price
    rows = _rows([(10_000, 10_050, 9_950, 10_000)] * 6 + [(9_800, 9_820, 9_300, 9_400)])
    runner, paper = _runner(tmp_path, rows)
    _arm(runner, paper, stop=9_500.0)
    runner.replay(warmup=6)                                # process the crash candle
    assert PAIR not in runner.account.positions            # position closed by the stop
    trades = runner.trades()
    assert len(trades) == 1 and trades[0]["exit_price"] == 9_500.0
    types = [e.type for e in runner.loop.events.read_all()]
    assert "StopTriggered" in types and "FillReceived" in types
    assert paper.resting_stops == {}                       # consumed


def test_stop_gap_down_fills_at_open(tmp_path):
    # the bar OPENS below the stop (gap) -> realistic fill at the open, not the stop price
    rows = _rows([(10_000, 10_050, 9_950, 10_000)] * 6 + [(9_200, 9_250, 9_100, 9_150)])
    runner, paper = _runner(tmp_path, rows)
    _arm(runner, paper, stop=9_500.0)
    runner.replay(warmup=6)
    assert runner.trades()[0]["exit_price"] == 9_200.0     # gapped through -> open

    ev = [e for e in runner.loop.events.read_all() if e.type == "StopTriggered"][0]
    assert ev.payload["gapped"] is True


def test_stop_does_not_fire_above_low(tmp_path):
    rows = _rows([(10_000, 10_050, 9_950, 10_000)] * 7)    # low 9950 never reaches 9500
    runner, paper = _runner(tmp_path, rows)
    _arm(runner, paper, stop=9_500.0)
    runner.replay(warmup=6)
    assert PAIR in runner.account.positions                # still held
    assert "stop-1" in paper.resting_stops                 # still resting


def test_stale_stop_cancelled_when_position_flat(tmp_path):
    # position was closed by other means (e.g. strategy exit) -> the leftover stop must be
    # cancelled, NOT fire later against a nonexistent position
    rows = _rows([(10_000, 10_050, 9_950, 10_000)] * 6 + [(9_800, 9_820, 9_300, 9_400)])
    runner, paper = _runner(tmp_path, rows)
    _arm(runner, paper)
    runner.account.apply_sell(PAIR, 0.02, 10_000.0, _COSTS.taker_fee)   # flat before the crash
    runner.replay(warmup=6)
    assert paper.resting_stops == {}                       # cancelled, no fill, no trade
    assert runner.trades() == []
    assert "StopTriggered" not in [e.type for e in runner.loop.events.read_all()]


def test_stop_realizes_the_loss_in_the_account(tmp_path):
    rows = _rows([(10_000, 10_050, 9_950, 10_000)] * 6 + [(9_800, 9_820, 9_300, 9_400)])
    runner, paper = _runner(tmp_path, rows)
    _arm(runner, paper, qty=0.02, entry=10_000.0, stop=9_500.0)
    cash_before = runner.account.cash
    runner.replay(warmup=6)
    # sell proceeds landed in cash and the realized loss ~= (9500-10000)*0.02 minus fees
    assert runner.account.cash > cash_before
    assert runner.account.realized_pnl < 0
    assert abs(runner.account.realized_pnl - ((9_500.0 - 10_000.0) * 0.02)) < 5.0
