"""src/fast_loop.py — §3 the fast (deterministic) loop — the only thing that trades.

Composes the whole pipeline for one closed-candle tick:

    quality gate → single feature path → strategy intent → sizing → RISK GATE → broker →
    exchange-side protective stop → event log

Every order still passes `risk.engine.validate` (Inv. 3/9) — this orchestrator has no backdoor.
It reads slow-loop (LLM) outputs only as optional, non-vetoing inputs (none wired until P1); it
must run correctly with them absent (Inv. 2). It is deterministic: given the same closed candles,
state, and context, it makes the same decision.

This wires together components each already unit-tested in isolation; the tests in
tests/test_fast_loop.py exercise the composition against a mock exchange.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.data.quality import check_quality
from src.events.log import (
    FILL_RECEIVED,
    MARKET_RECEIVED,
    ORDER_SUBMITTED,
    SIGNAL_GENERATED,
    EventLog,
    log_risk_decision,
)
from src.execution.broker import Broker, SubmitResult
from src.execution.stops import StopManager, StopResult
from src.features.indicators import atr as atr_fn
from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints, compute_size
from src.risk.types import Order, PortfolioState, RiskDecision
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT


@dataclass(frozen=True)
class TickResult:
    action: str  # "enter" | "exit" | "hold" | "skip"
    intent: str
    decision: RiskDecision | None = None
    submit: SubmitResult | None = None
    stop: StopResult | None = None
    reason: str = ""


class FastLoop:
    def __init__(
        self,
        *,
        broker: Broker,
        stops: StopManager,
        events: EventLog,
        strategy,
        cfg: RiskConfig,
        market: MarketConstraints,
        pair: str,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
    ):
        self.broker = broker
        self.stops = stops
        self.events = events
        self.strategy = strategy
        self.cfg = cfg
        self.market = market
        self.pair = pair
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult

    def tick(
        self,
        df: pd.DataFrame,
        state: PortfolioState,
        ctx: engine.RiskContext,
        *,
        client_order_id: str,
    ) -> TickResult:
        """Process one closed-candle decision. See module docstring for the flow."""
        clean = check_quality(df).clean
        if len(clean) < self.atr_period + 2:
            return TickResult("hold", "insufficient_data", reason="insufficient_data")

        intent = str(self.strategy.generate_signals(clean).iloc[-1])
        price = float(clean["close"].iloc[-1])
        atr_now = float(atr_fn(clean, self.atr_period).iloc[-1])
        ts = clean["timestamp"].iloc[-1]

        self.events.append(MARKET_RECEIVED, {"pair": self.pair, "close": price, "timestamp": str(ts)})
        self.events.append(SIGNAL_GENERATED, {"pair": self.pair, "intent": intent})

        pos = state.positions.get(self.pair)
        held = pos.qty if pos else 0.0

        if intent == INTENT_ENTER_LONG and held == 0.0:
            return self._enter(state, ctx, price, atr_now, client_order_id)
        if intent == INTENT_EXIT and held > 0.0:
            return self._exit(state, ctx, price, held, client_order_id)
        return TickResult("hold", intent)

    def _enter(self, state, ctx, price, atr_now, client_order_id) -> TickResult:
        sized = compute_size(
            pair=self.pair, price=price, atr=atr_now, state=state, cfg=self.cfg,
            market=self.market, atr_stop_mult=self.atr_stop_mult,
        )
        if not sized.feasible:
            self.events.append("EntrySkipped", {"pair": self.pair, "reason": sized.reason})
            return TickResult("skip", INTENT_ENTER_LONG, reason=sized.reason)

        order = sized.to_order(source="strategy")
        decision = engine.validate(order, state, self.cfg, ctx)
        log_risk_decision(self.events, order, decision)
        if not decision.approved:
            return TickResult("skip", INTENT_ENTER_LONG, decision=decision,
                              reason=",".join(decision.reasons))

        submit = self.broker.submit(decision, client_order_id=client_order_id)
        self.events.append(ORDER_SUBMITTED,
                           {"pair": self.pair, "side": "buy", "qty": submit.amount,
                            "client_order_id": client_order_id})
        stop = None
        if submit.filled > 0:
            self.events.append(FILL_RECEIVED, {"pair": self.pair, "filled": submit.filled})
            stop = self.stops.attach_protective(
                pair=self.pair, qty=submit.filled, entry_price=order.price, atr=atr_now,
                atr_stop_mult=self.atr_stop_mult, client_order_id=f"{client_order_id}-stop",
            )
        return TickResult("enter", INTENT_ENTER_LONG, decision=decision, submit=submit, stop=stop)

    def _exit(self, state, ctx, price, held, client_order_id) -> TickResult:
        order = Order(self.pair, "sell", held, price, "strategy")
        decision = engine.validate(order, state, self.cfg, ctx)
        log_risk_decision(self.events, order, decision)
        if not decision.approved:
            return TickResult("skip", INTENT_EXIT, decision=decision,
                              reason=",".join(decision.reasons))
        submit = self.broker.submit(decision, client_order_id=client_order_id)
        self.events.append(ORDER_SUBMITTED,
                           {"pair": self.pair, "side": "sell", "qty": submit.amount,
                            "client_order_id": client_order_id})
        return TickResult("exit", INTENT_EXIT, decision=decision, submit=submit)
