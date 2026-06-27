"""Tests for src/dry_run.py — paper dry-run loop driver.

The DryRunner pulls closed candles from a (read-only) data exchange, runs one fast-loop tick,
routes orders to a SIMULATED paper exchange (never real capital — §10/P0), and updates a paper
account from the fills. Sleep/CLI are separate; run_once() is the testable unit. The data
exchange is faked so the test needs no network.
"""
from __future__ import annotations

import math

import pandas as pd

from backtest.runner import Costs
from src.dry_run import PaperAccount, PaperBrokerExchange, build_runner
from src.events.log import EventLog
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

HOUR_MS = 3_600_000
PAIR = "BTC/USDT"
_MARKET = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)
_COSTS = Costs(taker_fee=0.00075, maker_fee=0.00075, slippage=0.0005)


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                                "futures_disabled": True, "reduce_only_on_exit": True},
    }
    return RiskConfig.from_dict(d)


class FakeDataExchange:
    """Read-only OHLCV source. ±100 bar range so ATR sizing is feasible; timestamps in the past."""

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        return [[i * HOUR_MS, 10000 + i, 10100 + i, 9900 + i, 10000 + i, 10.0] for i in range(8)]


class ScriptStrategy:
    """Emits a scripted last-bar intent, advancing one step per tick."""

    target_regime = "any"

    def __init__(self, intents):
        self._intents = intents
        self._i = 0

    def generate_signals(self, df):
        intent = self._intents[min(self._i, len(self._intents) - 1)]
        self._i += 1
        return pd.Series([INTENT_HOLD] * (len(df) - 1) + [intent], index=df.index, dtype="object")


def _runner(strategy, events, paper, account):
    return build_runner(
        data_exchange=FakeDataExchange(), paper_exchange=paper, events=events, strategy=strategy,
        cfg=_cfg(), costs=_COSTS, market=_MARKET, account=account, pair=PAIR, timeframe="1h",
        atr_period=3, atr_stop_mult=2.0,
    )


class SparseBitbankLike:
    """Mimics bitbank: a single fetch returns only ~per_call recent candles, but `since`-based
    pagination can walk the full history (so prewarm can gather enough)."""

    def __init__(self, candles, per_call):
        self.candles = candles  # full ascending [ts,o,h,l,c,v]
        self.per_call = per_call

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        if since is None:
            return list(self.candles[-self.per_call:])           # latest "day" only
        page = [c for c in self.candles if c[0] >= since]
        return page[: self.per_call]

    def milliseconds(self):
        return self.candles[-1][0] + HOUR_MS


def _sparse_history(n=60):
    return [[i * HOUR_MS, 10000 + i, 10100 + i, 9900 + i, 10000 + i, 10.0] for i in range(n)]


def _sparse_runner(strategy, events, prewarm):
    return build_runner(
        data_exchange=SparseBitbankLike(_sparse_history(60), per_call=12),
        paper_exchange=PaperBrokerExchange(), events=events, strategy=strategy, cfg=_cfg(),
        costs=_COSTS, market=_MARKET, account=PaperAccount(cash=100000.0), pair=PAIR,
        timeframe="1h", atr_period=14, limit=50, prewarm=prewarm,
    )


def test_sparse_feed_without_prewarm_is_insufficient(tmp_path):
    # 12 candles/call < atr_period+2 (16) → the loop can't compute indicators
    r = _sparse_runner(ScriptStrategy([INTENT_HOLD]), EventLog(tmp_path / "e.jsonl"), prewarm=False)
    res = r.run_once(now_ms=60 * HOUR_MS)
    assert res is not None and res.reason == "insufficient_data"


def test_prewarm_gathers_enough_history_to_tick(tmp_path):
    r = _sparse_runner(ScriptStrategy([INTENT_HOLD]), EventLog(tmp_path / "e.jsonl"), prewarm=True)
    res = r.run_once(now_ms=60 * HOUR_MS)
    assert res is not None and res.reason != "insufficient_data"  # buffer pre-warmed via pagination
    assert len(r._buffer) >= 16
    # a second tick keeps the buffer deduped (doesn't double-count overlapping candles)
    n1 = len(r._buffer)
    r.run_once(now_ms=60 * HOUR_MS)
    assert len(r._buffer) == n1


def test_sentiment_state_path_wires_a_provider(tmp_path):
    paper, account = PaperBrokerExchange(), PaperAccount(cash=100000.0)
    events = EventLog(tmp_path / "e.jsonl")
    base = build_runner(
        data_exchange=FakeDataExchange(), paper_exchange=paper, events=events,
        strategy=ScriptStrategy([INTENT_HOLD]), cfg=_cfg(), costs=_COSTS, market=_MARKET,
        account=account, pair=PAIR, timeframe="1h",
    )
    assert base.loop.sentiment_provider is None  # off by default
    wired = build_runner(
        data_exchange=FakeDataExchange(), paper_exchange=paper, events=events,
        strategy=ScriptStrategy([INTENT_HOLD]), cfg=_cfg(), costs=_COSTS, market=_MARKET,
        account=account, pair=PAIR, timeframe="1h",
        sentiment_state_path=str(tmp_path / "sentiment_state.json"),
    )
    assert wired.loop.sentiment_provider is not None
    # absent file → provider returns None (fail-to-neutral), never raises
    assert wired.loop.sentiment_provider() is None


def test_enter_opens_a_paper_position_and_logs(tmp_path):
    paper, account = PaperBrokerExchange(), PaperAccount(cash=100000.0)
    events = EventLog(tmp_path / "e.jsonl")
    res = _runner(ScriptStrategy([INTENT_ENTER_LONG]), events, paper, account).run_once()
    assert res.action == "enter"
    assert PAIR in account.positions and account.positions[PAIR].qty > 0
    # a buy and a reduceOnly stop hit the PAPER exchange only
    assert any(o["side"] == "buy" for o in paper.orders)
    assert any(o["params"].get("reduceOnly") for o in paper.orders)
    assert len(events.read_all()) > 0


def test_round_trip_closes_position_and_charges_fees(tmp_path):
    paper, account = PaperBrokerExchange(), PaperAccount(cash=100000.0)
    runner = _runner(ScriptStrategy([INTENT_ENTER_LONG, INTENT_EXIT]), EventLog(tmp_path / "e.jsonl"),
                     paper, account)
    runner.run_once()                # enter
    res = runner.run_once()          # exit
    assert res.action == "exit"
    assert PAIR not in account.positions          # flat again
    assert account.cash < 100000.0                # fees (and price-flat) leave less cash


def test_hold_does_nothing(tmp_path):
    paper, account = PaperBrokerExchange(), PaperAccount(cash=100000.0)
    res = _runner(ScriptStrategy([INTENT_HOLD]), EventLog(tmp_path / "e.jsonl"), paper, account).run_once()
    assert res.action == "hold"
    assert paper.orders == [] and account.positions == {}


def test_paper_account_equity_tracks_cash_plus_position():
    account = PaperAccount(cash=1000.0)
    account.apply_buy(PAIR, qty=0.05, price=10000.0, taker_fee=0.0)  # spend 500
    eq = account.equity({PAIR: 10000.0})
    assert math.isclose(eq, 1000.0)  # 500 cash + 500 position value, no fee
