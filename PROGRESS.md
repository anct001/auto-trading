# PROGRESS.md — current phase + checkpoint state

> Updated every session (§7 CHECKPOINT). This is the single source of truth for "where are we".

## Current phase

**P0 — data harness** (capital: none; LLM role: none).

**Entry gate to advance to P1 (§6 / §8):** clean data layer + reproducible backtest + dry-run
harness + 1 dumb strategy, all *proven* (not asserted). A green backtest number is not proof.

## Checkpoint log

### 2026-06-26 — repository bootstrap (scaffold)
- Initialized the repository on branch `claude/new-session-vcxyyp`.
- Committed the three orientation docs into canonical locations:
  - `CLAUDE.md` (agent entrypoint / invariants), with `AGENTS.md` + `GEMINI.md` aliases.
  - `docs/MASTER_DRIVER.md` (canonical spec v0.6).
  - `docs/CODEBASE_MAP.md`.
- Laid down the full `src/` package tree as **stub modules** — docstrings citing the spec §,
  no trading logic yet (per CODEBASE_MAP).
- Added `backtest/`, `config/{strategy,risk}/`, `tests/{risk,execution}/`, `ops/`,
  `docs/{adr,phases}/` scaffolding.
- Added `.gitignore` (secrets/§10, user_data, parquet caches, runtime state), `pyproject.toml`,
  `README.md`, and per-phase DONE-GATE docs in `docs/phases/`.

**Proven:** nothing yet — this is structure only.
**Not done:** no data layer, no quality gate, no strategy, no risk logic, no tests.

### 2026-06-26 — P0 BUILD: Slice 0 + Slice 1
- **Slice 0 (dev env):** pinned runtime deps in `pyproject.toml` (ccxt 4.5, pandas, pyarrow,
  numpy); installed `.[dev]`. Proof: `pytest -q` green, `ruff check .` clean.
- **Slice 1 (`src/data/feed.py`):** ccxt OHLCV fetch returning **closed candles only** — the
  still-forming candle is dropped (no look-ahead, §8.4); UTC tz-aware, ascending, numeric.
  Mockable via injected exchange. **Proof:** `pytest tests/data/test_feed.py -q` → 18 passed
  (forming-candle drop, close-boundary keep, UTC+monotonic, timeframe parsing, empty, fetch
  wiring). Full suite: 20 passed, ruff clean.
- **Slice 2 (`src/data/store.py`):** reproducible Parquet write/read/upsert; canonical form
  (UTC µs timestamps, sorted, deduped last-wins); idempotent upsert, conflict-newer-wins.
  **Proof:** `pytest tests/data/test_store.py` → 6 passed.
- **Slice 3 (`src/data/quality.py`, §8.1 — the gate):** partitions OHLCV into clean vs
  quarantined with a reason — duplicate/out-of-order timestamps, non-positive prices,
  OHLC inconsistency, negative volume, >N×ATR spikes. A quarantined candle never reaches the
  clean frame (so never a signal/trade). **Proof:** `pytest tests/data/test_quality.py` →
  9 passed (one per defect class + clean passthrough + empty). Full suite: **35 passed**, ruff
  clean.
- **Next:** Slice 4 — `src/features/indicators.py` (single feature path: EMA first).

## ORIENT decisions (§7 — being filled in with the operator)

| Item | Status | Decision |
|------|--------|----------|
| Exchange entity | ✅ decided | **Trade = Binance Japan**; **view = Binance Global** (read-only, operator UI only). See ADR 0002. |
| Target pairs | ✅ decided (1 caveat) | **BTC/USDT**. ⚠️ UNVERIFIED: confirm it's listed on Binance Japan when wiring ccxt (Japan quotes mainly in JPY); fallback **BTC/JPY**. |
| Timeframe | ✅ decided | **1h**. |
| Actual fee tier | ⛔ pending | VIP level / BNB & maker-taker discounts — wrong tier biases the backtest (§8.3). Needed before the backtest harness slice, not before the data slice. |
| Operator KYC / ToS | ⛔ operator to verify | Confirm eligibility on Binance Japan + that its ToS permits API/bot spot trading (§11). |
| Exact ccxt id/endpoint for the JP entity | ⛔ pending | Verify against current docs — do NOT invent (§7/§10). |

## Next slice (P0, first work item)

Per §7 build order — **data → quality gate → harness → dumb strategy → dry-run → risk wiring**.
Recommended first slice: `src/data/feed.py` + `src/data/store.py` + `src/data/quality.py` with
TDD on the quality gate (§8.1), proven by a reproducible OHLCV pull + quarantine unit tests.

**Ready to start:** pair (BTC/USDT), timeframe (1h) decided — enough to begin the data → quality
gate slice on dry-run/testnet data. Fee tier + the BTC/USDT-on-Japan check are only needed
before the **backtest harness** slice (§8.3) and **P4** respectively, so they don't block the
first slice. Next: write `PLAN.md` (slices, each with a proof command), then build.

## Phase ledger

| Phase | State | Gate |
|-------|-------|------|
| P0 data harness | **in progress (scaffold only)** | §8 proven |
| P1 LLM sentiment feature | not started | forward-validated ablation |
| P2 hypothesis generation | not started | ≥1 LLM strategy walk-forward validated |
| P3 monitor/orchestrate | not started | ≥30d dry-run, signal metrics ≈ backtest |
| P4 tiny real capital | not started | §14 go-live checklist + human sign-off |
| P5 scale | not started | sustained live perf + capacity + sign-off |
