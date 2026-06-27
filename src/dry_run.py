"""src/dry_run.py — paper dry-run loop driver + CLI (§6 P1–P3, §10 testnet-first).

Runs the fast (deterministic) loop continuously on closed candles in **paper** mode: OHLCV comes
from a read-only data exchange (ccxt), but every order is routed to a SIMULATED paper exchange —
**no real capital, no real order ever leaves this process** (real keys/orders are a P4 concern).
Each tick is appended to the event log.

This validates *signal logic and the order path* forward, not fills (§2 parity caveat): the paper
exchange fills at the order price with no slippage/latency, so dry-run P&L is optimistic.

`run_once()` is the testable unit (no sleep/network when injected with fakes). `run()` is the CLI
loop; `main()` wires a real ccxt data feed + a paper exchange. Run:  ``python -m src.dry_run``.
"""
from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backtest.runner import Costs
from src.data import feed
from src.events.log import FILL_RECEIVED, ORDER_SUBMITTED, EventLog, log_risk_decision
from src.execution.broker import Broker
from src.execution.stops import StopManager
from src.fast_loop import FastLoop, TickResult
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import Order, PortfolioState, Position

_GOOD_EXCHANGE = {"spot_mode": True, "leverage": 1, "margin_disabled": True,
                  "futures_disabled": True, "reduce_only_on_exit": True}


class PaperBrokerExchange:
    """A simulated exchange for ORDERS only (never real). Entries fill fully at the order price;
    reduceOnly stops are recorded as resting (they trigger only if price hits them, not modeled
    in this signal-focused dry-run)."""

    def __init__(self):
        self.orders: list[dict] = []

    def create_order(self, symbol, type, side, amount, price, params):
        self.orders.append({"symbol": symbol, "type": type, "side": side, "amount": amount,
                            "price": price, "params": dict(params)})
        is_stop = bool(params.get("reduceOnly"))
        return {
            "id": f"paper-{len(self.orders)}",
            "status": "open" if is_stop else "closed",
            "filled": 0.0 if is_stop else amount,
            "amount": amount,
        }


@dataclass
class PaperAccount:
    """Tracks paper equity/positions and updates from fills. quote_price defaults to a pegged 1.0;
    a real run should feed the live stablecoin price so the de-peg guard (§4) is not blind."""

    cash: float
    quote_price: float = 1.0
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    peak_equity: float = 0.0
    day_start_equity: float = 0.0

    def __post_init__(self):
        if self.peak_equity <= 0:
            self.peak_equity = self.cash
        if self.day_start_equity <= 0:
            self.day_start_equity = self.cash

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.value(prices[pair]) for pair, p in self.positions.items())

    def to_state(self, prices: dict[str, float]) -> PortfolioState:
        eq = self.equity(prices)
        self.peak_equity = max(self.peak_equity, eq)
        return PortfolioState(
            equity=eq, peak_equity=self.peak_equity, day_start_equity=self.day_start_equity,
            quote_price=self.quote_price, positions=dict(self.positions),
        )

    def apply_buy(self, pair: str, qty: float, price: float, taker_fee: float) -> None:
        cost = qty * price
        self.cash -= cost + cost * taker_fee
        self.positions[pair] = Position(pair, qty, price)

    def apply_sell(self, pair: str, qty: float, price: float, taker_fee: float) -> None:
        proceeds = qty * price
        fee = proceeds * taker_fee
        self.cash += proceeds - fee
        entry = self.positions[pair].entry_price
        self.realized_pnl += (price - entry) * qty - fee
        del self.positions[pair]


