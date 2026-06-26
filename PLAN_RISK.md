# PLAN_RISK.md — Risk engine (money code, TDD-mandatory)

> The dedicated TDD plan for `src/risk/**` — the part that keeps the account alive (§4) and the
> **single gate every order passes** (Invariant 3). This is **money code**: every slice is
> **test-first, red → green → refactor** (`superpowers:test-driven-development`). A green
> implementation with thin tests does not count — the tests ARE the spec here.
>
> Companion to `PLAN.md` (P0 data harness, ✅ Slices 0–7). Risk-engine wiring is the tail of the
> P0 build order (§7) and a prerequisite for the §14 go-live checklist.

## Non-negotiables this plan must encode in code (not just prose)

1. **One execution path, no backdoor (Inv. 3 / Inv. 9):** strategy, LLM-haircut, and manual-UI
   orders all call the *same* `engine.validate()`. The UI pre-trade preview (§12) calls the
   exact same function — no second, divergent check.
2. **Runtime may only tighten or halt, never loosen (Inv. 3):** a runtime control can lower a
   limit or stop trading; loosening requires a config change + human sign-off. Encoded as a
   guard, tested.
3. **Manual human kill overrides everything** and is always available (Inv. 3).
4. **Sub-minimum size → SKIP, never round up past the risk cap (§4/§9):** the single most
   crypto-specific correctness trap.
5. **Limits are hard-coded defaults to *tune*, not profit targets;** they live BELOW the
   strategy (§4). The strategy never sees them.

## Design (the shape the slices build toward)

```
strategy intent ┐
LLM size-haircut ┼─▶ sizing.compute_size ─▶ engine.validate(order, state, cfg) ─▶ RiskDecision
manual UI order ┘                                   │  (approve | reject + reasons)
                                                    ├─ killswitch.state        (HALTED ⇒ reject all)
                                                    ├─ exchange_assert.check    (mismatch ⇒ STOP)
                                                    ├─ depeg.check              (de-peg ⇒ no entries)
                                                    ├─ limits.* (per-trade, gross, per-asset,
                                                    │            concurrency, daily soft/hard,
                                                    │            drawdown kill, correlation/beta)
                                                    └─ sizing feasibility (minNotional/lot/tick)
                                                                  │
                                                            execution/ (only if approved)
```

**Core types** (introduced in R0, used everywhere): `Order` (pair, side, quote_notional,
price, source), `Position` (pair, qty, entry, value), `PortfolioState` (equity, peak_equity,
day_start_equity, realized_day_pnl, positions, quote_price, heartbeats), `RiskDecision`
(approved: bool, reasons: list[str], sized: Order | None), `RiskConfig` (loaded from
`config/risk/default.json`). Decisions are **explainable**: a rejection always carries reasons
(feeds the §12 decision log and §15 event log).

## Defaults (from `config/risk/default.json` — §4, do not invent new ones)

per-trade 0.5% · fractional-Kelly ≤ ½ · max 3 concurrent · gross ≤ 100% · per-asset ≤ 25% ·
correlation cluster >0.7 capped 40% · daily soft −2% (stop entries) / hard −4% (flatten) ·
drawdown kill −12% (non-overridable) · de-peg > 1% · human heartbeat 7d · leverage 0.

---

## Prerequisite — ATR on the single feature path

`sizing.py` needs ATR; the single-feature-path rule (§15) says it lives in
`features/indicators.py` (today only EMA). `quality.py` already has a private true-range helper
— promote it.

- **Do:** add `true_range(df)` and `atr(df, period=14)` to `features/indicators.py`; refactor
  `quality._true_range` to call it (one true-range implementation, no skew).
- **Proof:** `pytest tests/features/test_indicators.py -q` — ATR vs a hand-computed reference;
  `tests/data/test_quality.py` still green (refactor changed no behavior).

---

## Slices (each: test-first; money code)

