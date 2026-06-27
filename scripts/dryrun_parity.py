#!/usr/bin/env python3
"""§8.9 signal-parity proof via historical replay (P0 DONE-GATE last item).

The forward dry-run takes ≥N days of wall-clock; this proves the *same* invariant now by replay.
The live dry-run computes the strategy intent on a rolling window (limit candles) ending at the
latest closed candle; the backtest computes it over the full series. If the two ever disagree on
a shared candle, the live path and the backtested path are not the same logic (§2/§8.9) — a P0
blocker. This replays the rolling-window decision over real history and diffs it against the
full-series backtest signal.

Usage:  python scripts/dryrun_parity.py [--exchange bybit] [--pair BTC/USDT] [--days 365]
Exit code 0 on parity, 1 on a mismatch (so it can gate CI / the DONE-GATE).
"""
from __future__ import annotations

import argparse
import sys

import ccxt

from backtest import parity
from src.core.console import force_utf8_stdio
from src.data import feed, quality
from src.strategy.ema_cross import EmaCross


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="Replay-based dry-run vs backtest signal parity (§8.9).")
    p.add_argument("--exchange", default="bybit", help="ccxt id for the OHLCV feed (deep history)")
    p.add_argument("--pair", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--window", type=int, default=200, help="rolling window the live dry-run uses")
    p.add_argument("--ema-fast", type=int, default=12)
    p.add_argument("--ema-slow", type=int, default=26)
    args = p.parse_args(argv)

    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    now_ms = ex.milliseconds()
    since_ms = now_ms - args.days * 86_400_000
    df = feed.fetch_ohlcv_history(ex, args.pair, args.timeframe, since_ms=since_ms,
                                  page_limit=720, now_ms=now_ms)
    df = quality.check_quality(df).clean.reset_index(drop=True)
    print(f"== {args.pair} {args.timeframe} from {args.exchange}: {len(df)} clean candles "
          f"[{df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}] ==")

    strat = EmaCross(args.ema_fast, args.ema_slow)

    # Backtest signal series: the strategy over the FULL series (ground truth).
    backtest_sig = parity.backtest_signals(df, strat)

    # Dry-run replay: at each candle, the live loop sees only a rolling window ending there and
    # acts on that window's last intent. Reproduce that exactly.
    n = len(df)
    warmup = args.window  # only compare once the rolling window is full (matches live steady state)
    dryrun_sig = {}
    for i in range(warmup, n):
        # Reset the index so the window looks exactly like the freshly-fetched 0-indexed frame
        # the live dry-run feeds (DryRunner re-fetches each tick, then check_quality resets index).
        sub = df.iloc[i - args.window + 1: i + 1].reset_index(drop=True)
        intent = str(strat.generate_signals(sub).iloc[-1])
        dryrun_sig[df["timestamp"].iloc[i]] = intent

    report = parity.compare_signals(dryrun_sig, backtest_sig)
    print(report.summary())
    return 0 if report.is_parity else 1


if __name__ == "__main__":
    sys.exit(main())