class DryRunner:
    def __init__(self, *, data_exchange, loop: FastLoop, account: PaperAccount, costs: Costs,
                 pair: str, timeframe: str, limit: int = 200,
                 killswitch: KillSwitch | None = None, prewarm: bool = True):
        self.data_exchange = data_exchange
        self.loop = loop
        self.account = account
        self.costs = costs
        self.pair = pair
        self.timeframe = timeframe
        self.limit = limit
        self.killswitch = killswitch or KillSwitch()
        self.last_prices: dict[str, float] = {}  # latest closed-candle mark per pair (for the UI)
        # serializes paper-account mutations between the loop thread and a UI manual order
        self._lock = threading.Lock()
        self._manual_seq = 0
        # Rolling candle buffer. Sparse feeds (e.g. bitbank returns ~1 day / ~12-24 1h candles per
        # call) don't return enough history for the indicators in a single fetch; we pre-warm a
        # deep buffer once (paginated) and merge the latest candles each tick. Harmless on dense
        # feeds (they fill the buffer in one call). prewarm=False keeps the old single-fetch path.
        self.prewarm = prewarm
        self._buffer = None
        self._buffer_cap = max(limit, 250)
        self._equity_history: list[dict] = []  # marked equity per tick/fill, for the UI chart
        self._equity_cap = 1000
        self._trades: list[dict] = []          # closed round-trip trades (for performance panel)
        self._trades_cap = 1000
        self._tick_count = 0
        self._last_tick_at: str | None = None

    def _record_close(self, pair: str, qty: float, exit_price: float) -> None:
        """Record a closed round-trip (caller holds the lock; call BEFORE apply_sell)."""
        pos = self.account.positions.get(pair)
        if pos is None:
            return
        entry = pos.entry_price
        self._trades.append({
            "exit_time": datetime.now(timezone.utc).isoformat(), "pair": pair, "qty": qty,
            "entry_price": entry, "exit_price": exit_price,
            "return": (exit_price / entry - 1.0) if entry else 0.0,
            "pnl": (exit_price - entry) * qty,
        })
        if len(self._trades) > self._trades_cap:
            self._trades = self._trades[-self._trades_cap:]

    def trades(self) -> list[dict]:
        with self._lock:
            return list(self._trades)

    def health(self) -> dict:
        return {"running": True, "tick_count": self._tick_count, "last_tick_at": self._last_tick_at,
                "pair": self.pair, "timeframe": self.timeframe}

    def _record_equity(self) -> None:
        """Append the current marked paper equity (caller must hold self._lock)."""
        self._equity_history.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "equity": self.account.equity(self.marks()),
        })
        if len(self._equity_history) > self._equity_cap:
            self._equity_history = self._equity_history[-self._equity_cap:]

    def equity_history(self) -> list[dict]:
        with self._lock:
            return list(self._equity_history)

    def _prewarm_buffer(self, now_ms: int | None):
        """Gather a deep history buffer once (paginated) so indicators have enough candles."""
        try:
            now = int(now_ms) if now_ms is not None else int(self.data_exchange.milliseconds())
            since = now - self._buffer_cap * feed.timeframe_to_ms(self.timeframe)
            hist = feed.fetch_ohlcv_history(self.data_exchange, self.pair, self.timeframe,
                                            since_ms=since, page_limit=self.limit, now_ms=now_ms)
            return hist if not hist.empty else None
        except Exception:
            return None  # fall back to the single-fetch latest window

    def _merge(self, latest):
        import pandas as pd
        if self._buffer is None or self._buffer.empty:
            combined = latest
        elif latest.empty:
            combined = self._buffer
        else:
            combined = pd.concat([self._buffer, latest], ignore_index=True)
        combined = combined.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
        self._buffer = combined.tail(self._buffer_cap).reset_index(drop=True)
        return self._buffer

    def _window(self, now_ms: int | None):
        """The candle window for one tick: latest fetch merged into a (pre-warmed) rolling buffer."""
        latest = feed.fetch_closed_ohlcv(self.data_exchange, self.pair, self.timeframe,
                                         limit=self.limit, now_ms=now_ms)
        if not self.prewarm:
            return latest
        if self._buffer is None:
            self._buffer = self._prewarm_buffer(now_ms)
        return self._merge(latest)

    def run_once(self, *, now_ms: int | None = None) -> TickResult | None:
        """Fetch the latest closed candles (merged into the rolling buffer), run one tick."""
        from src.risk import engine

        df = self._window(now_ms)
        if df is None or df.empty:
            return None
        last_close = float(df["close"].iloc[-1])
        self.last_prices[self.pair] = last_close
        ctx = engine.RiskContext(prices={self.pair: last_close},
                                 exchange_state=dict(_GOOD_EXCHANGE), killswitch=self.killswitch)
        state = self.account.to_state({self.pair: last_close})
        cid = f"{self.pair.replace('/', '')}-{df['timestamp'].iloc[-1].value}"
        result = self.loop.tick(df, state, ctx, client_order_id=cid)

        with self._lock:
            if result.submit is not None and result.submit.filled > 0 and result.decision is not None:
                order = result.decision.sized
                if result.action == "enter":
                    self.account.apply_buy(order.pair, result.submit.filled, order.price,
                                           self.costs.taker_fee)
                elif result.action == "exit":
                    self._record_close(order.pair, result.submit.filled, order.price)
                    self.account.apply_sell(order.pair, result.submit.filled, order.price,
                                            self.costs.taker_fee)
            self._tick_count += 1
            self._last_tick_at = datetime.now(timezone.utc).isoformat()
            self._record_equity()  # mark-to-market each tick for the UI equity curve
        return result

    def marks(self) -> dict[str, float]:
        """Current marks for every held pair: last closed price, falling back to entry price."""
        m = dict(self.last_prices)
        for pair, pos in self.account.positions.items():
            m.setdefault(pair, pos.entry_price)
        return m

    def snapshot_state(self):
        """Read-only PortfolioState at current marks — for the operator UI (no trading effect)."""
        with self._lock:
            return self.account.to_state(self.marks())

    def place_manual(self, *, side: str, qty: float, price: float) -> dict:
        """Place a MANUAL paper order through the SAME risk engine + broker as the bot (Inv. 3/9).

        Operator-initiated write from the UI. It is not sized by a strategy — the operator gives
        qty/price — but it still passes `risk.engine.validate` (no backdoor) and only fills if
        approved. Serialized with the loop via the runner lock so the paper account can't be
        corrupted by a concurrent tick. Returns a JSON-able result; never raises on bad input.
        """
        from src.risk import engine

        with self._lock:
            try:
                order = Order(self.pair, side, float(qty), float(price), "manual")
            except (ValueError, TypeError) as e:
                return {"placed": False, "filled": 0.0, "reasons": [f"invalid_order:{e}"]}
            marks = dict(self.last_prices)
            for p, pos in self.account.positions.items():
                marks.setdefault(p, pos.entry_price)
            marks[self.pair] = float(price)  # the operator's price is the mark for this decision
            ctx = engine.RiskContext(prices=marks, exchange_state=dict(_GOOD_EXCHANGE),
                                     killswitch=self.killswitch, market=self.loop.market)
            state = self.account.to_state(marks)
            decision = engine.validate(order, state, self.loop.cfg, ctx)
            log_risk_decision(self.loop.events, order, decision)
            if not decision.approved:
                return {"placed": False, "filled": 0.0, "reasons": list(decision.reasons)}
            self._manual_seq += 1
            cid = f"manual-{self._manual_seq}"
            submit = self.loop.broker.submit(decision, client_order_id=cid)
            self.loop.events.append(ORDER_SUBMITTED, {"pair": self.pair, "side": side,
                                                      "qty": submit.amount, "client_order_id": cid})
            if submit.filled > 0:
                self.loop.events.append(FILL_RECEIVED, {"pair": self.pair, "filled": submit.filled})
                if side == "buy":
                    self.account.apply_buy(self.pair, submit.filled, order.price, self.costs.taker_fee)
                else:
                    self._record_close(self.pair, submit.filled, order.price)
                    self.account.apply_sell(self.pair, submit.filled, order.price, self.costs.taker_fee)
                self._record_equity()  # reflect the manual fill on the UI equity curve
            return {"placed": True, "filled": submit.filled, "reasons": []}

    def run(self, *, iterations: int | None = None, poll_seconds: float = 60.0) -> None:
        """CLI loop: tick, then sleep until roughly the next candle. iterations=None runs forever."""
        i = 0
        while iterations is None or i < iterations:
            result = self.run_once()
            action = result.action if result else "no_data"
            print(f"[dry-run] tick {i}: {action}  cash={self.account.cash:.2f}  "
                  f"realized_pnl={self.account.realized_pnl:.2f}")
            i += 1
            if iterations is not None and i >= iterations:
                break
            time.sleep(poll_seconds)


