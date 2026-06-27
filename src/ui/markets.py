"""src/ui/markets.py — §12 markets overview / watchlist + heatmap (operator-facing, read-only).

A sortable/filterable multi-asset overview and a heatmap, for the operator to see "where the
market is moving" at a glance. These are **research surfaces, never a signal path** (§12): the bot
makes no trade decision from them. A pair surfaced here is only a research candidate — it still
has to pass backtest + walk-forward (§5/§8) before it can join the traded set.

Pure and deterministic over injected OHLCV (one frame per pair), so it is testable without a feed.
Rows reuse the single-feature-path readouts (`screener.readouts_from_ohlcv`) and can be filtered
with the same `screener` conditions. We have no market-cap source from OHLCV, so heatmap tiles are
sized by a traded-volume proxy (colored by change) rather than cap.
"""
from __future__ import annotations

import pandas as pd

from src.ui.screener import readouts_from_ohlcv


def market_row(pair: str, df: pd.DataFrame, *, ema_fast: int = 12, ema_slow: int = 26,
               atr_period: int = 14, lookback: int = 24) -> dict:
    """One overview row for ``pair`` from its OHLCV frame."""
    r = readouts_from_ohlcv(df, ema_fast=ema_fast, ema_slow=ema_slow,
                            atr_period=atr_period, lookback=lookback)
    vol = float(df["volume"].iloc[-lookback:].sum()) if len(df) else 0.0
    return {
        "pair": pair,
        "close": r["close"],
        "change_pct": r["return_lookback_pct"],
        "atr_pct": r["atr_pct"],
        "trend_up": r["ema_fast_above_slow"],
        "volume": vol,
    }


def build_markets_overview(
    universe: dict[str, pd.DataFrame], *, sort_by: str = "change_pct", descending: bool = True,
    ema_fast: int = 12, ema_slow: int = 26, atr_period: int = 14, lookback: int = 24,
) -> list[dict]:
    """Build the overview rows for every pair, sorted by ``sort_by`` (default: change desc)."""
    rows = [market_row(pair, df, ema_fast=ema_fast, ema_slow=ema_slow,
                        atr_period=atr_period, lookback=lookback)
            for pair, df in universe.items()]
    rows.sort(key=lambda r: _sort_key(r.get(sort_by)), reverse=descending)
    return rows


def _sort_key(v):
    # push NaN/None to the bottom regardless of direction
    if v is None or (isinstance(v, float) and v != v):
        return float("-inf")
    if isinstance(v, bool):
        return int(v)
    return v


def top_gainers(rows: list[dict], n: int = 10) -> list[dict]:
    return sorted(rows, key=lambda r: _sort_key(r["change_pct"]), reverse=True)[:n]


def top_losers(rows: list[dict], n: int = 10) -> list[dict]:
    return sorted(rows, key=lambda r: _sort_key(r["change_pct"]))[:n]


def heatmap_tiles(rows: list[dict]) -> list[dict]:
    """Tiles sized by the traded-volume proxy, colored by change — largest first."""
    tiles = [{"pair": r["pair"], "size": r["volume"], "change_pct": r["change_pct"]} for r in rows]
    tiles.sort(key=lambda t: _sort_key(t["size"]), reverse=True)
    return tiles
