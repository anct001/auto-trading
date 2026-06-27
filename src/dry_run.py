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
import time
from dataclasses import dataclass, field

from backtest.runner import Costs
from src.data import feed
from src.events.log import EventLog
from src.execution.broker import Broker
from src.execution.stops import StopManager
from src.fast_loop import FastLoop, TickResult
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.sizing import MarketConstraints
from src.risk.types import PortfolioState, Position

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
                 killswitch: KillSwitch | None = None):
        self.data_exchange = data_exchange
        self.loop = loop
        self.account = account
        self.costs = costs
        self.pair = pair
        self.timeframe = timeframe
        self.limit = limit
        self.killswitch = killswitch or KillSwitch()

    def run_once(self, *, now_ms: int | None = None) -> TickResult | None:
        """Fetch the latest closed candles, run one tick, and update the paper account."""
        from src.risk import engine

        df = feed.fetch_closed_ohlcv(
            self.data_exchange, self.pair, self.timeframe, limit=self.limit, now_ms=now_ms
        )
        if df.empty:
            return None
        last_close = float(df["close"].iloc[-1])
        ctx = engine.RiskContext(prices={self.pair: last_close},
                                 exchange_state=dict(_GOOD_EXCHANGE), killswitch=self.killswitch)
        state = self.account.to_state({self.pair: last_close})
        cid = f"{self.pair.replace('/', '')}-{df['timestamp'].iloc[-1].value}"
        result = self.loop.tick(df, state, ctx, client_order_id=cid)

        if result.submit is not None and result.submit.filled > 0 and result.decision is not None:
            order = result.decision.sized
            if result.action == "enter":
                self.account.apply_buy(order.pair, result.submit.filled, order.price,
                                       self.costs.taker_fee)
            elif result.action == "exit":
                self.account.apply_sell(order.pair, result.submit.filled, order.price,
                                        self.costs.taker_fee)
        return result

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
                 sentiment_state_path: str | None = None) -> DryRunner:
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
                     pair=pair, timeframe=timeframe, limit=limit, killswitch=killswitch)


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
    runner.run(iterations=None if args.iterations == 0 else args.iterations,
               poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
