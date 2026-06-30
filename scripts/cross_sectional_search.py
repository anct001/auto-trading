#!/usr/bin/env python3
"""Explore cross-sectional momentum on a real basket (relative-value research, §5).

Fetches a basket of pairs, aligns closes, and runs cross-sectional momentum (hold the strongest
top_k by trailing return) for a few configs, vs an equal-weight buy-and-hold baseline. EXPLORATORY
numbers only — a panel-aware walk-forward + deflated Sharpe is still required before any of this
counts as edge (§5). No real money.

    python scripts/cross_sectional_search.py [--exchange bybit] [--days 365]
"""
from __future__ import annotations

import argparse

import ccxt
import pandas as pd

from backtest.cross_sectional import backtest_cross_sectional
from src.core.console import force_utf8_stdio
from src.data import feed

BASKET = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "BNB/USDT"]


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="Cross-sectional momentum exploration.")
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=365)
    args = p.parse_args(argv)

    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    now = ex.milliseconds()
    since = now - args.days * 86_400_000
    series = {}
    for pair in BASKET:
        try:
            df = feed.fetch_ohlcv_history(ex, pair, args.timeframe, since_ms=since,
                                          page_limit=720, now_ms=now)
            if len(df):
                series[pair] = df.set_index("timestamp")["close"]
        except Exception as e:
            print(f"  skip {pair}: {type(e).__name__}")
    closes = pd.DataFrame(series).dropna()
    print(f"== basket {list(closes.columns)} · {len(closes)} aligned {args.timeframe} candles ==")
    if len(closes) < 60:
        print("not enough aligned history")
        return 1

    # equal-weight buy & hold baseline
    bh = float((closes.iloc[-1] / closes.iloc[0] - 1.0).mean())
    print(f"-- equal-weight buy & hold basket: {bh * 100:+.1f}% --")

    print(f"{'lookback':>9}{'top_k':>6}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}{'turnover':>10}")
    for lookback in (24, 72, 168):       # ~1d / 3d / 1w on 1h bars
        for top_k in (1, 2, 3):
            r = backtest_cross_sectional(closes, lookback=lookback, top_k=top_k, cost=0.00075)
            print(f"{lookback:>9}{top_k:>6}{r['total_return'] * 100:>10.1f}{r['sharpe']:>9.3f}"
                  f"{r['max_drawdown'] * 100:>9.1f}{r['avg_turnover']:>10.3f}")
    print("\nNOTE: exploratory — NOT walk-forward / deflated-Sharpe validated. No edge claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
