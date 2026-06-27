"""backtest/runner.py — §8 deterministic backtest (reproducible + cost-modeled).

DEVIATION FROM §2 REFERENCE STACK (justified per §7, see PLAN.md Slice 6): the §2 stack names
Freqtrade as the execution/backtest backbone. For the P0 gate — which requires a *reproducible*
backtest with modeled costs and metrics (§8.3, §8.8) — this in-house deterministic runner is
used instead. It consumes the SAME single feature path + strategy code as live (no train-serve
skew) and is trivially reproducible. Freqtrade is reintroduced at P3, where its dry-run
live-parity (the thing it uniquely provides) actually earns its place. The operator may override
this choice.

Model (spot-only, long-or-flat — §0):
  - A strategy emits an intent per closed bar (enter_long / exit / hold). It never sizes (Inv. 3).
  - A signal at bar t fills at bar t+1's OPEN — never on bar t (no look-ahead, §8.4).
  - Sizing is a placeholder **full-equity** allocation (long-or-cash); real position sizing is
    the risk engine's job and is wired in a later money-code slice. This harness proves the
    signal + cost + reproducibility path, not sizing.
  - Costs: taker fee on each fill + slippage moving the fill price against us (§8.3).
  - Reproducibility (§8.8) comes from a pinned single pair (StaticPairlist principle) and a
    pure function of (data, strategy, costs).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from src.features.indicators import atr as atr_fn
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints, compute_size
from src.risk.types import PortfolioState
from src.strategy.base import INTENT_ENTER_LONG, INTENT_EXIT


@dataclass(frozen=True)
class RiskSizing:
    """Size entries through the live risk path (risk.sizing.compute_size) instead of the
    full-equity placeholder. Supplying this makes the backtest and live size identically."""

    cfg: RiskConfig
    market: MarketConstraints
    pair: str = "BACKTEST"
    atr_period: int = 14
    atr_stop_mult: float = 2.0


@dataclass(frozen=True)
class Costs:
    """Backtest cost model (§8.3). Fees/slippage are fractions (0.001 = 0.10%)."""

    taker_fee: float = 0.001
    maker_fee: float = 0.001
    slippage: float = 0.0005

    @classmethod
    def from_dict(cls, d: dict) -> "Costs":
        return cls(
            taker_fee=float(d["taker_fee"]),
            maker_fee=float(d.get("maker_fee", d["taker_fee"])),
            slippage=float(d["slippage"]),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Costs":
        return cls.from_dict(json.loads(Path(path).read_text()))


class _SupportsSignals(Protocol):
    def generate_signals(self, df: pd.DataFrame) -> pd.Series: ...


@dataclass(frozen=True)
class BacktestResult:
    trades: pd.DataFrame  # entry_time, entry_price, exit_time, exit_price, return
    equity_curve: pd.Series  # equity marked at each bar close, indexed by timestamp
    stats: dict


_TRADE_COLUMNS = ["entry_time", "entry_price", "exit_time", "exit_price", "qty", "return"]


def run_backtest(
    df: pd.DataFrame,
    strategy: _SupportsSignals,
    *,
    costs: Costs | None = None,
    initial_equity: float = 1.0,
    risk: RiskSizing | None = None,
) -> BacktestResult:
    """Run a deterministic spot long-or-flat backtest. See module docstring for the model.

    With ``risk`` set, each entry is sized via the live risk path (compute_size): inverse-ATR,
    per-trade-capped, and SKIPPED when sub-minimum. Without it, the full-equity placeholder is
    used (a test fixture for the engine mechanics, not a production sizing policy).
    """
    costs = costs or Costs()
    signals = strategy.generate_signals(df).to_numpy()
    ts = pd.to_datetime(df["timestamp"], utc=True).to_numpy()
    opens = df["open"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    closes = df["close"].to_numpy(dtype="float64")
    atr_series = atr_fn(df, risk.atr_period).to_numpy() if risk else None
    n = len(df)

    cash = float(initial_equity)
    units = 0.0
    in_position = False
    entry_time = entry_price = entry_equity = None
    stop_price: float | None = None  # protective stop for the open position (risk mode)
    trades: list[dict] = []
    equity_curve = []

    def _open_position(i: int) -> None:
        """Open a long at bar i's open. Returns silently without opening if risk sizing skips."""
        nonlocal cash, units, in_position, entry_time, entry_price, entry_equity, stop_price
        fill = opens[i] * (1 + costs.slippage)
        if risk is None:
            # full-equity placeholder: deploy all cash (fee taken from cash)
            qty = (cash - cash * costs.taker_fee) / fill
            spend = cash
        else:
            atr_i = atr_series[i]
            if not (atr_i > 0):  # NaN warmup or zero vol → no risk-based size
                return
            state = PortfolioState(equity=cash, peak_equity=cash, day_start_equity=cash,
                                   quote_price=1.0)
            sized = compute_size(
                pair=risk.pair, price=opens[i], atr=atr_i, state=state,
                cfg=risk.cfg, market=risk.market, atr_stop_mult=risk.atr_stop_mult,
            )
            if not sized.feasible:
                return  # SKIP — never round up to meet the minimum (§4/§9)
            qty = sized.qty
            spend = qty * fill * (1 + costs.taker_fee)
            if spend > cash:
                return  # unaffordable (defensive; risk sizing keeps this well under cash)
        entry_equity = cash
        cash -= spend
        units = qty
        in_position = True
        entry_time, entry_price = ts[i], fill
        # protective stop at entry − atr_stop_mult×ATR (risk mode only). This is the same stop
        # the live system places exchange-side (§4), so the backtest realizes the modeled risk
        # instead of riding a loser to the exit signal.
        stop_price = (fill - risk.atr_stop_mult * atr_i) if risk else None

    def _close_position(i: int, price: float) -> None:
        nonlocal cash, units, in_position, stop_price
        fill = price * (1 - costs.slippage)
        proceeds = units * fill
        cash += proceeds * (1 - costs.taker_fee)
        trades.append(
            {
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": ts[i],
                "exit_price": fill,
                "qty": units,
                "return": cash / entry_equity - 1.0,
            }
        )
        units = 0.0
        in_position = False
        stop_price = None

    for i in range(n):
        if i > 0:
            # 1) protective stop takes priority over the signal: a gap below the stop fills at the
            #    (worse) open; an intrabar pierce fills at the stop price. Slippage applied on exit.
            stopped = False
            if in_position and stop_price is not None:
                if opens[i] <= stop_price:
                    _close_position(i, opens[i])
                    stopped = True
                elif lows[i] <= stop_price:
                    _close_position(i, stop_price)
                    stopped = True
            # 2) otherwise act on the PREVIOUS bar's signal at this bar's open (no look-ahead)
            if not stopped:
                prev = signals[i - 1]
                if prev == INTENT_ENTER_LONG and not in_position:
                    _open_position(i)
                elif prev == INTENT_EXIT and in_position:
                    _close_position(i, opens[i])
        equity_curve.append(cash + units * closes[i])

    # force-close any open position at the last bar's close so equity/trades are realized
    if in_position:
        _close_position(n - 1, closes[n - 1])
        equity_curve[-1] = cash

    trades_df = pd.DataFrame(trades, columns=_TRADE_COLUMNS)
    eq = pd.Series(equity_curve, index=pd.to_datetime(df["timestamp"], utc=True), name="equity")
    return BacktestResult(trades=trades_df, equity_curve=eq, stats=_compute_stats(eq, trades_df))


def _compute_stats(equity: pd.Series, trades: pd.DataFrame) -> dict:
    """Basic, reproducible stats. Full metric suite (Calmar/Sharpe/Sortino) lands in Slice 7."""
    final_equity = float(equity.iloc[-1]) if len(equity) else 0.0
    initial = float(equity.iloc[0]) if len(equity) else 0.0
    total_return = (final_equity / initial - 1.0) if initial else 0.0

    max_drawdown = 0.0
    if len(equity):
        peak = equity.cummax()
        max_drawdown = float((equity / peak - 1.0).min())

    return {
        "trade_count": int(len(trades)),
        "final_equity": final_equity,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
    }
