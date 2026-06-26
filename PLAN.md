# PLAN.md — Phase 0 (data harness)

> The `autonomous-coding-loop` PLAN artifact (§7). Definition of done for P0 = the **§8 gate**
> in `docs/phases/P0.md` passes, *proven* (not asserted). Each slice below has a **proof
> command** — the cheapest failing signal that shows the slice works. A green backtest number is
> not proof; reproducibility + a matching forward dry-run is.

## ORIENT (settled — see ADR 0002 + PROGRESS.md)

- **Trade venue:** Binance Japan. **View venue:** Binance Global (read-only, UI only — never a
  signal/order path).
- **Pair:** BTC/USDT (⚠️ verify listed on Binance Japan when wiring ccxt; else BTC/JPY).
- **Timeframe:** 1h.
- **Still pending (does not block early slices):** actual fee tier (needed at Slice 6), operator
  KYC/ToS (operator verifies before P4), exact ccxt id/endpoint for the JP entity.

## Reference stack (§2) — deviations justified here

Python 3.11+ · ccxt (data + connectivity) · pandas + pyarrow (Parquet) · Freqtrade
(backtest/dry-run, Slice 6) · pytest/ruff/mypy (dev). vectorbt and Ollama enter in later phases.

---

## Slices (build order per §7: data → quality → features → strategy → harness → dry-run → risk)

### Slice 0 — Dev environment & toolchain
- **Do:** pin runtime deps in `pyproject.toml` (ccxt, pandas, pyarrow), create a venv, install
  `.[dev]`. Confirm the placeholder tests run.
- **Proof:** `pip install -e .[dev] && pytest -q` → the two placeholder tests pass; `ruff check .`
  clean.

### Slice 1 — Data feed: ccxt OHLCV, closed candles only (`src/data/feed.py`)
- **Do:** fetch BTC/USDT 1h OHLCV via ccxt; **drop the still-forming candle** (no look-ahead,
  §8.4); return a typed, UTC, ascending-timestamp frame. Sandbox/testnet by default (§10).
- **Proof:** unit test with a mocked ccxt client asserts (a) the last (open) candle is dropped,
  (b) timestamps are UTC + strictly increasing. `pytest tests/data/test_feed.py -q`.

### Slice 2 — Parquet store, reproducible (`src/data/store.py`)
- **Do:** write/read OHLCV to Parquet with a pinned schema + deterministic ordering; idempotent
  upsert by timestamp.
- **Proof:** round-trip test: write → read → identical frame; re-writing the same range changes
  nothing (byte-stable). `pytest tests/data/test_store.py -q`.

### Slice 3 — Data-quality gate **(TDD — the P0 spine, §8.1)** (`src/data/quality.py`)
- **Do:** quarantine duplicate/out-of-order timestamps, non-positive prices, single-candle
  spikes > N×ATR, and volume anomalies. A quarantined candle is **never** used for a signal.
  Write tests **first** (red → green).
- **Proof:** table-driven tests, one per defect class, assert the bad candle is quarantined and
  good candles pass through untouched. `pytest tests/data/test_quality.py -q`.

### Slice 4 — Single feature path: EMA (`src/features/indicators.py`)
- **Do:** one shared EMA implementation (same code for backtest and live — §15, no train-serve
  skew). Add RSI/MACD/ATR/Bollinger stubs to follow, EMA first (the dumb strategy needs it).
- **Proof:** test EMA against a hand-computed reference series within tolerance; test it consumes
  the §3 quality-gated frame. `pytest tests/features/test_indicators.py -q`.

### Slice 5 — Dumb strategy: EMA crossover (`src/strategy/base.py`, `src/strategy/ema_cross.py`)
- **Do:** `base.py` interface emits intent only (`enter_long`/`exit`/`hold`), declares target
  regime, **never sizes/executes** (Inv. 3). `ema_cross.py` = fast/slow EMA cross from
  `config/strategy/ema_cross.json` (12/26, 1h).
- **Proof:** on a fixed synthetic price series the crossover emits the expected intents at the
  expected bars; no look-ahead (decision at bar *t* uses only data ≤ *t* closed).
  `pytest tests/strategy/test_ema_cross.py -q`.

### Slice 6 — Backtest harness, reproducible (`backtest/runner.py`) **(needs fee tier)**
- **Do:** Freqtrade integration with a **StaticPairlist** (BTC/USDT), the operator's **actual
  fee tier**, and explicit non-zero slippage (§8.3). Pin data/seed/config/pairlist.
- **Proof:** run the backtest twice → **identical** metrics (reproducibility, §8.8). Costs
  non-zero. `python -m backtest.runner --config ... ` twice, diff the results = empty.
  *(Blocked on the fee-tier ORIENT item.)*

### Slice 7 — Walk-forward + metrics (`backtest/walkforward.py`)
- **Do:** out-of-sample split + walk-forward; compute CAGR, max drawdown, Calmar, Sharpe,
  Sortino, win rate, profit factor, expectancy, trade count (drawdown/Calmar primary).
- **Proof:** walk-forward runs across ≥ a multi-regime window and reports the metric set; the
  sample-size gate (≥ ~100 trades) is checked and surfaced. `pytest tests/backtest/ -q`.

### Slice 8 — Forward dry-run wiring (Freqtrade `dry_run: true`)
- **Do:** continuous dry-run on BTC/USDT 1h; capture **signal** metrics for comparison to the
  backtest (validates signal logic, not fills — §2).
- **Proof:** dry-run runs without crashing and logs signal metrics; a short run's signals match
  the backtest's signals on the same window.

> **Risk-engine wiring** (correlation cap, dual daily limits, drawdown kill-switch, exchange-side
> stops) is part of the P0 build order but is **money code → its own TDD-first plan** under
> `tests/risk/**`. It will get a dedicated PLAN section once the data/strategy spine above is
> green, so the risk slices are written test-first against a working pipeline.

---

## P0 DONE-GATE (re-prove from clean — `docs/phases/P0.md`)

After Slice 8: reproducible backtest (StaticPairlist pinned) · costs modeled · no look-ahead ·
OOS + walk-forward · ≥100-trade multi-regime sample · quality gate quarantines bad candles ·
forward dry-run signal metrics ≈ backtest. Then update `PROGRESS.md` and tag the P0 checkpoint.

## Recovery (§7 RECOVER)
Three strikes on a slice → stop, roll back to the last green checkpoint, escalate to the
operator. Never fake a green (never assert, always prove).
