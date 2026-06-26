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
- **Slice 4 (`src/features/indicators.py`, §15 single feature path):** `ema()` (recursive,
  alpha=2/(period+1), causal) + `with_emas()`; the ONE EMA implementation the strategy and
  backtest share (no train-serve skew). **Proof:** `pytest tests/features/test_indicators.py`
  → 7 passed (hand-computed EMA, constant series, period validation, quality-frame integration).
- **Slice 5 (`src/strategy/base.py` + `ema_cross.py`, §5):** `Strategy` base emits intent only
  (enter_long/exit/hold), declares target regime, never sizes/executes (Inv. 3). `EmaCross` =
  fast/slow EMA crossover, long-or-flat. **Proof:** `pytest tests/strategy/test_ema_cross.py`
  → 8 passed incl. a **causality test** (signal at bar t identical on prefix vs full frame →
  no look-ahead). Full suite: **50 passed**, ruff clean.
- **Slice 6 (`backtest/runner.py`, §8):** deterministic backtest — fills at bar t+1 open (no
  look-ahead), taker fee + slippage per fill, full-equity placeholder sizing (real sizing =
  risk engine, later), trades + equity curve + basic stats. **DEVIATION (justified §7):**
  in-house runner instead of Freqtrade for P0; Freqtrade returns at P3 for dry-run parity (see
  PLAN.md Slice 6). Cost model pinned in `config/backtest/costs.json` (VIP0, no BNB, slippage
  0.05%). **Proof:** `pytest tests/backtest/test_runner.py` → 7 passed (reproducible reruns
  §8.8, fill timing, cost math, force-close, real-EmaCross determinism). Full suite: **57
  passed**, ruff clean.
- **Slice 7 (`backtest/metrics.py` + `walkforward.py`, §8.5/§8.7):** full metric suite
  (CAGR, max drawdown, Calmar, Sharpe, Sortino, win rate, profit factor, expectancy, trade
  count — drawdown/Calmar primary; NaN on degenerate, +inf PF on no losses) + chronological OOS
  split + rolling walk-forward where `build_strategy` sees train-only; aggregated OOS trades
  drive the ≥100-trade `sample_size_ok` gate. Deflated-Sharpe deferred to P2 (needs §15 journal
  count). **Proof:** `pytest tests/backtest/` → 15 passed. Full suite: **72 passed**, ruff clean.
- **Costs config:** updated to VIP0 **with BNB discount** → 0.075% maker/taker (`config/backtest/costs.json`).
- **Next:** Slice 8 — forward dry-run wiring (signal metrics ≈ backtest). Then the P0 DONE-GATE
  re-prove, then risk-engine wiring (money code, separate TDD plan).

### 2026-06-26 — risk-engine TDD plan written
- `PLAN_RISK.md`: the dedicated **test-first** plan for `src/risk/**` (money code). Slices:
  ATR prereq → R0 types/config → R1 sizing (incl. sub-minimum SKIP-not-round-up) → R2 hard
  limits → R3 de-peg → R4 exchange assertions → R5 kill-switch/dead-man's → R6 engine (single
  gate) → R7 runtime tighten-only. Encodes Inv. 3/9 (one path, no backdoor), manual-kill
  override, and tighten-never-loosen directly in tests.

### 2026-06-26 — risk BUILD: ATR prereq + R0
- **ATR prereq:** added `true_range()` + `atr()` to `features/indicators.py` (single feature
  path §15); refactored `data/quality.py` to use the canonical `true_range` (one implementation,
  no skew — quality tests unchanged). **Proof:** `pytest tests/features/test_atr.py` → 4 passed.
- **R0 (`src/risk/types.py` + `src/risk/config.py`):** self-validating Order/Position/
  PortfolioState/RiskDecision + RiskConfig loader that refuses leverage>0 (Inv. 4) and soft/hard
  inversion (§15 config-integrity, config-only). **Proof:** `pytest tests/risk/` → 22 passed.
  Full suite: **98 passed**, ruff clean.
- **R1 (`src/risk/sizing.py`, money code §4/§9):** `compute_size()` — inverse-ATR sizing (higher
  ATR → smaller size), fractional-Kelly notional cap, price floored to tick / qty floored to lot,
  minNotional enforced. Sub-minimum or below-lot → `SizingResult(feasible=False, reason=...)`;
  **never rounds up past the risk cap** to meet the exchange minimum. **Proof:**
  `pytest tests/risk/test_sizing.py` → 11 passed incl. the sub-minimum-SKIP drill, lot/tick
  round-DOWN, and a realized-risk-≤-budget property. Full suite: **109 passed**, ruff clean.
- **R2 (`src/risk/limits.py`, money code §4):** boundary-checked pure predicates returning
  `CheckResult(ok, reason)` — gross ≤100%, per-asset ≤25%, concurrency ≤3, daily soft (block
  entries) / hard (halt), drawdown kill −12%, **correlation-cluster** (>0.7-corr positions summed
  as one, cap 40%), portfolio beta (cap passed explicitly — no config default invented). Per-trade
  risk stays in sizing R1. **Proof:** `pytest tests/risk/test_limits.py` → 11 passed. Full suite:
  **120 passed**, ruff clean.
- **R3 (`src/risk/depeg.py`, money code §4):** `assess_depeg()` measures quote deviation from $1
  (epsilon-robust threshold); `check_depeg(..., is_entry)` blocks new entries during a de-peg
  (both directions), allows exits, flags a re-mark. **Proof:** `pytest tests/risk/test_depeg.py`
  → 7 passed. Full suite: **127 passed**, ruff clean.
