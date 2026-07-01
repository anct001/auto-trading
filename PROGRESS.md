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
### 2026-06-26 — review rec #2: config hash-lock (§15 now enforced)
- `src/core/config.py`: `hash_config_file` (canonical SHA-256, formatting/key-order independent),
  `build_manifest`/`verify_configs` (raise `ConfigIntegrityError` on drift or unlocked file),
  `write_manifest`/`load_manifest`. Applies to declarative config files ONLY (never runtime
  state/tightening — structural: it only takes file paths).
- `config/config.lock.json`: committed lockfile for `config/risk/default.json`,
  `config/strategy/ema_cross.json`, `config/backtest/costs.json`. A test verifies the committed
  config matches the lock (catches drift at startup/CI). **§15 config integrity is now live.**
  Re-lock (regenerate) on any intentional config change + human sign-off.
- **Proof:** `pytest tests/core/` → 7 passed. Full suite: **210 passed**, ruff clean.
### 2026-06-26 — review rec #3: first real-data run (harness validated, P0 NOT closed)
- `scripts/first_real_backtest.py`: pulls real BTC/USDT 1h OHLCV and runs the full P0 pipeline
  end-to-end. **First time the code touched real market data.**
- **Binance is unreachable here (HTTP 451, restricted location)** — the actual venue (Binance
  Japan, ADR 0002) cannot be reached from this environment. Used **Kraken BTC/USDT as a
  PROVISIONAL data source** to validate the harness only.
- **Result (719 real closed candles, ~30 days):** quality gate clean (0 quarantined); stored +
  reloaded reproducibly; **risk-sized backtest reproducible=True**, 8 trades; metrics computed
  (Calmar 4.4 / Sharpe 0.65 — but over 8 trades = noise); **sample-size gate correctly = False**
  (8 ≪ 100); walk-forward 4 folds / 7 OOS trades / sample_ok=False.
- **What this proves:** feed→quality→store→features→strategy→risk-sized backtest→walk-forward all
  work on real OHLCV and are reproducible (§8.8). The §8 discipline holds: a green-looking number
  over a tiny sample is rejected by the gate.
- **What it does NOT prove / P0 still NOT closed:** wrong venue (Kraken, not Binance Japan);
  Binance fees applied to Kraken data; far too few trades (need ≥100, multi-regime); no forward
  dry-run. **Closing P0 requires running on the operator's actual venue, from an environment that
  can reach it, with enough history** — an operational step, not more code.

### Review recommendations #1–#3 — DONE
(1) execution E1–E4 complete; (2) config hash-lock live (§15); (3) harness validated on real
data. Remaining big gaps below.

### 2026-06-27 — core foundations complete (§9 + §10)
- `src/core/secrets.py` (§10): `Secret` wrapper whose repr/str/format are masked (logging or
  event-logging a secret yields "***"; value only via explicit `.reveal()`); `load_secret`
  (raises `MissingSecretError`), `load_optional`, `mask`. 8 tests.
- `src/core/clock.py` (§9): `check_skew`/`assert_clock_sane` (injected reference source) raise
  `ClockSkewError` beyond tolerance — skew is a circuit breaker, never trade on a skewed clock;
  `now_ms`. 7 tests.
- **Proof:** `pytest tests/core/` → 22 passed. Full suite: **225 passed**, ruff clean.
- **core/ is now fully implemented** (config hash-lock + secrets + clock). Remaining stubs:
  `features/cache.py`, `llm/**`, `strategy/shadow.py`, `ui/api.py`.

### 2026-06-27 — keystone: fast-loop orchestrator (§3)
- `src/fast_loop.py`: `FastLoop.tick(df, state, ctx)` composes ONE closed-candle decision —
  quality gate → single feature path (ATR) → strategy intent → sizing → **risk gate** → broker →
  exchange-side protective stop → event log. Every order still passes `engine.validate`
  (Inv. 3/9); reads slow-loop inputs as optional/non-vetoing (none until P1); deterministic.
- Integration-tested with the REAL components (engine/sizing/broker/stops/event log), faking only
  the exchange. **Proof:** `pytest tests/test_fast_loop.py` → 5 passed (enter flow sizes→
  validates→submits→attaches reduceOnly stop + logs the event chain; halt blocks + logs
  RiskRejected; hold no-op; infeasible size skipped; exit sells the held position). Full suite:
  **230 passed**, ruff clean.
- **Design note observed:** inverse-ATR sizing caps at fractional-Kelly (50%), but the engine's
  per-asset cap (25%) is tighter — in unrealistically low-vol data the sized order can exceed
  per-asset and the engine (correctly) rejects it. Safe (sizing proposes, engine disposes); a
  future tuning could clamp sizing to per-asset to avoid futile rejections.

### 2026-06-27 — security/correctness audit + fixes (all 13 findings)
Adversarial self-audit of the money code; fixed test-first (red→green). High: **#1** risk caps
fail-OPEN on missing data → now fail-closed (unknown correlation = correlated; `quote_price`
required); **#2** the single gate was forgeable (broker trusted any `approved=True`) → engine now
mints an unforgeable `Approval` capability the broker verifies; **#3** backtest ignored the
protective stop → now enforces the same stop (gap/intrabar), so realized risk matches the model.
Medium: **#4** event-log redaction was name-only → now redacts secret *values* (credentialed
URLs, AIza/AKIA/sk-/ghp_ tokens); **#5** missing price KeyError-crashed the gate → now rejects
`missing_price` (fail-closed); **#6** protective stop could be ≤0 → rejected, loop won't open an
unprotectable position. Low: **#8** `re_arm` now requires an operator identity; **#9** reconcile
detects partial (not just absent) stop coverage; **#11** sizing rejects non-positive price, loop
uses an epsilon for "flat". **#7** (in-memory idempotency) documented — cross-restart relies on a
deterministic client id + reconcile (persist deferred to P4). **#12** (exits skip exchange-assert,
intentional for flatten) left as a conscious note. **#13** (feed pagination) — **CLOSED** in the
2026-06-27 local session (`feed.fetch_ohlcv_history`).
Full suite **244 passed**, ruff clean.

