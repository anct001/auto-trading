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


class ReplayFeed:
    """Read-only OHLCV source over a FIXED historical dataset — for replaying the paper loop over
    the past (no network). Exposes the ccxt slice the feed uses (``fetch_ohlcv`` + ``milliseconds``)
    bounded by a movable ``_now`` so each replay step only ever sees candles that had closed by that
    wall-time — causal, no look-ahead (§8.4). Semantics mirror a real venue: ``since=None`` returns
    the most-recent ``limit`` rows; ``since`` given returns rows from there forward (ascending)."""

    def __init__(self, rows: list[list[float]]):
        self._rows = sorted(([int(r[0]), *(float(x) for x in r[1:6])] for r in rows),
                            key=lambda r: r[0])
        self._now = (self._rows[-1][0] + 1) if self._rows else 0

    @classmethod
    def from_frame(cls, df) -> "ReplayFeed":
        rows = []
        for _, r in df.iterrows():
            ts = r["timestamp"]
            ms = int(ts.value // 1_000_000) if hasattr(ts, "value") else int(ts)
            rows.append([ms, r["open"], r["high"], r["low"], r["close"], r["volume"]])
        return cls(rows)

    def set_now(self, now_ms: int) -> None:
        self._now = int(now_ms)

    def milliseconds(self) -> int:
        return self._now

    def all_rows(self) -> list[list[float]]:
        return [list(r) for r in self._rows]

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        rows = [r for r in self._rows if r[0] < self._now]
        if since is not None:
            rows = [r for r in rows if r[0] >= int(since)]
            return [list(r) for r in (rows[:limit] if limit else rows)]
        return [list(r) for r in (rows[-limit:] if limit else rows)]


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
        self._last_error: str | None = None  # last transient tick error (data feed hiccup, etc.)
        self.alerter = None  # optional ops.alerts.Alerter — fires on breaker/limit/kill each tick

    def _check_alerts(self) -> None:
        """Run the alerter over the current operator state (edge-triggered; fail-soft)."""
        if self.alerter is None:
            return
        try:
            from dataclasses import asdict
            from src.ui.dashboard.model import build_dashboard
            payload = asdict(build_dashboard(
                state=self.snapshot_state(), cfg=self.loop.cfg, prices=self.marks(),
                killswitch=self.killswitch, events=self.loop.events.read_all()))
            payload["health"] = self.health()
            self.alerter.update(payload)
        except Exception:
            pass  # alerting must never break the loop (Inv 2 spirit)

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
                "pair": self.pair, "timeframe": self.timeframe, "last_error": self._last_error}

    def current_frame(self):
        """The latest candle window (pre-warmed rolling buffer), for the agent-view overlay."""
        with self._lock:
            return None if self._buffer is None else self._buffer.copy()

    def _record_equity(self) -> None:
        """Append the current marked paper equity + exposure (caller must hold self._lock)."""
        marks = self.marks()
        eq = self.account.equity(marks)
        gross = sum(p.value(marks[pair]) for pair, p in self.account.positions.items()
                    if pair in marks)
        self._equity_history.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "equity": eq,
            "exposure": (gross / eq) if eq > 0 else 0.0,  # gross position value / equity (time-in-market)
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
        """CLI loop: tick, then sleep until roughly the next candle. iterations=None runs forever.

        A tick must NEVER kill the loop: a transient data-feed error (network reset, exchange 5xx,
        rate limit) is caught, recorded, and skipped — a missing candle simply means no decision
        this tick (fail-closed: no trade on absent data, §8). This is what lets the ≥N-day dry-run
        run continuously without crashing (P3). Programming errors surface the same way (logged),
        not silently — the operator sees `last_error` on the dashboard.
        """
        i = 0
        while iterations is None or i < iterations:
            try:
                result = self.run_once()
                action = result.action if result else "no_data"
                print(f"[dry-run] tick {i}: {action}  cash={self.account.cash:.2f}  "
                      f"realized_pnl={self.account.realized_pnl:.2f}")
            except Exception as e:  # noqa: BLE001 — resilience: a feed hiccup must not stop the loop
                self._last_error = f"{type(e).__name__}: {e}"
                print(f"[dry-run] tick {i}: ERROR {self._last_error} (skipped, continuing)")
            self._check_alerts()
            i += 1
            if iterations is not None and i >= iterations:
                break
            time.sleep(poll_seconds)

    def replay(self, *, warmup: int | None = None, progress_every: int = 0) -> dict:
        """Replay the fast loop over PAST data (``data_exchange`` must be a :class:`ReplayFeed`).

        Steps a synthetic 'now' forward one timeframe at a time so each tick sees exactly the
        candles closed by that step — the *same* path as a live run (loop → risk → broker → stops →
        event log), but over history and without sleeping. A bad bar is caught and skipped (matches
        ``run``'s resilience), so the whole replay always completes. Returns the final health dict.
        """
        rows = self.data_exchange.all_rows()
        if not rows:
            return self.health()
        step = feed.timeframe_to_ms(self.timeframe)
        warm = self._buffer_cap if warmup is None else warmup
        warm = min(max(warm, 0), len(rows))
        total = len(rows)
        for idx in range(warm, total):
            t = int(rows[idx][0]) + step  # wall-clock just after this candle closes
            self.data_exchange.set_now(t)
            try:
                self.run_once(now_ms=t)
            except Exception as e:  # noqa: BLE001 — a bad bar must not stop the replay (see run())
                self._last_error = f"{type(e).__name__}: {e}"
            self._check_alerts()
            if progress_every and (idx - warm) % progress_every == 0:
                print(f"[replay] {idx}/{total}  equity={self.account.equity(self.marks()):.2f}")
        return self.health()


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


