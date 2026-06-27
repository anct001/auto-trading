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
**Remaining (UI):** only nice-to-have pixels (live order-book depth, richer charts).
**Remaining (operational gate):** ≥30-day dry-run on bitbank + parity + §9 restart-safety; running
*actual* Freqtrade dry-run (install/config) to feed the fill-parity reference.

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