### R0 — Risk types + RiskConfig loader (`src/risk/types.py`, `core/config.py` minimal)
- **Do:** the dataclasses above; load `RiskConfig` from `config/risk/default.json`. Validate the
  config (positive caps, soft < hard in magnitude, leverage == 0 pre-P5) and **refuse to load**
  a malformed/loosened-beyond-schema config. This is the declarative-config side of the §15
  integrity check — applies to config files only, never to runtime state.
- **Test-first:** loads expected defaults; rejects leverage>0 (pre-P5 invariant); rejects
  soft/hard inversion; `Order`/`PortfolioState` construct and reject nonsensical values.
- **Proof:** `pytest tests/risk/test_types.py tests/risk/test_config.py -q`.

### R1 — Position sizing + order-size feasibility (`src/risk/sizing.py`)
- **Do:** `compute_size(order_intent, state, cfg, atr)`:
  - risk_amount = equity × per_trade_pct; **inverse-ATR**: qty = risk_amount / (k×ATR stop
    distance); fixed-fractional fallback. Cap at **fractional-Kelly ≤ ½** — never raw Kelly.
  - round price to **tick-size**, qty **DOWN** to **lot-step**; enforce **minNotional**.
  - if the risk-capped size < exchange minimum → return **infeasible / SKIP + flag**; **must NOT
    round up past the risk cap** (§4/§9).
  - surface the **three-way tension** (capital ↔ 0.5% ↔ ≥100 trades): if unsatisfiable, flag
    "account too small for this risk policy" rather than silently breaking one constraint.
- **Test-first (the critical ones):**
  - inverse-ATR sizing math on a worked example; higher ATR → smaller size.
  - fractional-Kelly cap binds when it would exceed ½.
  - lot-step rounds **down**, tick-size rounds price, minNotional enforced.
  - **sub-minimum → SKIP, size never rounded up past the cap** (boundary: just-below skips,
    just-above passes).
  - tiny-account three-way-tension flag fires.
- **Proof:** `pytest tests/risk/test_sizing.py -q`.

### R2 — Hard limits (`src/risk/limits.py`)
- **Do:** one pure predicate per limit, each returning `(ok: bool, reason: str)`:
  per-trade risk ≤ cap · gross exposure ≤ 100% · per-asset ≤ 25% · max concurrent ≤ 3 ·
  **daily soft −2%** (block *new entries* only) vs **daily hard −4%** (flatten all) ·
  **drawdown −12%** kill · **correlation-cluster**: positions with pairwise corr > 0.7 are
  treated as **one** for exposure math, capped 40% · portfolio BTC-beta cap.
- **Test-first:** boundary at each threshold (just-under ok, at/over fails); soft blocks entries
  but not exits, hard flattens; correlated cluster summed as one (two 0.8-corr 25% longs breach
  the 40% cluster cap even though each is under per-asset); beta cap.
- **Proof:** `pytest tests/risk/test_limits.py -q`.

### R3 — Quote-stablecoin de-peg guard (`src/risk/depeg.py`)
- **Do:** `check_depeg(quote_price, cfg)` → within band ok; |quote−1| > threshold (default 1%) →
  **halt new entries + signal re-mark equity + alert**; defines behavior for open positions
  (hold/flag, do not auto-trade through a de-peg — §13.16).
- **Test-first:** at/under threshold passes; over (both directions) halts entries; the re-mark
  flag is set; an exit is still permitted during a de-peg (we can leave, not enter).
- **Proof:** `pytest tests/risk/test_depeg.py -q`.

### R4 — Exchange-config assertions (`src/risk/exchange_assert.py`)
- **Do:** `assert_exchange_state(actual, cfg)` → require spot mode, leverage == 1, margin
  disabled, futures disabled, reduceOnly on exits (and exchange max-order-size where supported).
  Any mismatch → **STOP, do not trade** (returns a hard-stop / raises a typed error).
- **Test-first:** matching state passes; each individual mismatch (leverage 2, margin on,
  futures on, reduceOnly off) → STOP with the specific reason.
