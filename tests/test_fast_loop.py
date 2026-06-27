"""Integration tests for src/fast_loop.py — the §3 fast (deterministic) loop.

The keystone: one tick composes data → quality gate → single feature path → strategy → sizing →
risk gate → broker → exchange-side stop → event log. These tests use the REAL components
(engine, sizing, broker, stops, event log) and fake only the exchange — proving the pieces fit
together and that every order still passes the risk gate (Inv. 3/9).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.events.log import (
    FILL_RECEIVED,
    MARKET_RECEIVED,
    ORDER_SUBMITTED,
    RISK_PASSED,
    RISK_REJECTED,
    SIGNAL_GENERATED,
    EventLog,
)
from src.execution.broker import Broker
from src.execution.stops import StopManager
from src.fast_loop import SENTIMENT_HAIRCUT, FastLoop
from src.llm.sentiment import PairSentiment, SentimentState
from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Position, PortfolioState
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT, INTENT_HOLD

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
PAIR = "BTC/USDT"
_MARKET = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)
_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}


def _cfg():
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": dict(_GOOD_EXCHANGE),
    }
    return RiskConfig.from_dict(d)


class FakeExchange:
    def __init__(self):
        self.created = []

    def create_order(self, symbol, type, side, amount, price, params):
        self.created.append({"type": type, "side": side, "amount": amount, "params": dict(params)})
        return {"id": f"ex-{len(self.created)}", "status": "open", "filled": amount, "amount": amount}


class FakeStrategy:
    target_regime = "any"

    def __init__(self, last_intent):
        self._last = last_intent

    def generate_signals(self, df):
        seq = [INTENT_HOLD] * (len(df) - 1) + [self._last]
        return pd.Series(seq, index=df.index, dtype="object")


def _frame(n=8, close0=10000.0):
    # ~±100 bar range → ATR ≈ 200 so inverse-ATR sizing lands well under the 25% per-asset cap
    rows = [[T0 + i * HOUR, close0 + i, close0 + 100 + i, close0 - 100 + i, close0 + i, 10.0]
            for i in range(n)]
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _loop(exchange, events, strategy):
    markets = {PAIR: _MARKET}
    return FastLoop(
        broker=Broker(exchange, markets), stops=StopManager(exchange, markets), events=events,
        strategy=strategy, cfg=_cfg(), market=_MARKET, pair=PAIR, atr_period=3, atr_stop_mult=2.0,
    )


def _state(positions=None):
    return PortfolioState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0,
                          quote_price=1.0, positions=positions or {})


def _ctx(ks=None):
    return engine.RiskContext(prices={PAIR: 10000.0}, exchange_state=dict(_GOOD_EXCHANGE),
                              killswitch=ks or KillSwitch())


def test_enter_flow_sizes_validates_submits_and_attaches_stop(tmp_path):
    ex = FakeExchange()
    events = EventLog(tmp_path / "e.jsonl")
    res = _loop(ex, events, FakeStrategy(INTENT_ENTER_LONG)).tick(
        _frame(), _state(), _ctx(), client_order_id="t1"
    )
    assert res.action == "enter"
    assert res.submit is not None and res.submit.amount > 0
    assert res.stop is not None and res.stop.reduce_only is True
    # a buy and a reduceOnly sell stop both reached the (fake) exchange
    sides = [(c["side"], c["params"].get("reduceOnly", False)) for c in ex.created]
    assert ("buy", False) in sides and ("sell", True) in sides
    # the event chain was recorded in order
    types = [e.type for e in events.read_all()]
    for expected in (MARKET_RECEIVED, SIGNAL_GENERATED, RISK_PASSED, ORDER_SUBMITTED, FILL_RECEIVED):
        assert expected in types
    assert types.index(SIGNAL_GENERATED) < types.index(RISK_PASSED) < types.index(ORDER_SUBMITTED)


def test_halt_blocks_entry_and_logs_rejection(tmp_path):
    ex = FakeExchange()
    events = EventLog(tmp_path / "e.jsonl")
    ks = KillSwitch()
    ks.manual_kill()
    res = _loop(ex, events, FakeStrategy(INTENT_ENTER_LONG)).tick(
        _frame(), _state(), _ctx(ks=ks), client_order_id="t1"
    )
    assert res.action == "skip"
    assert ex.created == []  # nothing reached the exchange
    assert RISK_REJECTED in [e.type for e in events.read_all()]


def test_hold_does_nothing(tmp_path):
    ex = FakeExchange()
    res = _loop(ex, EventLog(tmp_path / "e.jsonl"), FakeStrategy(INTENT_HOLD)).tick(
        _frame(), _state(), _ctx(), client_order_id="t1"
    )
    assert res.action == "hold"
    assert ex.created == []


def test_infeasible_size_is_skipped(tmp_path):
    ex = FakeExchange()
    big_min = MarketConstraints(min_notional=1e9, lot_step=1e-5, tick_size=0.1)
    loop = FastLoop(
        broker=Broker(ex, {PAIR: big_min}), stops=StopManager(ex, {PAIR: big_min}),
        events=EventLog(tmp_path / "e.jsonl"), strategy=FakeStrategy(INTENT_ENTER_LONG),
        cfg=_cfg(), market=big_min, pair=PAIR, atr_period=3,
    )
    res = loop.tick(_frame(), _state(), _ctx(), client_order_id="t1")
    assert res.action == "skip" and res.reason == "below_min_notional"
    assert ex.created == []


def test_skips_entry_when_stop_would_be_non_positive(tmp_path):
    # extreme intrabar range → ATR exceeds the entry price → protective stop ≤ 0 → don't open naked
    rows = [[T0 + i * HOUR, 100.0, 200.0, 1.0, 100.0, 10.0] for i in range(8)]
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    ex = FakeExchange()
    # big equity so sizing stays feasible despite the huge stop distance
    state = PortfolioState(equity=1e9, peak_equity=1e9, day_start_equity=1e9, quote_price=1.0)
    res = _loop(ex, EventLog(tmp_path / "e.jsonl"), FakeStrategy(INTENT_ENTER_LONG)).tick(
        df, state, _ctx(), client_order_id="t1"
    )
    assert res.action == "skip" and res.reason == "unprotectable"
    assert ex.created == []  # nothing opened


def test_exit_flow_sells_the_held_position(tmp_path):
    ex = FakeExchange()
    events = EventLog(tmp_path / "e.jsonl")
    state = _state(positions={PAIR: Position(PAIR, 0.1, 10000.0)})
    res = _loop(ex, events, FakeStrategy(INTENT_EXIT)).tick(_frame(), state, _ctx(), client_order_id="x1")
    assert res.action == "exit"
    assert any(c["side"] == "sell" for c in ex.created)


# ---- P1 sentiment haircut wiring (§3) --------------------------------------------------------

def _cfg_floor(floor):
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0}, "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": dict(_GOOD_EXCHANGE), "sentiment_floor": floor,
    }
    return RiskConfig.from_dict(d)


def _loop_sentiment(exchange, events, strategy, cfg, provider):
    markets = {PAIR: _MARKET}
    return FastLoop(
        broker=Broker(exchange, markets), stops=StopManager(exchange, markets), events=events,
        strategy=strategy, cfg=cfg, market=_MARKET, pair=PAIR, atr_period=3, atr_stop_mult=2.0,
        sentiment_provider=provider, now_fn=lambda: NOW,
    )


def _bearish_state(sentiment=-1.0, confidence=1.0):
    return SentimentState(schema_version=1, generated_at=NOW, ttl_seconds=600,
                          pairs={PAIR: PairSentiment(sentiment, confidence, "bearish news")})


def test_default_floor_1_makes_sentiment_a_no_op(tmp_path):
    # FLOOR=1.0 (project default): even a max-bearish reading must not change the size
    ex_plain, ex_sent = FakeExchange(), FakeExchange()
    base = _loop(ex_plain, EventLog(tmp_path / "a.jsonl"), FakeStrategy(INTENT_ENTER_LONG)).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    sent = _loop_sentiment(ex_sent, EventLog(tmp_path / "b.jsonl"), FakeStrategy(INTENT_ENTER_LONG),
                           _cfg_floor(1.0), lambda: _bearish_state()).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    assert base.submit.amount == sent.submit.amount  # identical size → P0 path preserved
    assert SENTIMENT_HAIRCUT not in [e.type for e in EventLog(tmp_path / "b.jsonl").read_all()]


def test_active_floor_shrinks_size_and_logs(tmp_path):
    ex_plain, ex_sent = FakeExchange(), FakeExchange()
    base = _loop(ex_plain, EventLog(tmp_path / "a.jsonl"), FakeStrategy(INTENT_ENTER_LONG)).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    events = EventLog(tmp_path / "b.jsonl")
    sent = _loop_sentiment(ex_sent, events, FakeStrategy(INTENT_ENTER_LONG),
                           _cfg_floor(0.5), lambda: _bearish_state()).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    assert sent.action == "enter"
    assert sent.submit.amount < base.submit.amount  # bearish + active floor → smaller
    haircuts = [e for e in events.read_all() if e.type == SENTIMENT_HAIRCUT]
    assert len(haircuts) == 1 and haircuts[0].payload["multiplier"] == 0.5


def test_absent_state_with_active_floor_is_neutral(tmp_path):
    ex_plain, ex_sent = FakeExchange(), FakeExchange()
    base = _loop(ex_plain, EventLog(tmp_path / "a.jsonl"), FakeStrategy(INTENT_ENTER_LONG)).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    sent = _loop_sentiment(ex_sent, EventLog(tmp_path / "b.jsonl"), FakeStrategy(INTENT_ENTER_LONG),
                           _cfg_floor(0.5), lambda: None).tick(  # provider returns no state
        _frame(), _state(), _ctx(), client_order_id="t1")
    assert sent.submit.amount == base.submit.amount  # no state → neutral, runs normally


def test_sentiment_never_triggers_an_entry_on_hold(tmp_path):
    # a bearish reading on a HOLD intent must not create a trade (haircut can't trigger/flip)
    ex = FakeExchange()
    res = _loop_sentiment(ex, EventLog(tmp_path / "e.jsonl"), FakeStrategy(INTENT_HOLD),
                          _cfg_floor(0.5), lambda: _bearish_state()).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    assert res.action == "hold"
    assert ex.created == []


def test_provider_exception_does_not_break_the_trade(tmp_path):
    def boom():
        raise RuntimeError("ollama down")
    ex = FakeExchange()
    res = _loop_sentiment(ex, EventLog(tmp_path / "e.jsonl"), FakeStrategy(INTENT_ENTER_LONG),
                          _cfg_floor(0.5), boom).tick(
        _frame(), _state(), _ctx(), client_order_id="t1")
    assert res.action == "enter"  # LLM path failure → neutral, trade proceeds (Inv. 2)