def build_runner(*, data_exchange, paper_exchange, events: EventLog, strategy, cfg: RiskConfig,
                 costs: Costs, market: MarketConstraints, account: PaperAccount, pair: str,
                 timeframe: str, limit: int = 200, killswitch: KillSwitch | None = None,
                 atr_period: int = 14, atr_stop_mult: float = 2.0,
                 sentiment_state_path: str | None = None, prewarm: bool = True) -> DryRunner:
    markets = {pair: market}
    # Optional P1 sentiment haircut: read the slow-loop state file each entry (non-blocking, never
    # calls the LLM). Inert unless cfg.sentiment_floor < 1.0 (default 1.0 = off, §6/P1).
    sentiment_provider = None
    if sentiment_state_path is not None:
        from src.llm.state_io import load_sentiment_state
        sentiment_provider = lambda: load_sentiment_state(sentiment_state_path)  # noqa: E731
    loop = FastLoop(
        broker=Broker(paper_exchange, markets), stops=StopManager(paper_exchange, markets),
        events=events, strategy=strategy, cfg=cfg, market=market, pair=pair,
        atr_period=atr_period, atr_stop_mult=atr_stop_mult, sentiment_provider=sentiment_provider,
    )
    return DryRunner(data_exchange=data_exchange, loop=loop, account=account, costs=costs,
                     pair=pair, timeframe=timeframe, limit=limit, killswitch=killswitch,
                     prewarm=prewarm)


