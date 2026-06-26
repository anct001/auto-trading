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

## Next slice (P0, first work item)

Per §7 build order — **data → quality gate → harness → dumb strategy → dry-run → risk wiring**.
Recommended first slice: `src/data/feed.py` + `src/data/store.py` + `src/data/quality.py` with
TDD on the quality gate (§8.1), proven by a reproducible OHLCV pull + quarantine unit tests.

Before writing code, run the `autonomous-coding-loop` **ORIENT** step: confirm exchange, target
pairs, and timeframe with the operator (blocking if unset — §7), then write `PLAN.md`.

## Phase ledger

| Phase | State | Gate |
|-------|-------|------|
| P0 data harness | **in progress (scaffold only)** | §8 proven |
| P1 LLM sentiment feature | not started | forward-validated ablation |
| P2 hypothesis generation | not started | ≥1 LLM strategy walk-forward validated |
| P3 monitor/orchestrate | not started | ≥30d dry-run, signal metrics ≈ backtest |
| P4 tiny real capital | not started | §14 go-live checklist + human sign-off |
| P5 scale | not started | sustained live perf + capacity + sign-off |
