# Autonomous Crypto Trading Agent

A **solo-operator, paper-first, phase-gated** automated crypto-trading system. A local LLM is
an out-of-loop researcher; a deterministic, backtested, risk-capped engine makes every trade.

> ⚠️ **No real capital is used before the Phase 3 DONE-GATE.** No leverage before Phase 5.
> Spot-only (long-or-cash) before then. This is not financial advice — see
> [`docs/MASTER_DRIVER.md`](docs/MASTER_DRIVER.md) §0 and §11.

## Read these first (orientation order)

1. **[`CLAUDE.md`](CLAUDE.md)** — agent entrypoint: the hard invariants + routing (short).
   `AGENTS.md` and `GEMINI.md` are aliases.
2. **[`docs/MASTER_DRIVER.md`](docs/MASTER_DRIVER.md)** — the canonical spec (v0.6, §0–§16).
   Section numbers are the project's shared vocabulary.
3. **[`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md)** — component → directory → file, and which
   spec § each implements.
4. **[`PROGRESS.md`](PROGRESS.md)** — current phase + checkpoint state.
5. **[`docs/phases/`](docs/phases/)** — the per-phase spec + DONE-GATE you're working to.

## The non-negotiable invariants (condensed — full form in §1)

1. The LLM **never** places or sizes a live order. Deterministic code only.
2. Two decoupled loops — the fast trading loop never blocks on or is vetoed by the LLM.
3. **One execution path, no backdoor** — every order passes `src/risk/engine.py`. Runtime
   controls may only *tighten* or *halt*, never loosen. A manual human kill is always available.
4. **Paper-first, gated** — no real capital before P3; no leverage before P5; spot-only before P5.
5. A **human partner** approves go-live and every limit increase.
6. Secrets are trade-only, withdrawal-disabled, IP-whitelisted, vaulted, git-ignored, and
   **never logged** (redact first).

## Status

**Current phase: P0** (data harness, reproducible backtest, one dumb strategy — all unproven).
This repository is at the **scaffold** stage: directory structure + stub modules citing the
spec, no trading logic yet. See [`PROGRESS.md`](PROGRESS.md).

## Layout

See [`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md) for the full tree. Top level:

```
src/        core, data, features, strategy, risk, execution, llm, events, ui
backtest/   §8 harness (Freqtrade + walk-forward)
config/     hash-locked declarative config (strategy/, risk/)
tests/      TDD — risk/ is the most-tested module (money code)
ops/        docker-compose, metrics, runbook
docs/       MASTER_DRIVER (spec), CODEBASE_MAP, adr/, phases/
```

## Development discipline

- **TDD is mandatory** in `src/risk/**` and `src/execution/**` (money code): test-first,
  red-green-refactor.
- A green backtest number is **not** proof — reproducibility + matching forward dry-run is
  (fills stay unproven until P4). See §8.
- Commit with a **secrets-scanned** diff; never commit `.env`, keys, parquet caches, or
  `user_data/`.

## Reference stack

Python 3.11+ · Freqtrade (execution/backtest) · vectorbt (offline research) · ccxt
(connectivity) · Ollama (local LLM, slow-loop only) · Parquet + SQLite/Postgres · Docker
Compose. Justify any deviation in `PLAN.md` (§2).