def main(argv: list[str] | None = None) -> None:
    import ccxt

    from src.core.console import force_utf8_stdio
    from src.strategy.ema_cross import EmaCross

    force_utf8_stdio()

    p = argparse.ArgumentParser(description="Paper dry-run loop (no real capital).")
    p.add_argument("--data-exchange", default="kraken", help="ccxt id for the read-only OHLCV feed")
    p.add_argument("--pair", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--equity", type=float, default=10000.0, help="starting paper equity")
    p.add_argument("--iterations", type=int, default=1, help="ticks to run (0 = forever)")
    p.add_argument("--poll-seconds", type=float, default=60.0)
    p.add_argument("--events", default="events/dry_run.jsonl")
    p.add_argument("--sentiment-state", default=None,
                   help="path to sentiment_state.json (P1 haircut; inert unless sentiment_floor<1.0)")
    p.add_argument("--serve-ui", action="store_true",
                   help="serve the read-only operator dashboard over the live paper state")
    p.add_argument("--ui-port", type=int, default=8787)
    args = p.parse_args(argv)

    import os
    data_ex = getattr(ccxt, args.data_exchange)({"enableRateLimit": True})
    if os.environ.get("HTTPS_PROXY"):
        data_ex.https_proxy = os.environ["HTTPS_PROXY"]

    cfg = RiskConfig.load("config/risk/default.json")
    costs = Costs.load("config/backtest/costs.json")
    market = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)  # approx; verify per venue
    runner = build_runner(
        data_exchange=data_ex, paper_exchange=PaperBrokerExchange(), events=EventLog(args.events),
        strategy=EmaCross(ema_fast=12, ema_slow=26), cfg=cfg, costs=costs, market=market,
        account=PaperAccount(cash=args.equity), pair=args.pair, timeframe=args.timeframe,
        sentiment_state_path=args.sentiment_state,
    )
    print(f"[dry-run] PAPER mode — no real capital. data={args.data_exchange} pair={args.pair} "
          f"tf={args.timeframe} equity={args.equity}")

    if args.serve_ui:
        import threading

        from src.ui.live import build_live_context
        from src.ui.server import serve
        ctx = build_live_context(runner)
        threading.Thread(target=serve, args=(ctx,), kwargs={"port": args.ui_port},
                         daemon=True).start()
        print(f"[dry-run] operator dashboard (read-only) on http://127.0.0.1:{args.ui_port}/")

    runner.run(iterations=None if args.iterations == 0 else args.iterations,
               poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
