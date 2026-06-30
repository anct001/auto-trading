"""backtest/benchmark.py — buy-and-hold baseline (§8, the honest comparison).

The first question to ask of any active strategy: does it beat just *holding* the asset, net of
cost? For a trending asset like BTC, most simple TA strategies don't — their "return" is really
the asset's drift, and the trading churns it away in fees. This computes the passive baseline:
buy at the first open, hold to the last close, one round-trip cost. Pure/deterministic; the search
prints it next to every candidate so an apparent strategy "edge" is measured against doing nothing.
"""
from __future__ import annotations

import pandas as pd

from backtest.metrics import _max_drawdown, _sharpe, infer_periods_per_year
from backtest.runner import Costs


def buy_and_hold(df: pd.DataFrame, *, costs: Costs | None = None) -> dict:
    """Passive long: buy first open, hold to last close, one round-trip cost. Returns key stats."""
    costs = costs or Costs()
    n = len(df)
    if n == 0:
        return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "periods": 0}
    entry = float(df["open"].iloc[0]) * (1.0 + costs.slippage)
    exit_ = float(df["close"].iloc[-1]) * (1.0 - costs.slippage)
    gross = (exit_ / entry)
    net_return = gross * (1.0 - costs.taker_fee) ** 2 - 1.0   # taker fee on the buy and the sell
    close = pd.to_numeric(df["close"], errors="coerce")
    rets = close.pct_change().dropna()
    ppy = infer_periods_per_year(pd.to_datetime(df["timestamp"], utc=True))
    equity = close / float(close.iloc[0])
    return {
        "total_return": float(net_return),
        "sharpe": float(_sharpe(rets, ppy)),
        "max_drawdown": float(_max_drawdown(equity)),
        "periods": int(n),
    }