### 2026-06-27 — paper dry-run CLI (`src/dry_run.py`)
`python -m src.dry_run` runs the fast loop on closed candles in PAPER mode: OHLCV from a
read-only ccxt feed, orders routed to a SIMULATED paper exchange (no real capital ever leaves
the process), each tick appended to the event log. `PaperAccount` tracks paper equity/positions
and updates from fills. run_once() is the testable unit (4 tests, fakes, no network); run()/main()
add the sleep loop + CLI. Smoke-tested live against Kraka data (1 tick → hold). Full suite **248 passed**, ruff clean.

### 2026-06-27 (local session) — UTF-8 stdio fix, feed pagination (#13), harness clears sample gate
Three things, all on the operator's local Windows machine (which — unlike the cloud env that
wrote HANDOFF — **can reach Binance/Bybit/OKX, no HTTP 451**).
- **UTF-8 stdio (`src/core/console.py`):** the console here is **cp1258 (Vietnamese)**, so a bare
  `print("→")` raised `UnicodeEncodeError` and crashed `dry_run.py` + `first_real_backtest.py`
  mid-run. `force_utf8_stdio()` (display-only) wired into both entrypoints. +2 tests.
- **Feed pagination (#13 closed):** `feed.fetch_ohlcv_history()` loops `fetch_ohlcv` advancing
  `since`, de-dupes by timestamp, stops on no forward progress. +4 tests. Probed venues: Bybit/
  OKX/Binance/KuCoin/Coinbase all serve multi-year 1h; **Kraken caps at ~720** (that was the old
  720-trade ceiling, not our bug).
- **Harness now clears the ≥100-trade gate:** `first_real_backtest.py` switched Kraken→**Bybit**
  (deep-history, non-Binance per ADR 0002). Real run: **21,597 clean candles** (2 spikes
  quarantined, 2024-01..2026-06), reproducible backtest **366 trades** (sample gate TRUE),
  walk-forward **212 folds / 499 OOS**. EMA-cross posts **negative** metrics (ret −22%, Sharpe
  −0.88) under costs — the harness correctly showing **no edge**; not an edge claim, not the real
  P0 close. Full suite **254 passed**, ruff clean.

### 2026-06-27 (local session, cont.) — venue probe + §8.9 signal-parity tool & proof
- **ccxt has no Binance Japan entity** (probed 4.5.60): `binance`→Global, plus `binanceus` /
  futures. JFSA venues in ccxt: bitbank/bitflyer/coincheck/zaif. Recorded in ADR 0002 "Findings";
  operator must pin a venue before P4. Doesn't block P0 (pipeline venue-agnostic).
- **§8.9 signal parity (`backtest/parity.py`, +7 tests):** parses a dry-run event log into a
  per-candle intent series (pairs MarketReceived+SignalGenerated), computes the backtest's
  full-series intents, diffs on shared candles. Parity = non-empty overlap, zero mismatches.
- **Replay proof (`scripts/dryrun_parity.py`):** proves §8.9 NOW (no 30-day wait) by reproducing
  the live rolling-window decision over real history vs. the full-series backtest. Bybit 1y 1h:
  **8557/8557 candles match (rate=1.0000)** → no train-serve skew. Full suite **261 passed**.

### 2026-06-27 (local session, cont.) — trading venue pinned: bitbank BTC/JPY (ADR 0002 amended)
Operator chose "adapt to a JFSA venue." Probed all four ccxt JFSA venues: **bitbank is the only
one with OHLCV support** (bitflyer/coincheck/zaif return no candles via ccxt). bitbank lists
**BTC/JPY** (active), published spot fees **maker 0.00% / taker 0.10%** (from ccxt market meta).
- **Config switched + re-locked:** `ema_cross.json` venue=bitbank, pairs=["BTC/JPY"];
  `costs.json` = bitbank fees (was binance_japan VIP0+BNB 0.075%); `.env.example`
  TRADING_EXCHANGE_ID=bitbank; `config.lock.json` regenerated. `first_real_backtest.py` docstring
  clarifies data(Bybit, deep)-vs-venue(bitbank) split. ADR 0002 amended with the decision.
- **Proven on the real venue (bitbank BTC/JPY):** `dry_run.py` paper tick OK; `dryrun_parity.py`
  **519/519 (rate=1.0000)** on 30d of bitbank 1h. bitbank serves 1h one-day-per-call (deep pull =
  many calls); no public sandbox (paper/dry-run until P4). Full suite **261 passed**, ruff clean.

### 2026-06-27 (local session, cont.) — P1 LLM sentiment feature BUILT (inert at FLOOR=1.0)
Operator chose "continue to P1, reserve bitbank." Built the whole sentiment pipeline with TDD,
no Ollama needed for tests. The feature is wired but a **strict no-op at the default FLOOR=1.0**
(§6) so the P0 path is byte-for-byte preserved.
- **Consumer:** `llm/sentiment.size_haircut` (multiplier ∈ [floor,1.0], only bearish shrinks,
  never grows/triggers/flips/vetoes, absent/stale→neutral); `llm/state_io` (sentiment_state.json
  r/w, any problem→neutral); `risk/sizing` `size_multiplier` (tighten-only, >1 clamped, backtest
  never passes it — no look-ahead §6); `RiskConfig.sentiment_floor` (default 1.0, [0.5,1.0],
  re-locked); `FastLoop._enter` applies + logs `SentimentHaircut`, fails neutral on any error.
- **Producer (slow loop):** `llm/orchestrator.run_sentiment_cycle` (per-pair classify→clamp→write,
  one pair's failure never breaks the cycle); `llm/ollama_client` (stdlib-urllib Ollama backend,
  format=json, temp 0, bounded tokens; transport injected → parsing tested offline).
- **Wiring:** `dry_run.py --sentiment-state <path>`. Full suite **261→313 passed**, ruff clean.
- **Remaining (operational):** run Ollama, produce real sentiment_state.json, run the ≥30-day
  ablation (feature on vs off) to decide forward if it helps; FLOOR stays 1.0 until it does.

### 2026-06-27 (local session, cont.) — P2 hypothesis-validation machinery BUILT
Built the deterministic anti-data-snooping core of P2 (§5), all tested without Ollama:
- `llm/journal.py` — append-only AI trade/hypothesis journal (§15); `count_trials()` = §5
  denominator (validated|rejected only). 
- `backtest/multiple_testing.py` — Probabilistic + **Deflated Sharpe** (Bailey & López de Prado);
  ships own `norm_cdf`(erf)/`norm_ppf`(Acklam), no scipy. Bar rises with #trials; non-normal
  returns penalised.
- `backtest/hypothesis.validate_hypothesis` — walk-forward → per-trade SR/skew/kurt → DSR vs the
  journal's trial count → VALIDATE iff sample-size gate AND DSR≥threshold; every attempt journalled.
- `llm/hypothesis_gen.py` — offline LLM proposer, **human-gated** (Inv 1/8), strict param budget
  (over-budget dropped); stdlib Ollama backend, transport injected → tested offline.
- Full suite **313→343 passed**, ruff clean. **Remaining (operational):** run proposer → human
  approves → validate on real bitbank BTC/JPY → clear gate with ≥1 net-of-cost+tax validated edge.

### 2026-06-27 (local session, cont.) — P3 deterministic cores BUILT
Built the testable cores of P3 (last paper gate), no web deps, all TDD:
- `strategy/shadow.py` `ShadowBook` — hypothetical P&L from a strategy's intents, never trades;
  long-or-flat; fills at mark (optimistic §2) → relative promotion screen.
- `ui/preview.py` `preview_manual_order` — runs the EXACT `risk.engine.validate` (Inv 9, no UI
  backdoor); verdict == engine; bad input fails closed; + exposure/daily metrics for badges.
- `ui/dashboard/model.py` `build_dashboard` — read-only §12 view: equity, P&L vs soft/hard daily
  limits, drawdown vs kill-switch, gross+per-asset exposure, positions w/ unrealized, kill-switch
  status, slow-loop freshness, decision-log "why" from the event log.
- Full suite **343→361 passed**, ruff clean.

### 2026-06-28 (session) — P3 operator HTTP API transport (stdlib)
`ui/server.py`: dependency-free stdlib-http.server transport over the tested `ui.api` service.
`handle_request` is a PURE router (unit-tested, no sockets) enforcing the §12 contract — read-only
by default; only writes are kill-switch (`/api/killswitch/engage|rearm`) + manual-order preview
(`/api/preview`, exact risk engine, Inv 9); 405 on write-to-read-only, 404 unknown, 400 rearm
w/o operator. `dashboard_sse_frame` for SSE; `serve()` + `build_demo_context()` + `main()` make
`python -m src.ui.server` runnable on a paper snapshot (live smoke verified: dashboard equity +
engage halt). Then added `ui/screener.py` (§12 research screener — condition filter over EMA/ATR
readouts; never trades) and `ui/agent_view.py` (§12 agent-view overlay — per-coin "why acting/not":
signal, regime, sentiment haircut, position, kill-switch; reuses the loop's exact predicates).
Then `index_html()` — a self-contained operator **control dashboard page** (no CDN) served at
`GET /` by the stdlib transport: equity, P&L vs daily limits, drawdown vs kill-switch, exposure,
positions, decision log, + kill-switch engage/re-arm controls (live smoke: 200 text/html).
The §12 **control dashboard is end-to-end viewable**. Then added `ui/markets.py` (markets
overview/watchlist + heatmap logic over injected OHLCV — rows, sort, gainers/losers,
screener-filterable, volume-proxy heatmap tiles) wired as `GET /api/markets` + a `/markets`
watchlist+heatmap page; demo serves a 3-pair universe (live smoke OK). Full suite **376→408**,
ruff clean. The §12 **UI logic is complete** (control dashboard, markets/watchlist/heatmap,
screener, agent-view, all served by the stdlib transport). Then **wired the UI to LIVE dry-run state** (`ui/live.py build_live_context` + `DryRunner.marks()`/
`snapshot_state()` + `dry_run.py --serve-ui`): the dashboard reflects the actual paper portfolio,
the preview runs the real engine over live state (Inv 9), and the UI kill-switch IS the loop's
(engage halts the live loop). Read-only, never blocks the loop (Inv 2). Verified end-to-end (after
a paper entry: position + fee-adjusted equity + decision log on the live HTTP dashboard).
Full suite **412**. Then added **fill-parity harness** (`backtest/fill_parity.py`: `MarkFill` vs
`SlippageLatencyFill` reference + `compare_fills` → optimism-gap report; the §2/Freqtrade-P3 role,
Freqtrade-adapter seam, no dep) and **coin-detail** (`ui/coin_detail.py` K-line + EMA overlays +
readouts; `GET /api/coin?pair=` + `/coin` SVG candlestick page; live smoke 60 candles). Full suite
**426**. Then the **order & trade panel** (`ui/orders_panel.py` read model from the event log:
attempts w/ source + verdict, submissions, fills; `/api/orders` + `/orders` page) and **manual
order placing** (`DryRunner.place_manual` — same risk engine + broker as the bot, Inv 3/9,
lock-serialized vs the loop; `POST /api/order`, 404 when disabled; a preview-gated entry form on
`/orders`). E2E HTTP verified (POST /api/order opens a real paper position). Full suite **439**,
ruff clean.

**§12 operator surface is now COMPLETE** (logic + stdlib transport + all read surfaces +
both writes: kill-switch and the risk-gated manual order; live-wired via `dry_run.py --serve-ui`).
Order-book depth added (`ui/orderbook.py` + `/api/orderbook` + depth panel on `/coin`; demo static
book, live via the view feed's fetch_order_book). Polish pass: CODEBASE_MAP refreshed, dead
`ui/market`+`ui/orders` empty subpackages removed, cross-page nav links. Final live smoke: all 4
pages (/ /markets /coin /orders) 200 + all read APIs ok. Full suite **446**, ruff clean.
**Live-run fix:** a real test surfaced that bitbank returns only ~12 closed 1h candles per fetch
(< atr_period+2) so the live loop returned `insufficient_data` every tick. `DryRunner` now keeps a
rolling buffer, **pre-warmed** once via the paginated feed then merged each tick (prewarm=True
default). Verified: paper dry-run on REAL bitbank BTC/JPY now ticks (MarketReceived +
SignalGenerated).

### 2026-06-28 — dashboard enrichment (gap analysis vs comparable bot dashboards)
Chose to enhance the self-hosted UI (not adopt QuantIDE — see HANDOFF decision). Added, all
self-hosted/no-deps/invariant-safe: **live equity curve** (SVG) + **limit gauges** (Day P&L /
drawdown / gross vs soft/hard/cap); **performance summary** (`ui/performance.py`: win rate,
profit factor [None=∞, JSON-safe — float('inf') breaks browser JSON.parse], expectancy, total
P&L, best/worst); **closed-trades table** (`DryRunner` records round-trips on every sell); **bot
health** (tick_count/last_tick). All wired live (`dry_run.py --serve-ui`) + demo; verified live on
real bitbank (strict-JSON safe). Then added the requested nice-to-haves: **SSE push**
(`GET /api/stream` + EventSource, poll fallback), **agent-view overlay** on the coin page
(`/api/agentview` — signal/acting/blocked/regime/sentiment/position), **flatten button** per
position (force-exit through the risk engine, Inv 9), and a **light/dark theme toggle**. Verified
live on bitbank. Then, learning from a dense multi-widget trading terminal, added **`/terminal`**:
a single-screen CSS-grid that tiles every read surface (KPI strip, watchlist, heatmap, candles
**with volume bars** + EMA, order-book ladder + depth chart, positions w/ flatten, agent-view +
decision log) over the existing JSON APIs, SSE-driven. Also fixed a real gap — `build_live_context`
never wired the coin/markets providers, so `/coin`, `/markets`, and the terminal were blank on a
LIVE run; now sourced from the runner's live frame. Verified live on bitbank (200 candles+volume,
watchlist, 15×15 order book). Then added to the terminal: **scrolling trade tape** (`/api/trades`),
**multi-timeframe chart** (`/api/coin?tf=`, 1h/4h/1d buttons), and **drag-drop tile reordering**
(persisted). Verified live on bitbank (tape 30 rows, 1h 200 candles). NOTE: bitbank errors on
4h/1d via ccxt (fail-soft → "no data"); dense venues serve all timeframes. Full suite **469**.
Then the coin chart was upgraded to **TradingView Lightweight Charts** (v4.2.3, Apache-2.0,
**vendored** to `src/ui/static/`, served same-origin at `/static/` — no CDN, no data leaves):
candles + volume + EMA + crosshair/zoom + tf buttons, over our own `/api/coin`. (TradingView
*data* APIs were rejected — ToS §11 + reproducibility/ADR 0002.) Full suite **471**.
**Remaining (UI):** essentially feature-complete for a solo operator.

### 2026-06-28 — robustness fix found by the live run (P3-relevant)
A live paper run on bitbank **crashed (exit 1) after ~400 ticks** when bitbank reset a connection
(`ConnectionError 10054` → ccxt `NetworkError`) inside `run_once`'s fetch — it propagated out of
`run()` and killed the process. That would have failed the **P3 ≥30-day no-crash gate** and killed
any live loop on a network blip. Fixed: `DryRunner.run()` now catches per-tick errors, records
`last_error` (shown in `health()` + on dashboard/terminal), and skips the tick (fail-closed: no
trade on absent data, §8) — the loop continues. Found by *running it for real*, not a unit test.
Full suite **472**.

### 2026-06-28 — pro-quant gap fill + go-live pre-flight; real-money test DECLINED (by design)
Asked to "test with real money": **declined** per Inv 1 (LLM never places live orders) / Inv 4
(no capital before the P3 gate) / Inv 5 (human sign-off) — and the EMA-cross has **no validated
edge** (−22%/2.5y). Instead, added the legitimate path + pro-quant gaps:
- `backtest/risk_analytics.py` — historical **VaR**, **CVaR/Expected Shortfall**, seeded
  **Monte-Carlo bootstrap** of the max-drawdown distribution (p50/p95/p99).
- `src/core/preflight.py` + `scripts/go_live_preflight.py` — the **§14 go-live readiness gate**:
  auto-verifies in-process invariants (config lock, leverage=0, sentiment_floor=1.0, kill-switch,
  no secret in `.env.example`) and lists the operator-only gates; `is_ready` stays False until ALL
  pass. Places NO orders. Run reports **5/5 auto-pass, 13 operator gates pending → NOT READY**.
- Full suite **472→481**, ruff clean.

### 2026-06-28 — more pro-quant gaps (paper-safe): stress / alerting / metrics
- `backtest/scenario.py` — adversarial **stress tests** (flash_crash/gap_down/vol_spike) run
  through the risk-sized backtest; proves the protective stop caps a flash-crash loss (market
  −30% → realized worst trade > −15%).
- `src/ops/alerts.py` — **operator alerting** (edge-triggered Alerter, no spam; print/event-log/
  webhook fail-soft sinks) over the dashboard payload (§12/§15).
- `src/ops/metrics.py` + `GET /metrics` — **Prometheus exposition** of operator state for
  Grafana/Alertmanager (§16). New `src/ops` package (top-level `ops/` stays infra/docker).
- Full suite **481→493**, ruff clean. Still NOT READY for real capital (preflight unchanged).

### 2026-06-28 — alerting wired + strategy library & edge search
- **Alerting wired into the dry-run loop**: `DryRunner._check_alerts()` runs the edge-triggered
  `Alerter` each tick (print + event-log sinks; `dry_run.py --alert-webhook` for Telegram/Slack).
- **Strategy library**: `indicators.rsi` (Wilder) + `RsiReversion` (range) + `DonchianBreakout`
  (trend), intent-only/causal/TDD.
- **`scripts/strategy_search.py`** runs all candidates through walk-forward + Deflated Sharpe with
  one shared journal (§5). Run on real Bybit 1y: **ALL 5 REJECTED** (OOS Sharpe negative, DSR ≪
  0.95) → **no validated edge**. The concrete reason real-money testing is blocked (on top of §14).
- Full suite **493→504**, ruff clean.

### 2026-06-28 — edge research (chose "find an edge"): buy-and-hold benchmark + multi-TF search
- `backtest/benchmark.py` `buy_and_hold` baseline; search now requires beating it.
- Ran the full search across **1h/4h/1d** on Bybit BTC/USDT ~2y → **NO edge** (DSR never near
  0.95; all rejected). Cost-frequency is the killer (Sharpe rises as freq falls); lower TF trades
  sample for cost (1–21 trades). Buy & hold also lost (−3.6..−4.3%). Logged in
  `docs/research/strategy_search_findings.md`. Conclusion: a real edge needs a *different
  information source*, not more tuning — go-live stays correctly blocked. Suite **504→508**.

### 2026-06-28 — a1 cross-sectional + a2 regime classifier (both: no edge)
- **a1**: `backtest/cross_sectional.py` (rank basket, hold top_k) + `scripts/cross_sectional_search.py`.
  7-pair Bybit basket 1y → every config −62..−90%, *worse* than equal-weight hold (−47%):
  momentum-chasing into a bear market. No edge.
- **a2**: `indicators.efficiency_ratio` (Kaufman) + `strategy/regime_classify.py` (deterministic
  trend/range + `RegimeFiltered` wrapper). Regime-filtered donchian/rsi were slightly WORSE +
  under-sampled — filter doesn't rescue them.
- Conclusion (docs/research/strategy_search_findings.md updated): TA + cross-sectional + regime
  filtering ALL fail deflated-Sharpe on crypto. Edge is a research problem, not a coding one.
  Suite **508→518**, ruff clean.

### 2026-06-28 — full-autonomy pass: bug-hunt + `/pro` TradingView-style dashboard
- **Bug-hunt** (probed every endpoint on the live `:8788` dry-run → all 200/valid-JSON; reviewed
  degenerate-index paths → already guarded): **no real defect** — integration layer is clean.
- **`/pro` page**: vendored Lightweight Charts main pane (candles+volume+EMA) with a **time-synced
  RSI(14) sub-pane**, KPI strip (SSE), order book + depth, watchlist, agent-view+regime, positions
  (flatten), trades. coin payload enriched with `overlays.rsi` + `regime`. `.claude/launch.json`
  added (preview-tool dev-server configs). Suite **518→519**, ruff clean. Served live on :8788.
**Remaining (operational gate):** ≥30-day dry-run on bitbank + parity + §9 restart-safety; running
*actual* Freqtrade dry-run (install/config) to feed the fill-parity reference.

### 2026-06-30 (session) — invariant re-audit of all order paths + router robustness fix
Fresh-container resume; re-installed deps, baseline **519 passed**, ruff clean. Since the last
13-finding audit several write surfaces were added (manual order, UI flatten, kill-switch via
HTTP), so re-proved **Inv 3/9 ("one execution path, no backdoor")** by tracing every path that can
reach the exchange:
- **Bot loop** (`fast_loop._enter/_exit`), **manual order** (`DryRunner.place_manual`), and **UI
  flatten** (`POST /api/order` side:sell → `place_manual`) all route `engine.validate` →
  `broker.submit`. Broker still refuses any non-engine-minted `Approval` (`is_engine_approved`).
- **Preview** (`ui/preview`, `/api/preview`) runs the exact `validate` with **no** submit.
- **Protective stop** (`stops.attach`) is a **reduceOnly sell** (risk-reducing) — correctly outside
  the entry gate; fails closed on a non-positive stop price.
- **Kill-switch** `re_arm` requires an operator identity; engage overrides everything.
  → **Core invariants intact.** No backdoor; runtime controls remain tighten-only.
- **One defect found & fixed (test-first):** the live `_place`/`_preview` wrappers do
  `float(body["qty"])` *before* `place_manual`'s own try/except, so a malformed write body (e.g.
  `{"qty":"abc"}`) raised `ValueError` that escaped `handle_request` (no try/except in `_dispatch`)
  → traceback/500 to the operator instead of a clean 400. Not an invariant breach (no order placed,
  no server crash — `ThreadingHTTPServer` isolates the request), but a robustness/contract gap.
  Fixed in the router: `/api/preview` and `/api/order` now catch `(ValueError, TypeError)` → **400**
  `invalid request body`. Test `test_malformed_write_body_is_400_not_a_crash`. Suite **519→520**.

### 2026-06-30 (session, cont.) — operator chat assistant (READ-ONLY, explain-only)
Added a real chat UI the operator can ask about the bot's state — built within the invariants
(this is the only safe shape for an LLM chat here):
- `src/llm/chat.py` — the assistant. **Inv 1 by construction:** the module imports NOTHING from
  the risk/execution/order path (an AST import-safety test pins it), so there is provably no path
  from a chat reply to an order. **Inv 2:** off the fast loop — runs only on the UI thread when the
  operator asks; Ollama down → fail-soft 'unavailable', trading unaffected. **Inv 6:**
  `build_context_summary` is a strict field whitelist over the (already-redacted) dashboard
  snapshot, so a new/secret field can't leak into the prompt. Strong system prompt REFUSES to act
  (place/size/limit/kill-switch) and points to the deterministic risk-gated controls. Ollama
  `/api/chat` backend, transport injected → tested offline. +11 tests.
- `ui/server.py` — `OperatorContext.chat`, `POST /api/chat` (404 when disabled, 400 on bad body,
  405 on GET), `GET /chat` self-contained page (persistent read-only banner), nav link from the
  dashboard. `chat_html()`. Demo context wires a canned transport so `/chat` is navigable offline.
- `ui/live.py` — `_chat` wires the live dashboard snapshot to an `OllamaChat` client (model/host
  configurable); `dry_run.py --chat-model` (default llama3.1). Live HTTP smoke OK (page + answer).
- Suite **520→532**, ruff clean. The assistant cannot trade — all trading stays on the manual
  risk-gated order form (Inv 9) and the kill-switch.

### 2026-06-30 (session, cont.) — chat assistant extended (richer context + multi-turn + chips)
Made the read-only assistant genuinely useful, still within the invariants:
- **Richer whitelisted context** (`build_context_summary`): now also renders **bot health**
  (running/ticks/last-tick/last-error), **realized performance** (trades, win rate, profit factor
  [∞-safe], expectancy, total P&L, best/worst), and **recent closed trades** — all from the
  already-redacted live dashboard payload, still an explicit field whitelist (no leak).
- **Multi-turn conversation** (`_sanitize_history` + `build_messages(..., history)`): follow-ups
  ("why?") keep context. History is sanitized (only user/assistant + string content), bounded
  (`_MAX_HISTORY_TURNS=6`), and never raises on junk; the current state is attached only to the
  latest turn (no stale-snapshot duplication). Threaded through `OllamaChat.answer`/`answer` and the
  live + demo `_chat` (`body["history"]`).
- **UI** (`chat_html`): client-side conversation memory sent as `history`, **suggested-question
  chips**, and a **Clear chat** button. Live HTTP smoke OK (chips, multi-turn, junk-history
  fail-soft). +5 tests. Suite **532→537**, ruff clean.

### 2026-06-30 (session, cont.) — chat: structured decision-log citations + per-row "explain"
- **Structured citations (1):** decision-log lines in the prompt are labelled `[D1] [D2] …`; the
  system prompt tells the model to cite those labels when explaining a past decision. `answer()`
  returns a `citations` list — `extract_citations()` parses `[D#]` out of the reply and maps each
  back to the *actual* logged entry (same 1-based index as the context slice, deduped, out-of-range
  dropped → no hallucinated reference). The `/chat` page renders citations (ref + timestamp + type +
  pair + reason) under the answer. +4 tests.
- **Per-row explain button (2):** every decision-log row on the dashboard gets an "explain" link to
  `/chat?q=<prefilled question about that row>`; the chat page reads `?q=` and auto-asks on load.
- Demo dashboard now seeds a small decision log so citations are demonstrable offline. Live HTTP
  smoke OK (explain links present, `?q=` auto-ask wired, `/api/chat` returns resolved citations
  D1/D2). Suite **537→541**, ruff clean. Still strictly read-only (Inv 1).

### 2026-06-30 (session, cont.) — UI write-endpoint auth token (defense-in-depth)
The whole operator UI assumed localhost/solo-operator (no auth). Added an OPTIONAL shared-secret
gate on the *dangerous* writes so the surface is safe if ever exposed off localhost:
- `handle_request(..., headers=None)` + `OperatorContext.auth_token`: when a token is configured,
  `/api/order` and `/api/killswitch/engage|rearm` require a matching `X-Auth-Token` (constant-time
  compare) → **401** otherwise. Read endpoints (GET) and read-only POSTs (`/api/preview`,
  `/api/chat`) stay open (they can't move money). Backward compatible: no token = unchanged.
- Wiring: `build_live_context(..., auth_token=)`; `dry_run.py --ui-token` / env `UI_AUTH_TOKEN`;
  startup line reports writes as TOKEN-PROTECTED or open. Browser side: a small `fetch` wrapper on
  the four write pages attaches the token from `localStorage` on POSTs and prompts+retries on 401.
- +2 tests; live HTTP smoke OK (order/kill-switch 401 without token, 200 with; preview/dashboard
  open). Suite **541→543**, ruff clean.

### 2026-06-30 (session, cont.) — edge research a3: funding-rate carry (new information source)
Acted on the findings doc's own recommendation (TA/cross-sectional/regime all failed DSR → try a
*different information source*). Built the funding path, TDD, invariant-safe (spot long-or-flat, no
leverage/shorting — funding is only the signal; ADR 0002: funding is research/viewing data):
- `src/data/funding.py` — `fetch_funding_history` (paginate `fetch_funding_rate_history`, drop
  still-forming interval, causal, mirrors feed.py) + `align_funding` (`merge_asof` backward: each
  candle sees only funding known at/before its close — no look-ahead §8.4). +7 tests.
- `src/strategy/funding_carry.py` — `FundingCarry`: long spot when smoothed funding ≤ threshold
  (contrarian to crowd positioning), else flat; intent-only, causal, 2 params, NaN→fail-flat. +7
  tests (incl. causality prefix==full and backtest-integration lock).
- `scripts/funding_edge_search.py` — variant set through the SAME walk-forward + Deflated-Sharpe +
  shared journal (§5), must beat buy-and-hold.
- **NOT yet run on real data:** cloud env is geo-blocked from Bybit (403 CloudFront, like Binance
  451) → the real fetch+search runs on the operator's machine (command in findings doc). Offline
  integration proven (synthetic → align → FundingCarry → walk-forward → DSR, correctly rejects
  random data). Findings doc updated (a3). Suite **543→557**, ruff clean.

### 2026-06-30 (session, cont.) — operator runbook (docs/RUNBOOK.md)
Tied the existing tooling into one step-by-step for the operator-only gates: env setup → paper tick
→ signal parity (§8.9) → edge research (strategy_search + the new funding_edge_search) → ≥30-day
dry-run with the UI (incl. the new `UI_AUTH_TOKEN`) → restart-safety (§9) → optional Ollama (P1
ablation, P2 hypotheses, /chat) → config re-lock (§15) → go-live pre-flight (§14). Every command
verified against the actual CLI flags; the config re-lock snippet reproduces the committed lock;
`go_live_preflight` runs (5/5 auto, 13 manual pending → NOT READY, correct). Linked from CLAUDE.md
and HANDOFF.md. Docs-only; suite unchanged at **557**, ruff clean.

### 2026-06-30 (session, cont.) — dry-run REPLAY mode (paper loop over past data)
Added a way to run the paper dry-run over **historical** data (not just the live poll): 
- `ReplayFeed` (src/dry_run.py) — read-only OHLCV over a fixed dataset, bounded by a movable `_now`;
  mirrors venue semantics (since=None → most-recent `limit`; since given → from there forward). No
  network. `from_frame` builds it from a fetched history frame.
- `DryRunner.replay()` — steps a synthetic clock one timeframe at a time, calling `run_once(now_ms=t)`
  so each tick sees only candles closed by t (causal, no look-ahead §8.4). Same path as live
  (loop → risk → broker → protective stop → event log); a bad bar is caught+skipped like `run()`.
- CLI `--replay-days N`: fetch N days from `--data-exchange`, replay through the paper loop, print
  ticks/trades/final-equity/return, write the event log; `--serve-ui` keeps the dashboard up after.
- +4 tests (ReplayFeed semantics, from_frame, end-to-end trades, **causality: first-K result
  independent of future bars**). Offline e2e demo ran 150 ticks over synthetic history. RUNBOOK
  §4b documents it as a same-day smoke before the ≥30-day forward run. Suite **557→561**, ruff clean.

### 2026-06-30 (session, cont.) — replay from REAL stored history (fetch-once / replay-offline)
Extended replay to use the coin's **real** past data, repeatably:
- CLI `--replay-file <.parquet/.csv>` (replay stored real OHLCV, no network) and `--save-history
  <.parquet>` (with `--replay-days`, save the fetched real history via `data.store` for reuse).
  `_load_replay_history` picks file-vs-fetch. +1 test (store round-trip → ReplayFeed → replay).
- **Demonstrated on REAL data here:** cloud env reaches kraken/coinbase/kucoin/okx/gemini (only
  bitbank/bybit/binance are geo-blocked). Fetched **1079 real kucoin BTC/USDT 1h candles (45d)**,
  saved to parquet, replayed offline from the file — identical, reproducible. Event breakdown: 829
  MarketReceived+SignalGenerated, 9 enter_long, all 9 **rejected by the engine as `per_asset_cap`**
  → 0 trades. That is the documented "sizing proposes >25% per-asset, engine disposes" design note
  (correct, invariant-safe), surfaced faithfully by replay — not a replay bug. Suite **561→562**,
  ruff clean.

### 2026-06-30 (session, cont.) — sizing: opt-in per-asset clamp (money code, TDD)
Resolved the long-standing design note (inverse-ATR sizing proposing >25% per-asset → every live
entry futilely rejected as `per_asset_cap`). TDD, money code, invariant-safe:
- `risk/sizing.compute_size(..., clamp_per_asset=False)` — when on, caps the notional to the
  per-asset headroom (`per_asset_cap_pct` − existing position value in the pair), matching
  `limits.check_per_asset` exactly. **Strictly tightening** (size only shrinks; the engine still
  disposes). No headroom → SKIP with `per_asset_cap` (never a forced trade). **Default off → the P0
  backtest path is byte-for-byte unchanged** (same opt-in pattern as `size_multiplier`).
- `fast_loop._enter` opts in (`clamp_per_asset=True`) so the **live loop / dry-run / replay** size
  feasibly instead of being rejected. Fast-loop tests already keep size under the cap → unchanged.
- +6 sizing tests (clamp limits notional to cap, accounts for existing exposure, SKIP at cap,
  never-grows-when-under-cap, engine-accepts-the-clamped-order, opt-in default preserves Kelly).
- **Verified on REAL data:** re-ran the replay on the saved 1079 real kucoin BTC/USDT candles —
  now **9 closed trades, 18 RiskPassed, 0 rejections** (was 0 trades / 9 `per_asset_cap` rejects);
  realized_pnl ~breakeven (EMA-cross has no edge — honest). Suite **562→568**, ruff clean.

### 2026-06-30 (session, cont.) — multi-pair replay dashboard + cross-coin AI assistant
Built a visual dry-run/replay comparison across selectable coins, with the read-only assistant
scoped to all of them:
- `ui/replay_dashboard.py` — pure model: `summarize_result` (per pair: return, max-DD, per-tick
  Sharpe from the equity curve + `performance_summary` trade stats) and `build_replay_comparison`
  (JSON-safe sortable table + best/worst + per-pair detail). +6 tests.
- `llm/chat.py` — `build_multi_pair_summary` (renders the comparison for the LLM) + an injectable
  `context_builder` threaded through `build_messages`/`OllamaChat.answer`/`answer` (default =
  single-pair dashboard, unchanged). The assistant can now analyse/look up ACROSS coins. +3 tests.
- `ui/server.py` — `OperatorContext.replay`, `GET /api/replay` + a `/replay` page (sortable metric
  table, pair selector with equity mini-chart + trades, cross-coin chat box, suggestion chips), nav
  link. +1 test. `ui/live.py` `build_replay_context` wires a finished comparison + the multi-pair
  chat (read-only, no live loop).
- `dry_run.py` — `run_multi_replay` (a DryRunner.replay per pair, own account/log/feed; a bad
  symbol is skipped) + `--pairs A,B,C`; `--serve-ui` serves `/replay`.
- **Verified on REAL data:** kucoin BTC/ETH/SOL 30d → BTC −0.69%, ETH −0.51%, SOL +1.77% (best SOL,
  worst BTC); HTTP smoke: `/api/replay` sorted, `/replay` renders, cross-coin chat gets the whole
  table in context and answers "SOL did best". Suite **568→578**, ruff clean. Read-only throughout
  (Inv 1); still an optimistic-fill pipeline view (§2), not an edge claim.

### 2026-06-30 (session, cont.) — richer replay metrics (Calmar, VaR/CVaR, exposure)
Enriched the multi-pair comparison table + cross-coin chat with pro-desk metrics:
- `ui/replay_dashboard.ReplayResult` now also carries **Calmar** (total_return/|maxDD|), **VaR95**
  and **CVaR95** (from `backtest.risk_analytics` over per-tick equity returns), **avg_exposure**
  and **time_in_market**. To feed exposure, `DryRunner._record_equity` now records per-tick gross
  exposure (gross position value / equity) alongside equity — backward-compatible extra key.
- Surfaced in the `/replay` table (new sortable columns), the chat multi-pair summary (so the AI
  can reason over risk/exposure), and JSON. +1 test (calmar/VaR/CVaR/exposure) + updated fixtures.
- **Verified on REAL data:** kucoin BTC/ETH/SOL 20d — all columns populate (e.g. SOL Calmar −0.29,
  VaR95 −0.167%, CVaR95 −0.214%, exposure 8% / time-in-market 43%); CVaR ≤ VaR as expected. Suite
  **578→579**, ruff clean.

### 2026-06-30 (session, cont.) — replay metrics: Sortino + Monte-Carlo DD + threshold colouring
- `ReplayResult` adds **Sortino** (per-tick mean / downside deviation) and **Monte-Carlo drawdown
  p95/p99** (`bootstrap_max_drawdown`, seeded n=500 → reproducible). Both in the `/replay` table,
  the chat multi-pair summary, and JSON.
- `/replay` table now **threshold-coloured**: profit factor green ≥1 / red <1; max-DD, VaR95,
  CVaR95, DD95, DD99 amber→red as the loss deepens; Calmar/Sharpe/Sortino/return green/red by sign;
  exposure amber if >90%. +1 test (Sortino + MC-DD, incl. seeded reproducibility). Verified over
  HTTP (new keys in `/api/replay`, new headers + colour helpers on the page). Suite **579→580**,
  ruff clean.

### 2026-06-30 (session, cont.) — strategy comparison on one coin (EMA/RSI/Donchian/Funding)
Reused the /replay comparison surface to compare *strategies* on a single coin (not just coins):
- `build_replay_comparison(..., dimension="strategy", coin=...)` — same table/model, rows now keyed
  by strategy name; adds `dimension`/`label`/`coin`. Default stays `dimension="pair"` (backward
  compatible). Chat multi-pair summary is dimension-aware ("Multi-strategy comparison on <coin>").
- `dry_run.run_strategy_comparison` — runs each strategy on the SAME fetched history via the §8
  **backtest engine** (uniform + honest; FundingCarry gets a causally-aligned funding column);
  `_summarize_backtest` adapts BacktestResult→ReplayResult incl. per-bar exposure from trade spans.
  Registry: ema/rsi/donchian/funding. CLI `--strategies a,b,c` (+ `--perp`) on one `--pair`.
- `/replay` relabels the first column (Pair→Strategy) and shows the coin; served via
  `build_replay_context`. +4 tests (strategy dimension model + default). 
- **Verified on REAL data:** kucoin BTC/USDT 60d, all four — funding_carry −1.48% (best), ema
  −3.67%, rsi −5.61%, donchian −7.23% (worst); kucoin served 100 funding prints. All negative =
  honest (no edge). HTTP smoke: /replay shows dimension=strategy, coin, rows by strategy. Suite
  **580→582**, ruff clean.

### 2026-06-30 (session, cont.) — pluggable /chat backends (bring-your-own AI API key)
The assistant was local-Ollama-only; added **opt-in cloud providers** while keeping local default:
- `llm/chat.py` — `AnthropicChat` (Claude Messages API, `x-api-key` + `anthropic-version`, system
  split out, model default `claude-opus-4-8`) and `OpenAIChat` (OpenAI-compatible chat/completions,
  `Authorization: Bearer`, `base_url` override). Both use the **same stdlib-urllib + injectable
  transport** pattern as `OllamaChat` (no new deps, offline-tested). `build_chat_client(provider,…)`
  factory. `parse_anthropic_response`/`parse_openai_response`. +7 tests (incl. key-never-in-body).
- **Secrets/privacy (Inv 6):** the API key is wrapped in `core.secrets.Secret` (repr/str masked to
  `***`, revealed only for the auth header, never logged / never in the request body). Key comes
  from the ENV only (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`), never a CLI flag.
- **Wiring:** `build_live_context`/`build_replay_context` take `chat_provider`/`chat_api_key`/
  `chat_base_url`; `dry_run.py` `--chat-provider {ollama,anthropic,openai}` + `--chat-base-url`;
  `_resolve_chat_config` pulls the key from env, prints a **privacy warning** that cloud sends the
  dashboard snapshot off-machine, and **falls back to local Ollama** if the key is missing.
- Still read-only (Inv 1) on every backend; default stays **local Ollama (data never leaves)**.
  Suite **580→587**, ruff clean.

### 2026-06-30 (session, cont.) — UI provider switcher + Gemini (extensible provider registry)
- **Gemini backend** (`GeminiChat`): Google generateContent API (`x-goog-api-key` header, roles
  user/model, separate `system_instruction`); same stdlib+injectable-transport pattern, key masked.
  Registered in `CHAT_PROVIDERS` + `build_chat_client`. Adding a provider = one registry entry + one
  factory branch.
- **`ChatRouter`** holds one pre-built backend per available provider and routes by name;
  `build_chat_router(env, …)` includes every provider whose key is in the env (Ollama always).
- **Switch from the UI:** `GET /api/chat/providers` lists {name, model, default}; a **dropdown on
  `/chat` and `/replay`** lets the operator pick the AI per question (sent as `provider` in the
  `/api/chat` body — the key never leaves the server). `OperatorContext.chat_providers`;
  `build_live_context`/`build_replay_context` take a `chat_router`; `dry_run` builds it from env and
  prints which providers are available.
- +7 tests (Gemini role-map + key-in-headers-only, router list/route/unknown→default,
  build_chat_router key-gating, providers endpoint). Live HTTP smoke: 3 providers switchable per
  request. Read-only on every backend (Inv 1); default local Ollama. Suite **587→591**, ruff clean.

### What's left
- **Phase-gated (later):** P1 `llm/**` sentiment (needs Ollama), P3 `ui/**` + `strategy/shadow`,
  `features/cache.py`.
- **Operational (not code):** the real P0 close — venue now pinned (bitbank); remaining is the
  operator's account: confirm bitbank KYC + ToS for API/bot spot trading (§11), confirm the live
  fee schedule (§8.3), then a live **≥30-day forward dry-run** on bitbank BTC/JPY and run
  `dryrun_parity` on its event log. The whole code path is proven on real data + the real venue.

## ORIENT decisions (§7 — being filled in with the operator)

| Item | Status | Decision |
|------|--------|----------|
| Exchange entity | ✅ decided (amended 2026-06-27) | **Trade = bitbank** (JFSA); **view = Binance Global** (read-only). See ADR 0002 + Amendment. |
| Target pairs | ✅ decided | **BTC/JPY** (bitbank's primary BTC quote; confirmed active via ccxt). StaticPairlist for reproducibility. |
| Timeframe | ✅ decided | **1h**. |
| Actual fee tier | ✅ set (operator to confirm) | bitbank published spot: **maker 0.00% / taker 0.10%** (ccxt market meta). In `costs.json`, re-locked. Operator confirms current schedule/rebate campaign before real capital (§8.3). |
| Operator KYC / ToS | ⛔ operator to verify | Confirm bitbank KYC + that its ToS permits API/bot spot trading (§11). |
| ccxt id/endpoint | ✅ resolved | **`bitbank`** (present in ccxt 4.5.60, OHLCV supported). No public sandbox → paper/dry-run until P4. |

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
| P0 data harness | **code proven on real venue; awaiting operator's bitbank account close** | §8 proven |
| P1 LLM sentiment feature | **built + tested, inert at FLOOR=1.0; awaiting Ollama + ablation** | forward-validated ablation |
| P2 hypothesis generation | **validation machinery built + tested; awaiting Ollama + human-approved validated edge** | ≥1 LLM strategy walk-forward validated |
| P3 monitor/orchestrate | **deterministic cores built (shadow, risk-preview, dashboard model); awaiting UI transport + ≥30d dry-run** | ≥30d dry-run, signal metrics ≈ backtest |
| P4 tiny real capital | not started | §14 go-live checklist + human sign-off |
| P5 scale | not started | sustained live perf + capacity + sign-off |