- **R4 (`src/risk/exchange_assert.py`, money code §4):** `exchange_mismatches()` (pure; a field
  the exchange won't confirm counts as a mismatch) + `assert_exchange_state()` raising
  `ExchangeStateError` listing all mismatches — spot/lev=1/margin-off/futures-off/reduceOnly.
  **Proof:** `pytest tests/risk/test_exchange_assert.py` → 9 passed. Full suite: **136 passed**,
  ruff clean.
- **R5 (`src/risk/killswitch.py`, money code §4):** `KillSwitch` ARMED⇄HALTED — manual kill and
  drawdown kill (first cause preserved; runtime eval can only *add* halts, only `re_arm()`
  clears), plus `evaluate_dead_mans()` (human absent ≥7d → halt; stale process heartbeat →
  recoverable cancel-resting-entries flag, not a full halt). **Proof:**
  `pytest tests/risk/test_killswitch.py` → 8 passed. Full suite: **144 passed**, ruff clean.
- **R6 (`src/risk/engine.py`, money code §4 — the single gate, Inv. 3/9):** `validate()` —
  entries run the full fail-closed gauntlet (killswitch → exchange assert → de-peg →
  daily/drawdown → gross/per-asset/concurrency/correlation/beta → feasibility) with all breach
  reasons aggregated; exits allowed even when halted (flatten works) but rejected if they'd
  exceed held qty (`would_short`, spot-only §0). Same path/verdict for bot + manual UI. **Proof:**
  `pytest tests/risk/test_engine.py` → 13 passed incl. the manual-over-limit drill (§14) and
  multi-breach aggregation. Full suite: **157 passed**, ruff clean.
- **R7 (`src/risk/runtime.py`, money code Inv. 3):** `apply_runtime_override()` returns a
  tightened, re-validated RiskConfig or raises `RuntimeLoosenError` — direction-aware (lower-is-
  tighter caps; negative loss/drawdown limits tighten toward zero); one loosening refuses the
  whole override. **Proof:** `pytest tests/risk/test_runtime_controls.py` → 10 passed. Full
  suite: **167 passed**, ruff clean.

### Risk engine (PLAN_RISK) — R0–R7 COMPLETE
All risk money-code slices done & tested: types/config, sizing, limits, de-peg, exchange
assertions, kill-switch/dead-man's, the single-gate engine, runtime tighten-only.

### 2026-06-26 — integration (a): risk sizing wired into the backtest
- `backtest/runner.py`: new `RiskSizing` policy — supplying it sizes each entry via the live
  `risk.sizing.compute_size` (inverse-ATR, per-trade-capped, **SKIPPED when sub-minimum**)
  instead of the full-equity placeholder (kept as an engine-mechanics test fixture). Backtest and
  live now size through one path (no skew). Trades gained a `qty` column. **Proof:**
  `pytest tests/backtest/test_runner_risk.py` → 5 passed (qty matches compute_size, deploys < full
  equity, respects per-trade budget, sub-minimum skipped, reproducible). Full suite: **172
  passed**, ruff clean.
- **Remaining integration follow-up:** CodeGraph convergence check when `execution/` is built.

### 2026-06-26 — integration (b): append-only event log
- `src/events/log.py` (§15, Inv. 6): `EventLog` append-only JSONL (no update/delete API; file
  only grows), `redact()` scrubs key/secret/token fields **before write** (append-only ⇒
  undeletable), `read_all()` replays, `log_risk_decision()` emits RiskPassed/RiskRejected with
  reasons. **Proof:** `pytest tests/events/` → 9 passed (roundtrip, append-only, secret never in
  raw file, nested/list redaction, persistence, risk-decision emission). Full suite: **181
  passed**, ruff clean.
### 2026-06-26 — integration (c): execution layer started (PLAN_EXECUTION + E1)
- `PLAN_EXECUTION.md`: TDD plan for `execution/**` — E0 types, E1 broker, E2 stops, E3 reconcile,
  E4 convergence. Encodes Inv. 3/9 (broker only submits approved decisions), idempotency,
  precision, exchange-side stops, restart-safety.
- **E1 (`src/execution/broker.py`, money code §9):** `Broker.submit(decision, ...)` refuses any
  unapproved decision (exchange never touched — Inv. 3), idempotent per client_order_id (no
  double-trade), floors qty/price to lot/tick via the shared `floor_to_step`, sets TIF, tracks
  partial fills. Promoted `sizing.floor_to_step` to public (shared precision). **Proof:**
  `pytest tests/execution/test_broker.py` → 6 passed. Full suite: **187 passed**, ruff clean.
- **E2 (`stops.py`):** reduceOnly protective stop attached at fill (`protective_stop_price` =
  entry − mult×ATR, idempotent). 5 tests.
- **E3 (`reconcile.py`):** restart-safe — exchange truth wins, adopts unknown positions, drops
  orphan local orders (no double-trade), flags naked positions (no reduceOnly stop). 8 tests.
- **E4 (convergence):** integration test — approved order reaches the exchange; over-limit/
  halted/unsized decisions are refused by the broker (exchange untouched). Inv. 3/9 end-to-end.
  4 tests.

### Execution layer (PLAN_EXECUTION) — E1–E4 COMPLETE
broker + stops + reconcile + convergence, all money-code TDD. **Full suite: 203 passed**, ruff
clean. Remaining: when CodeGraph is rebuilt, confirm the graph shows every order path through
`risk/engine`.
- **Next (sequential, per review rec #2):** `core/config.py` — config hash-lock so §15 config
  integrity actually takes effect (currently `risk/config.py` validates values but no file is
  hash-locked).

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
