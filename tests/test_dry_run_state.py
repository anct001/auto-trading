"""Tests for dry-run STATE correctness: daily-limit rollover (fix 1) + checkpoint/resume (fix 2).

These pin the two defects found in the 2026-07 re-audit:
  - "daily" loss limits were anchored once at process start, so on a multi-day run a cumulative
    −2% silently locked the bot out of entries forever (§4 semantics broken);
  - the paper account lived only in RAM, so a ≥30-day P3 run could not survive a restart (§9).
"""
from __future__ import annotations

import json

import pandas as pd

from backtest.runner import Costs
from src.dry_run import PaperAccount, PaperBrokerExchange, ReplayFeed, build_runner
from src.events.log import EventLog
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.risk.types import Position
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


def _history(n, start_ms=0):
    return [[start_ms + i * HOUR_MS, 10000 + i, 10100 + i, 9900 + i, 10000 + i, 10.0]
            for i in range(n)]


def _runner(tmp_path, rows=None, checkpoint=None):
    return build_runner(
        data_exchange=ReplayFeed(rows if rows is not None else _history(60)),
        paper_exchange=PaperBrokerExchange(),
        events=EventLog(str(tmp_path / "ev.jsonl")), strategy=HoldStrategy(),
        cfg=_cfg(), costs=_COSTS, market=_MARKET, account=PaperAccount(cash=10_000.0),
        pair=PAIR, timeframe="1h", atr_period=3, checkpoint_path=checkpoint)


# ---- fix 1: daily rollover -------------------------------------------------------------------

def test_roll_day_reanchors_daily_limits():
    acct = PaperAccount(cash=9_000.0, day_start_equity=10_000.0)
    acct.day_anchor = "1970-01-01"
    # after an overnight −10%: rolling to the new UTC day re-anchors "today" at current equity
    rolled, prev = acct.roll_day("1970-01-02", {})
    assert rolled and prev == "1970-01-01"
    assert acct.day_start_equity == 9_000.0 and acct.day_anchor == "1970-01-02"
    assert acct.to_state({}).day_return() == 0.0          # the day starts flat again
    # same day → no-op
    assert acct.roll_day("1970-01-02", {}) == (False, "1970-01-02")


def test_replay_rolls_days_at_utc_midnight(tmp_path):
    # 60 hourly candles from the epoch cross two UTC midnights (t=24h and t=48h)
    runner = _runner(tmp_path)
    runner.replay(warmup=5)
    assert runner.account.day_anchor == "1970-01-03"       # anchored to the last candle's UTC day
    rolls = [e for e in runner.loop.events.read_all() if e.type == "DayRolled"]
    assert len(rolls) >= 3                                 # initial anchor + 2 midnights
    # a DayRolled event carries the audit trail
    assert rolls[-1].payload["date"] == "1970-01-03"
    assert "day_start_equity" in rolls[-1].payload


# ---- fix 2: checkpoint / resume --------------------------------------------------------------

def test_checkpoint_roundtrip_restores_everything(tmp_path):
    ck = tmp_path / "state.json"
    a = _runner(tmp_path, checkpoint=str(ck))
    # simulate lived state: a position, realized pnl, peaks, history, trades, ticks
    a.account.apply_buy(PAIR, 0.05, 10_000.0, 0.001)
    a.account.realized_pnl = 123.0
    a.account.peak_equity = 10_500.0
    a.account.day_start_equity = 9_900.0
    a.account.day_anchor = "1970-01-02"
    a._equity_history.append({"t": "t1", "equity": 10_100.0, "exposure": 0.5})
    a._trades.append({"exit_time": "t0", "pair": PAIR, "qty": 0.01,
                      "entry_price": 9_000.0, "exit_price": 9_100.0, "return": 0.011, "pnl": 1.0})
    a._tick_count = 42
    a.save_checkpoint()
    assert ck.exists()

    b = _runner(tmp_path, checkpoint=str(ck))
    assert b.load_checkpoint() is True
    assert b.account.cash == a.account.cash
    assert b.account.positions[PAIR] == Position(PAIR, 0.05, 10_000.0)
    assert b.account.realized_pnl == 123.0
    assert b.account.peak_equity == 10_500.0               # drawdown kill keeps its memory
    assert b.account.day_start_equity == 9_900.0 and b.account.day_anchor == "1970-01-02"
    assert b.equity_history() == a.equity_history()
    assert b.trades() == a.trades()
    assert b.health()["tick_count"] == 42


def test_checkpoint_corrupt_file_fails_soft(tmp_path):
    ck = tmp_path / "state.json"
    ck.write_text("{not json", encoding="utf-8")
    r = _runner(tmp_path, checkpoint=str(ck))
    assert r.load_checkpoint() is False                    # fresh start, no crash
    assert r.account.cash == 10_000.0
    assert ck.with_suffix(".json.corrupt").exists()        # evidence kept for the operator


def test_run_writes_checkpoint_each_tick(tmp_path):
    ck = tmp_path / "state.json"
    r = _runner(tmp_path, checkpoint=str(ck))
    r.run(iterations=1, poll_seconds=0.0)
    assert ck.exists()
    data = json.loads(ck.read_text(encoding="utf-8"))
    assert data["tick_count"] == 1 and data["account"]["cash"] == 10_000.0
