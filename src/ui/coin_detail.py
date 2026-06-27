"""src/ui/coin_detail.py — §12 coin-detail surface (read-only).

The per-coin research view: a candlestick (K-line) series with EMA overlays, plus the indicator
readouts (the bot consumes raw indicator *values* in code, §12 — the chart is for the human). Pure
and deterministic over one pair's OHLCV; never a signal path. Pairs naturally with the agent-view
overlay (`ui/agent_view.py`) which adds *why the agent is/ isn't acting* on this coin.
"""
from __future__ import annotations

import math

import pandas as pd

from src.features.indicators import ema
from src.ui.screener import readouts_from_ohlcv


def _num(x) -> float | None:
    f = float(x)
    return None if math.isnan(f) else f


def build_coin_detail(
    pair: str, df: pd.DataFrame, *, ema_fast: int = 12, ema_slow: int = 26,
    atr_period: int = 14, max_candles: int = 200,
) -> dict:
    """K-line candles (last ``max_candles``) + EMA overlays + readouts for ``pair`` (read-only)."""
    n = len(df)
    if n == 0:
        return {"pair": pair, "candles": [], "overlays": {"ema_fast": [], "ema_slow": []},
                "readouts": readouts_from_ohlcv(df, ema_fast=ema_fast, ema_slow=ema_slow,
                                                atr_period=atr_period)}

    fast = ema(df["close"], ema_fast)
    slow = ema(df["close"], ema_slow)
    start = max(0, n - max_candles)
    candles = [
        {"t": str(df["timestamp"].iloc[i]), "time": int(df["timestamp"].iloc[i].timestamp()),
         "o": float(df["open"].iloc[i]), "h": float(df["high"].iloc[i]),
         "l": float(df["low"].iloc[i]), "c": float(df["close"].iloc[i]),
         "v": float(df["volume"].iloc[i])}
        for i in range(start, n)
    ]
    return {
        "pair": pair,
        "candles": candles,
        "overlays": {
            "ema_fast": [_num(fast.iloc[i]) for i in range(start, n)],
            "ema_slow": [_num(slow.iloc[i]) for i in range(start, n)],
        },
        "readouts": readouts_from_ohlcv(df, ema_fast=ema_fast, ema_slow=ema_slow,
                                        atr_period=atr_period),
    }
