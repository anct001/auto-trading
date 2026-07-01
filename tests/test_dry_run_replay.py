"""Tests for the historical REPLAY mode of the paper dry-run (src/dry_run.py).

Replaying past data must use the SAME order path as a live run (loop → risk → broker → stops →
event log), be causal (a tick at time t sees only candles closed by t — no look-ahead §8.4), and
never touch the network. The ReplayFeed is driven by a movable 'now'; run_once(now_ms=t) steps it.
"""
from __future__ import annotations

import pandas as pd

from backtest.runner import Costs
from src.dry_run import PaperAccount, PaperBrokerExchange, ReplayFeed, build_runner
from src.events.log import EventLog
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

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


def _history(n=80):
    # a gentle uptrend so ATR sizing is feasible and an EMA-style entry can fill
    return [[i * HOUR_MS, 10000 + i * 5, 10100 + i * 5, 9900 + i * 5, 10000 + i * 5, 10.0]
            for i in range(n)]


# ---- ReplayFeed unit behaviour -------------------------------------------------------------

def test_replayfeed_is_bounded_by_now_and_mirrors_venue_semantics():
    rf = ReplayFeed(_history(10))
    rf.set_now(5 * HOUR_MS)                       # only candles with ts < 5h exist "now"
    latest = rf.fetch_ohlcv(PAIR, since=None, limit=3)
    assert [r[0] for r in latest] == [2 * HOUR_MS, 3 * HOUR_MS, 4 * HOUR_MS]  # most-recent 3 < now
    paged = rf.fetch_ohlcv(PAIR, since=1 * HOUR_MS, limit=10)
    assert [r[0] for r in paged] == [1 * HOUR_MS, 2 * HOUR_MS, 3 * HOUR_MS, 4 * HOUR_MS]  # from since, < now
    assert rf.milliseconds() == 5 * HOUR_MS


def test_replayfeed_from_frame_roundtrips_timestamps():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([0, HOUR_MS], unit="ms", utc=True),
        "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [1.0, 2.0], "volume": [9.0, 9.0]})
    rf = ReplayFeed.from_frame(df)
    assert [r[0] for r in rf.all_rows()] == [0, HOUR_MS]


# ---- end-to-end replay ---------------------------------------------------------------------

class _ScriptStrategy:
    """Enter early, exit later — exercises a full round-trip through the replay."""
    target_regime = "any"

    def __init__(self, enter_before_ts, exit_after_ts):
        self._enter_before, self._exit_after = enter_before_ts, exit_after_ts

    def generate_signals(self, df):
        last_ts = df["timestamp"].iloc[-1].value // 1_000_000
        intent = INTENT_HOLD
        if last_ts <= self._enter_before:
            intent = INTENT_ENTER_LONG
        elif last_ts >= self._exit_after:
            intent = INTENT_EXIT
        return pd.Series([INTENT_HOLD] * (len(df) - 1) + [intent], index=df.index, dtype="object")


def _runner(strategy):
    return build_runner(
        data_exchange=ReplayFeed(_history(80)), paper_exchange=PaperBrokerExchange(),
        events=EventLog(":memory:" if False else "/tmp/replay_test_events.jsonl"),
        strategy=strategy, cfg=_cfg(), costs=_COSTS, market=_MARKET,
        account=PaperAccount(cash=10_000.0), pair=PAIR, timeframe="1h", atr_period=3,
    )


def test_replay_steps_through_history_and_trades(tmp_path):
    runner = build_runner(
        data_exchange=ReplayFeed(_history(80)), paper_exchange=PaperBrokerExchange(),
        events=EventLog(str(tmp_path / "ev.jsonl")),
        strategy=_ScriptStrategy(enter_before_ts=50 * HOUR_MS, exit_after_ts=70 * HOUR_MS),
        cfg=_cfg(), costs=_COSTS, market=_MARKET, account=PaperAccount(cash=10_000.0),
        pair=PAIR, timeframe="1h", atr_period=3)
    health = runner.replay(warmup=40)
    assert health["tick_count"] == 80 - 40            # one tick per remaining bar
    assert len(runner.equity_history()) == 80 - 40    # equity marked each tick
    assert len(runner.trades()) >= 1                  # a full round-trip completed


def test_replay_is_causal_result_independent_of_future_bars(tmp_path):
    # replaying only the first K bars must give the SAME per-tick equity as the full replay's first K
    def run(n_bars):
        r = build_runner(
            data_exchange=ReplayFeed(_history(80)[:n_bars]), paper_exchange=PaperBrokerExchange(),
            events=EventLog(str(tmp_path / f"ev{n_bars}.jsonl")),
            strategy=_ScriptStrategy(enter_before_ts=50 * HOUR_MS, exit_after_ts=70 * HOUR_MS),
            cfg=_cfg(), costs=_COSTS, market=_MARKET, account=PaperAccount(cash=10_000.0),
            pair=PAIR, timeframe="1h", atr_period=3)
        r.replay(warmup=40)
        return [round(e["equity"], 6) for e in r.equity_history()]

    short, full = run(60), run(80)
    assert short == full[: len(short)]  # future bars never change a past tick's outcome (no look-ahead)