- **Proof:** `pytest tests/risk/test_exchange_assert.py -q`.

### R5 — Kill-switch + dead-man's switches (`src/risk/killswitch.py`)
- **Do:** a small state machine `ARMED ⇄ HALTED`:
  - **manual human kill** → HALTED (flatten + stop), overrides everything; re-arm requires an
    explicit human action (never automatic).
  - **drawdown −12% kill** → HALTED, **non-overridable at runtime** (only config + human).
  - **process heartbeat** loss → cancel resting *entry* orders.
  - **human heartbeat** absent ≥ 7d → flatten + halt until re-enabled.
- **Test-first:** manual kill halts and blocks new orders; auto re-arm is impossible; drawdown
  kill cannot be cleared at runtime; stale process heartbeat cancels entries (not exits); stale
  human heartbeat (7d) halts; fresh heartbeats stay ARMED.
- **Proof:** `pytest tests/risk/test_killswitch.py -q`.

### R6 — The engine: the single gate (`src/risk/engine.py`)  ← convergence point
- **Do:** `validate(order, state, cfg) → RiskDecision`. Runs, in order and **fail-closed**:
  killswitch state → exchange assertions → de-peg → hard limits → sizing feasibility. **Any**
  failure ⇒ `approved=False` with **all** accumulated reasons; approve only if every check
  passes. Same function for bot and manual-UI orders (Inv. 9). Emits the decision for the §15
  event log (RiskPassed / RiskRejected; secrets redacted) — log wiring lands when
  `events/log.py` is built (tracked as a follow-up, not blocking R6's logic).
- **Test-first:**
  - an all-clean order is approved with a concrete sized order.
  - each single breach (one per R2–R5 class) ⇒ rejected with that reason.
  - **HALTED killswitch ⇒ every order rejected**, including a manual one.
  - **a deliberately over-limit manual order is rejected** and yields the same reasons the UI
    preview would show (the §14 manual-order drill).
  - reasons aggregate (multiple simultaneous breaches all reported).
- **Proof:** `pytest tests/risk/test_engine.py -q`.

### R7 — Runtime tighten-only guard (`engine`/`limits`)
- **Do:** a runtime override may **tighten** any limit or **halt**, but an attempt to **loosen**
  at runtime is rejected (needs config + human). Encodes Inv. 3 directly.
- **Test-first:** tightening per-trade 0.5%→0.3% applies; halting applies; loosening 0.5%→1.0%
  at runtime is refused; loosening via a (signed) config change is the only path.
- **Proof:** `pytest tests/risk/test_runtime_controls.py -q`.

---

## Definition of done for the risk layer (re-prove from clean)

- [ ] Every slice green via TDD (tests written first, observed red, then green).
- [ ] **Convergence check (Inv. 3):** the only path from an order to `execution/` is through
      `engine.validate()`. When `execution/` exists, CodeGraph shows no bypass; until then, a
      test asserts strategy/manual order objects cannot reach an "approved" state without it.
- [ ] Risk-engine **drills** each *actually fire* (the §14 checklist): per-trade cap,
      correlation/beta cap, soft AND hard daily limits, drawdown kill-switch, de-peg guard,
      exchange-config STOP, manual-over-limit rejection.
- [ ] `config/risk/default.json` is the single source of limits; runtime tightening works,
      loosening is refused.
- [ ] Replace the backtest's full-equity placeholder sizing (PLAN.md Slice 6) with
      `risk/sizing.compute_size` so backtest and live size through one path (no skew).

## Sequencing & dependencies

ATR prereq → R0 → R1 (sizing) ∥ R2 (limits) ∥ R3 (depeg) ∥ R4 (assert) ∥ R5 (killswitch) →
R6 (engine assembles them) → R7 (tighten-only). R1–R5 are independent and can be built in any
order; R6 depends on all of them. Event-log emission (RiskPassed/Rejected) integrates once
`events/log.py` exists — a separate slice, not a blocker for risk-engine logic.