def _resolve_chat_config(args) -> dict:
    """Build the /chat ChatRouter from CLI + env: every provider whose API key is in the environment
    (local Ollama always), so the UI can switch among them. Keys come ONLY from the env (never a
    flag → never in shell history / process list), wrapped as masked Secrets. A privacy warning is
    printed when any cloud provider is available. Returns build_*_context kwargs."""
    from src.llm.chat import build_chat_router
    provider = getattr(args, "chat_provider", "ollama")
    router = build_chat_router(default_provider=provider, ollama_model=args.chat_model,
                               base_url=args.chat_base_url)
    names = [p["name"] for p in router.providers()["providers"]]
    print(f"[dry-run] /chat providers: {', '.join(names)} (default {router.default}; "
          "switch in the UI)")
    if any(n != "ollama" for n in names):
        print("[dry-run] ⚠ CLOUD chat providers send the dashboard snapshot (equity/positions/"
              "decisions) off-machine when selected. Read-only — they still cannot trade (Inv 1).")
    if provider != "ollama" and provider not in names:
        env_name = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                    "gemini": "GEMINI_API_KEY"}.get(provider, "?")
        print(f"[dry-run] ⚠ requested provider '{provider}' has no {env_name} set — "
              f"defaulting to {router.default}.")
    return {"chat_router": router}


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
    p.add_argument("--replay-days", type=int, default=0,
                   help="replay N days of PAST data through the paper loop, then stop "
                        "(0 = normal live-poll mode). No sleeping; same order path as live.")
    p.add_argument("--replay-file", default=None,
                   help="replay from a stored OHLCV file (.parquet/.csv) of REAL past data instead "
                        "of fetching — offline & repeatable. Overrides --replay-days.")
    p.add_argument("--save-history", default=None,
                   help="with --replay-days, also save the fetched REAL history to this .parquet "
                        "so it can be replayed later with --replay-file (reproducible).")
    p.add_argument("--pairs", default=None,
                   help="comma-separated pairs for a MULTI-PAIR replay comparison (needs "
                        "--replay-days); serves the /replay dashboard + cross-coin assistant.")
    p.add_argument("--strategies", default=None,
                   help="comma-separated strategies (ema,rsi,donchian,funding) to compare on ONE "
                        "--pair over --replay-days; serves the /replay dashboard + assistant.")
    p.add_argument("--perp", default=None,
                   help="perp symbol for funding (default <pair>:USDT) when comparing the funding strategy")
    p.add_argument("--poll-seconds", type=float, default=60.0)
    p.add_argument("--events", default="events/dry_run.jsonl")
    p.add_argument("--sentiment-state", default=None,
                   help="path to sentiment_state.json (P1 haircut; inert unless sentiment_floor<1.0)")
    p.add_argument("--serve-ui", action="store_true",
                   help="serve the read-only operator dashboard over the live paper state")
    p.add_argument("--ui-port", type=int, default=8787)
    p.add_argument("--chat-model", default="llama3.1",
                   help="model for the /chat assistant (Ollama default llama3.1; per-provider "
                        "default used automatically for cloud providers)")
    p.add_argument("--chat-provider", default="ollama", choices=["ollama", "anthropic", "openai"],
                   help="AI backend for /chat. ollama = LOCAL (default; data never leaves). "
                        "anthropic/openai are CLOUD (send dashboard state off-machine; key from "
                        "ANTHROPIC_API_KEY / OPENAI_API_KEY env, never a flag)")
    p.add_argument("--chat-base-url", default=None,
                   help="override the base URL for the openai provider (OpenAI-compatible endpoints)")
    p.add_argument("--ui-token", default=None,
                   help="require this X-Auth-Token on UI writes (order/kill-switch); "
                        "defaults to env UI_AUTH_TOKEN. Unset = open (localhost only)")
    p.add_argument("--alert-webhook", default=None,
                   help="POST alerts (breaker/limit/kill) to this webhook URL (Telegram/Slack/push)")
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

    chat_cfg = _resolve_chat_config(args)  # provider/model/api_key/base_url for the /chat assistant

    # operator alerting: log every alert to the event store + print; optional webhook (§12/§15)
    from src.ops.alerts import Alerter, event_log_sink, print_sink, webhook_sink
    sinks = [print_sink, event_log_sink(runner.loop.events)]
    if args.alert_webhook:
        sinks.append(webhook_sink(args.alert_webhook))
        print(f"[dry-run] alerts -> webhook {args.alert_webhook}")
    runner.alerter = Alerter(sinks=sinks)

    if args.serve_ui:
        import threading

        from src.ui.live import build_live_context
        from src.ui.server import serve
        ui_token = args.ui_token or os.environ.get("UI_AUTH_TOKEN")
        ctx = build_live_context(runner, auth_token=ui_token, **chat_cfg)
        threading.Thread(target=serve, args=(ctx,), kwargs={"port": args.ui_port},
                         daemon=True).start()
        print(f"[dry-run] operator dashboard (read-only) on http://127.0.0.1:{args.ui_port}/ "
              f"(assistant at /chat, model {args.chat_model}; "
              f"writes {'TOKEN-PROTECTED' if ui_token else 'open — localhost only'})")

    if args.strategies:
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
        days = args.replay_days if args.replay_days > 0 else 365
        print(f"[dry-run] STRATEGY COMPARISON on {args.pair} over {days}d: {', '.join(strategies)}")
        comparison = run_strategy_comparison(
            data_ex=data_ex, coin=args.pair, strategies=strategies, timeframe=args.timeframe,
            days=days, equity=args.equity, cfg=cfg, costs=costs, market=market, perp=args.perp)
        print(f"[dry-run] STRATEGY COMPARISON done. Best: {comparison.get('best_pair')} · "
              f"worst: {comparison.get('worst_pair')}")
        if args.serve_ui:
            import threading

            from src.ui.live import build_replay_context
            from src.ui.server import serve
            rctx = build_replay_context(comparison, **chat_cfg)
            threading.Thread(target=serve, args=(rctx,), kwargs={"port": args.ui_port},
                             daemon=True).start()
            print(f"[dry-run] strategy-comparison dashboard on "
                  f"http://127.0.0.1:{args.ui_port}/replay")
            while True:
                time.sleep(3600)
        return

    if args.pairs:
        from src.strategy.ema_cross import EmaCross
        from src.ui.replay_dashboard import build_replay_comparison
        pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
        days = args.replay_days if args.replay_days > 0 else 365
        print(f"[dry-run] MULTI-PAIR REPLAY over {days}d: {', '.join(pairs)}")
        results = run_multi_replay(
            data_ex=data_ex, pairs=pairs, timeframe=args.timeframe, days=days, equity=args.equity,
            cfg=cfg, costs=costs, market=market, strategy_factory=lambda: EmaCross(12, 26),
            events_dir=os.path.join(os.path.dirname(args.events) or ".", "replay"))
        comparison = build_replay_comparison(results)
        print(f"[dry-run] MULTI-PAIR done. Best: {comparison.get('best_pair')} · "
              f"worst: {comparison.get('worst_pair')} ({len(results)} pairs)")
        if args.serve_ui:
            import threading

            from src.ui.live import build_replay_context
            from src.ui.server import serve
            rctx = build_replay_context(comparison, **chat_cfg)
            threading.Thread(target=serve, args=(rctx,), kwargs={"port": args.ui_port},
                             daemon=True).start()
            print(f"[dry-run] replay dashboard on http://127.0.0.1:{args.ui_port}/replay "
                  f"(cross-coin assistant at /chat)")
            while True:
                time.sleep(3600)
        return

    if args.replay_file or args.replay_days > 0:
        hist = _load_replay_history(args, data_ex)
        print(f"[dry-run] REPLAY: {len(hist)} REAL {args.pair} {args.timeframe} candles — stepping "
              f"the paper loop over the past (no real capital) ...")
        runner.data_exchange = ReplayFeed.from_frame(hist)
        h = runner.replay(progress_every=max(1, len(hist) // 10))
        eq = runner.account.equity(runner.marks())
        ret = (eq / args.equity - 1.0) * 100.0 if args.equity else 0.0
        print(f"[dry-run] REPLAY done: {h['tick_count']} ticks, {len(runner.trades())} closed "
              f"trades, final equity {eq:.2f} ({ret:+.2f}% vs start), realized_pnl "
              f"{runner.account.realized_pnl:.2f}. Events -> {args.events}")
        if args.serve_ui:
            print("[dry-run] UI still serving the replay result — Ctrl-C to exit.")
            while True:
                time.sleep(3600)
        return

    runner.run(iterations=None if args.iterations == 0 else args.iterations,
               poll_seconds=args.poll_seconds)


def run_multi_replay(*, data_ex, pairs: list[str], timeframe: str, days: int, equity: float,
                     cfg: RiskConfig, costs: Costs, market: MarketConstraints, strategy_factory,
                     events_dir: str, atr_period: int = 14, atr_stop_mult: float = 2.0) -> dict:
    """Replay the paper loop over PAST data for each pair and return {pair: ReplayResult}.

    Each pair gets its own paper account, event log, and ReplayFeed (fetched history). A pair whose
    fetch/replay fails is skipped (logged) so one bad symbol never sinks the batch. No real capital.
    """
    import os

    from src.ui.replay_dashboard import summarize_result
    os.makedirs(events_dir, exist_ok=True)
    now = data_ex.milliseconds()
    since = now - days * 86_400_000
    results: dict = {}
    for pair in pairs:
        try:
            hist = feed.fetch_ohlcv_history(data_ex, pair, timeframe, since_ms=since,
                                            page_limit=720, now_ms=now)
            if hist is None or len(hist) == 0:
                print(f"[multi-replay] {pair}: no data, skipped")
                continue
            safe = pair.replace("/", "_")
            runner = build_runner(
                data_exchange=ReplayFeed.from_frame(hist), paper_exchange=PaperBrokerExchange(),
                events=EventLog(os.path.join(events_dir, f"{safe}.jsonl")),
                strategy=strategy_factory(), cfg=cfg, costs=costs, market=market,
                account=PaperAccount(cash=equity), pair=pair, timeframe=timeframe,
                atr_period=atr_period, atr_stop_mult=atr_stop_mult)
            runner.replay()
            results[pair] = summarize_result(
                pair, trades=runner.trades(), equity_history=runner.equity_history(),
                start_equity=equity)
            print(f"[multi-replay] {pair}: {len(hist)} candles, "
                  f"{len(runner.trades())} trades, return "
                  f"{results[pair].total_return * 100:+.2f}%")
        except Exception as e:  # noqa: BLE001 — one bad symbol must not sink the batch
            print(f"[multi-replay] {pair}: ERROR {type(e).__name__}: {e} (skipped)")
    return results


def _summarize_backtest(name: str, df, result, start_equity: float):
    """Adapt a BacktestResult (trades DF + equity Series) into a ReplayResult, computing per-bar
    exposure (position value / equity) from the trade spans + the coin's closes."""
    from src.ui.replay_dashboard import summarize_result
    eqc = result.equity_curve
    closes = dict(zip(df["timestamp"], df["close"]))
    tdf = result.trades
    exposure = {ts: 0.0 for ts in eqc.index}
    for _, tr in tdf.iterrows():
        et, xt, q = tr["entry_time"], tr["exit_time"], float(tr["qty"])
        for ts in eqc.index:
            if et <= ts < xt:
                eq = float(eqc.loc[ts])
                exposure[ts] = (q * float(closes.get(ts, 0.0)) / eq) if eq > 0 else 0.0
    equity_history = [{"t": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                       "equity": float(eqc.loc[ts]), "exposure": exposure[ts]} for ts in eqc.index]
    trades = [{"exit_time": tr["exit_time"].isoformat() if hasattr(tr["exit_time"], "isoformat")
               else str(tr["exit_time"]), "return": float(tr["return"]),
               "pnl": (float(tr["exit_price"]) - float(tr["entry_price"])) * float(tr["qty"])}
              for _, tr in tdf.iterrows()]
    return summarize_result(name, trades=trades, equity_history=equity_history,
                            start_equity=start_equity)


# strategy registry for the comparison: cli-name -> (label, factory, needs_funding)
def _strategy_registry():
    from src.strategy.donchian_breakout import DonchianBreakout
    from src.strategy.ema_cross import EmaCross
    from src.strategy.funding_carry import FundingCarry
    from src.strategy.rsi_reversion import RsiReversion
    return {
        "ema": ("ema_12_26", lambda: EmaCross(12, 26), False),
        "rsi": ("rsi_14", lambda: RsiReversion(14, 30, 70), False),
        "donchian": ("donchian_20", lambda: DonchianBreakout(20), False),
        "funding": ("funding_carry", lambda: FundingCarry(0.0), True),
    }


def run_strategy_comparison(*, data_ex, coin: str, strategies: list[str], timeframe: str,
                            days: int, equity: float, cfg: RiskConfig, costs: Costs,
                            market: MarketConstraints, perp: str | None = None,
                            atr_period: int = 14, atr_stop_mult: float = 2.0) -> dict:
    """Compare several strategies on ONE coin via the §8 backtest engine, return a comparison dict.

    All strategies run on the same fetched history (uniform, honest); FundingCarry additionally gets
    a causally-aligned funding column. Returns build_replay_comparison(..., dimension="strategy")."""
    from backtest.runner import RiskSizing, run_backtest
    from src.data import quality
    from src.ui.replay_dashboard import build_replay_comparison
    registry = _strategy_registry()
    specs = [(registry[s][0], registry[s][1], registry[s][2]) for s in strategies if s in registry]

    now = data_ex.milliseconds()
    since = now - days * 86_400_000
    df = feed.fetch_ohlcv_history(data_ex, coin, timeframe, since_ms=since, page_limit=720, now_ms=now)
    df = quality.check_quality(df).clean.reset_index(drop=True)
    print(f"[strategy-cmp] {coin} {timeframe}: {len(df)} clean candles")

    df_funding = df
    if any(nf for _, _, nf in specs):
        from src.data import funding as funding_mod
        try:
            perp_sym = perp or f"{coin}:USDT"
            fdf = funding_mod.fetch_funding_history(data_ex, perp_sym, since_ms=since, now_ms=now)
            df_funding = funding_mod.align_funding(df, fdf)
            print(f"[strategy-cmp] funding: {len(fdf)} prints for {perp_sym}")
        except Exception as e:  # noqa: BLE001
            print(f"[strategy-cmp] funding fetch failed ({e}); dropping funding strategy")
            specs = [(n, f, nf) for (n, f, nf) in specs if not nf]

    risk = RiskSizing(cfg=cfg, market=market, atr_period=atr_period, atr_stop_mult=atr_stop_mult)
    results: dict = {}
    for name, factory, needs_funding in specs:
        try:
            use_df = df_funding if needs_funding else df
            res = run_backtest(use_df, factory(), costs=costs, risk=risk, initial_equity=equity)
            results[name] = _summarize_backtest(name, use_df, res, equity)
            print(f"[strategy-cmp] {name}: {len(res.trades)} trades, "
                  f"return {results[name].total_return * 100:+.2f}%")
        except Exception as e:  # noqa: BLE001 — one bad strategy must not sink the batch
            print(f"[strategy-cmp] {name}: ERROR {type(e).__name__}: {e} (skipped)")
    return build_replay_comparison(results, dimension="strategy", coin=coin)


def _load_replay_history(args, data_ex):
    """Get the REAL historical OHLCV to replay: from a stored file (offline/repeatable) or by
    fetching ``--replay-days`` from the venue (optionally saving it for reuse)."""
    import pandas as pd

    from src.data import store
    if args.replay_file:
        if str(args.replay_file).endswith(".csv"):
            hist = pd.read_csv(args.replay_file, parse_dates=["timestamp"])
        else:
            hist = store.read_ohlcv(args.replay_file)
        print(f"[dry-run] REPLAY from file {args.replay_file}")
        return hist
    now = data_ex.milliseconds()
    since = now - args.replay_days * 86_400_000
    print(f"[dry-run] REPLAY: fetching {args.replay_days}d of {args.pair} {args.timeframe} "
          f"history from {args.data_exchange} ...")
    hist = feed.fetch_ohlcv_history(data_ex, args.pair, args.timeframe,
                                    since_ms=since, page_limit=720, now_ms=now)
    if args.save_history:
        store.write_ohlcv(hist, args.save_history)
        print(f"[dry-run] saved {len(hist)} candles -> {args.save_history} (replay later with "
              f"--replay-file)")
    return hist


if __name__ == "__main__":
    main()
