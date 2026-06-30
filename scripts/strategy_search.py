#!/usr/bin/env python3
"""Search deterministic strategies for a *validated* edge (P2 §5) — the real go-live blocker.

Runs each candidate through the same walk-forward + Deflated-Sharpe gate, sharing ONE journal so
the multiple-testing correction counts them together (§5). Prints, per candidate: OOS trades, OOS
annualised Sharpe, Deflated Sharpe (DSR), and VALIDATED/REJECTED. Honest by design — simple
strategies almost never clear the bar after costs; a green line here would be the first real
edge, not a foregone conclusion.

    python scripts/strategy_search.py [--exchange bybit] [--pair BTC/USDT] [--days 365]

No real money; paper research only.
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
from src.data import feed, quality
from src.llm.journal import HypothesisJournal
from src.strategy.donchian_breakout import DonchianBreakout
from src.strategy.ema_cross import EmaCross
from src.strategy.rsi_reversion import RsiReversion

ROOT = Path(__file__).resolve().parents[1]

CANDIDATES = [
    ("ema_12_26", {"ema_fast": 12, "ema_slow": 26}, "trend", lambda: EmaCross(12, 26)),
    ("ema_5_20", {"ema_fast": 5, "ema_slow": 20}, "trend", lambda: EmaCross(5, 20)),
    ("rsi_14_30_70", {"period": 14, "low": 30, "high": 70}, "range", lambda: RsiReversion(14, 30, 70)),
    ("donchian_20", {"period": 20}, "trend", lambda: DonchianBreakout(20)),
    ("donchian_55", {"period": 55}, "trend", lambda: DonchianBreakout(55)),
]


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="Search strategies for a validated edge (§5).")
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--pair", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--dsr-threshold", type=float, default=0.95)
    args = p.parse_args(argv)

    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    now = ex.milliseconds()
    df = feed.fetch_ohlcv_history(ex, args.pair, args.timeframe,
                                  since_ms=now - args.days * 86_400_000, page_limit=720, now_ms=now)
    df = quality.check_quality(df).clean.reset_index(drop=True)
    print(f"== {args.pair} {args.timeframe} from {args.exchange}: {len(df)} clean candles ==")
    costs = Costs.load(ROOT / "config" / "backtest" / "costs.json")
    bh = buy_and_hold(df, costs=costs)
    print(f"-- baseline buy & hold: total_return {bh['total_return'] * 100:+.1f}%  "
          f"sharpe {bh['sharpe']:.3f}  maxDD {bh['max_drawdown'] * 100:.1f}%  "
          f"(an edge must BEAT this) --")
    journal = HypothesisJournal(Path(tempfile.gettempdir()) / "strategy_search_journal.jsonl")
    open(journal.path, "w").close()  # fresh journal for this search
    n = len(df)
    train, test = max(300, n // 5), max(150, n // 10)

    print(f"{'strategy':16}{'trades':>8}{'oos_sharpe':>12}{'DSR':>8}  verdict")
    any_edge = False
    for name, params, regime, factory in CANDIDATES:
        rec = validate_hypothesis(
            name=name, params=params, target_regime=regime, generated_by="search",
            data=df, build_strategy=lambda _t, f=factory: f(), journal=journal, now=datetime.now(timezone.utc),
            train_size=train, test_size=test, step=test, costs=costs, min_trades=args.min_trades,
            dsr_threshold=args.dsr_threshold,
        )
        o = rec.outcome
        any_edge = any_edge or rec.status == "validated"
        print(f"{name:16}{o['trade_count']:>8}{(o.get('oos_annual_sharpe') or 0):>12.3f}"
              f"{o['dsr']:>8.3f}  {rec.status.upper()}")

    print(f"\nN trials counted (§5 denominator): {journal.count_trials()}")
    print(f"buy & hold over the same window: {bh['total_return'] * 100:+.1f}%")
    print("VERDICT: " + ("a VALIDATED edge found — review vs buy&hold before any capital (§14)"
                         if any_edge
                         else "no validated edge — go-live correctly blocked; keep researching"))
    return 0 if any_edge else 1


if __name__ == "__main__":
    raise SystemExit(main())
