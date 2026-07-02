#!/usr/bin/env python3
"""Search a funding-rate signal for a *validated* edge (P2 §5) — a different information source.

TA, cross-sectional momentum, and regime filtering all failed the deflated-Sharpe gate
(docs/research/strategy_search_findings.md). Funding rate is the next honest thing to try: it is
perp-market *positioning*, not price. This runs `FundingCarry` (spot long-or-flat, contrarian to
crowd funding) through the SAME walk-forward + Deflated-Sharpe gate, sharing one journal so the
multiple-testing correction counts the variants together (§5), and requires beating buy-and-hold.

Data (ADR 0002): OHLCV for the spot ``--pair`` (the thing we'd trade), funding for the perp
``--perp`` (the signal). Bybit is reachable from the operator's machine (not the cloud build env,
which is geo-blocked). No leverage, no shorting, no real money — research only.

    python scripts/funding_edge_search.py --exchange bybit --pair BTC/USDT \
        --perp BTC/USDT:USDT --timeframe 1h --days 730
"""
from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import ccxt

from backtest.benchmark import buy_and_hold
from backtest.hypothesis import validate_hypothesis
from backtest.runner import Costs
from src.core.console import force_utf8_stdio
from src.data import feed, funding, quality
from src.llm.journal import HypothesisJournal
from src.strategy.funding_carry import FundingCarry

ROOT = Path(__file__).resolve().parents[1]

# small, fixed variant set — keep the §5 multiple-testing budget honest
CANDIDATES = [
    ("funding<=0", {"entry_threshold": 0.0, "smooth": 1}),
    ("funding<=0 sm3", {"entry_threshold": 0.0, "smooth": 3}),
    ("funding<=-1bp", {"entry_threshold": -0.0001, "smooth": 1}),
    ("funding<=+1bp", {"entry_threshold": 0.0001, "smooth": 1}),
]


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="Search a funding-rate edge (§5).")
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--pair", default="BTC/USDT", help="spot pair for OHLCV + the (paper) trade")
    p.add_argument("--perp", default="BTC/USDT:USDT", help="perp symbol for funding-rate history")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--dsr-threshold", type=float, default=0.95)
    args = p.parse_args(argv)

    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    now = ex.milliseconds()
    since = now - args.days * 86_400_000

    df = feed.fetch_ohlcv_history(ex, args.pair, args.timeframe,
                                  since_ms=since, page_limit=720, now_ms=now)
    df = quality.check_quality(df).clean.reset_index(drop=True)
    fdf = funding.fetch_funding_history(ex, args.perp, since_ms=since, now_ms=now)
    df = funding.align_funding(df, fdf)
    have = int(df["funding"].notna().sum())
    print(f"== {args.pair} {args.timeframe} {args.exchange}: {len(df)} clean candles, "
          f"{len(fdf)} funding prints ({have} candles with funding) ==")
    if have < args.min_trades:
        print("!! too little funding coverage to validate — widen --days or check --perp symbol")

    costs = Costs.load(ROOT / "config" / "backtest" / "costs.json")
    bh = buy_and_hold(df, costs=costs)
    print(f"-- baseline buy & hold: total_return {bh['total_return'] * 100:+.1f}%  "
          f"sharpe {bh['sharpe']:.3f}  (an edge must BEAT this) --")

    journal = HypothesisJournal(Path(tempfile.gettempdir()) / "funding_edge_journal.jsonl")
    open(journal.path, "w").close()  # fresh journal for this search
    n = len(df)
    train, test = max(300, n // 5), max(150, n // 10)

    print(f"{'variant':16}{'trades':>8}{'oos_sharpe':>12}{'DSR':>8}  verdict")
    any_edge = False
    for name, params in CANDIDATES:
        rec = validate_hypothesis(
            name=name, params=params, target_regime="any", generated_by="funding_search",
            data=df, build_strategy=lambda _t, pr=params: FundingCarry(**pr),
            journal=journal, now=datetime.now(timezone.utc),
            train_size=train, test_size=test, step=test, costs=costs,
            min_trades=args.min_trades, dsr_threshold=args.dsr_threshold,
        )
        o = rec.outcome
        any_edge = any_edge or rec.status == "validated"
        print(f"{name:16}{o['trade_count']:>8}{(o.get('oos_annual_sharpe') or 0):>12.3f}"
              f"{o['dsr']:>8.3f}  {rec.status.upper()}")

    print(f"\nN trials counted (§5 denominator): {journal.count_trials()}")
    print("VERDICT: " + ("a VALIDATED funding edge — review vs buy&hold before any capital (§14)"
                         if any_edge
                         else "no validated funding edge — go-live stays blocked; keep researching"))
    return 0 if any_edge else 1


if __name__ == "__main__":
    raise SystemExit(main())
