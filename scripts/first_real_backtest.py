#!/usr/bin/env python3
"""Harness validation on REAL OHLCV (review rec #3).

Pulls real BTC/USDT 1h candles, runs them through the full P0 pipeline
(feed → quality gate → single feature path → dumb strategy → risk-sized backtest →
walk-forward/metrics), and proves the stored data backtests reproducibly (§8.8).

PROVISIONAL DATA SOURCE: the trading venue is Binance Japan (ADR 0002), which is not reachable
from this environment (Binance returns HTTP 451 here) and may not list BTC/USDT. Kraken is used
ONLY to exercise the pipeline on real market data. Closing the P0 DONE-GATE for real requires
running this on the operator's actual venue, from an environment that can reach it, with enough
history for a ≥100-trade multi-regime sample. The cost model is our Binance VIP0+BNB config and
does NOT reflect Kraken's fees — fine for harness validation, not for an edge claim.
"""
from __future__ import annotations

import os
from pathlib import Path

import ccxt

from backtest.metrics import compute_metrics, meets_sample_size
from backtest.runner import Costs, RiskSizing, run_backtest
from backtest.walkforward import walk_forward
from src.data import feed, quality, store
from src.risk.config import RiskConfig
from src.risk.sizing import MarketConstraints
from src.strategy.ema_cross import EmaCross

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "parquet" / "btc_usdt_1h_kraken.parquet"
EXCHANGE_ID = "kraken"
PAIR = "BTC/USDT"
TIMEFRAME = "1h"


def fetch_real_ohlcv(limit=720):
    ex = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})
    proxy = os.environ.get("HTTPS_PROXY")
    if proxy:
        ex.https_proxy = proxy
    raw = ex.fetch_ohlcv(PAIR, timeframe=TIMEFRAME, limit=limit)
    return feed.to_closed_frame(raw, TIMEFRAME)


def main():
    from src.core.console import force_utf8_stdio

    force_utf8_stdio()
    print(f"== fetch real {PAIR} {TIMEFRAME} from {EXCHANGE_ID} ==")
    df = fetch_real_ohlcv()
    print(f"  fetched closed candles: {len(df)}  "
          f"[{df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}]")

    qr = quality.check_quality(df)
    print(f"== quality gate ==  clean={len(qr.clean)}  quarantined={len(qr.quarantined)}")
    if not qr.quarantined.empty:
        print("  reasons:", qr.quarantined['reason'].value_counts().to_dict())

    store.write_ohlcv(qr.clean, DATA)
    reloaded = store.read_ohlcv(DATA)
    print(f"== stored + reloaded == rows={len(reloaded)}  (pinned for reproducibility)")

    cfg = RiskConfig.load(ROOT / "config" / "risk" / "default.json")
    costs = Costs.load(ROOT / "config" / "backtest" / "costs.json")
    market = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)  # approx (not Binance)
    risk = RiskSizing(cfg=cfg, market=market, pair=PAIR, atr_period=14, atr_stop_mult=2.0)
    strat = EmaCross.from_config({"params": {"ema_fast": 12, "ema_slow": 26}})

    a = run_backtest(reloaded, strat, costs=costs, initial_equity=10000.0, risk=risk)
    b = run_backtest(reloaded, strat, costs=costs, initial_equity=10000.0, risk=risk)
    reproducible = a.trades.equals(b.trades) and a.equity_curve.equals(b.equity_curve)
    print(f"== backtest (risk-sized) == reproducible={reproducible}  trades={len(a.trades)}")

    m = compute_metrics(a.equity_curve, a.trades)
    print("== metrics ==")
    for k in ("total_return", "cagr", "max_drawdown", "calmar", "sharpe", "sortino",
              "win_rate", "profit_factor", "expectancy", "trade_count"):
        print(f"  {k:14} {m[k]}")

    ok = meets_sample_size(a.trades)
    print(f"== sample-size gate (>=100 trades): {ok} ==")
    if not ok:
        print("  → NOT enough trades for a meaningful read (expected on ~30 days of 1h data).")
        print("  → A real P0 close needs more history on the operator's actual venue.")

    wf = walk_forward(reloaded, lambda _t: EmaCross(12, 26), costs=costs,
                      train_size=300, test_size=100, step=100)
    print(f"== walk-forward == folds={len(wf.folds)}  oos_trades={wf.oos_metrics.get('trade_count')}"
          f"  sample_ok={wf.sample_size_ok}")


if __name__ == "__main__":
    main()
