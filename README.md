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

**Current phase: P0** (data harness, reproducible backtest, dumb strategy, full risk + execution
money-code, fast-loop orchestrator). All logic is unit/integration tested but has **not** been
proven against real data on the target venue — P0 is **not** closed. There is **no live trading
yet** (real capital is gated behind P3/P4). See [`PROGRESS.md`](PROGRESS.md).

## Quickstart

Requires **Python 3.11+**. Outbound network access to an exchange is needed only for the data
demo, not for tests.

```bash
# 1) clone, then create an isolated environment
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2) install the package + dev tools (editable)
pip install -e ".[dev]"

# 3) run the test suite (244 tests, all offline/mock — no network, no keys)
pytest -q

# 4) lint
ruff check .

# 5) verify config integrity (§15 hash-lock)
python -c "from pathlib import Path; from src.core.config import load_manifest, verify_configs as v; \
m=load_manifest('config/config.lock.json'); v([Path(p) for p in m], {str(Path(p)):h for p,h in m.items()}); \
print('config integrity OK')"
```

### Run the data → backtest demo (needs network)

```bash
python scripts/first_real_backtest.py
```

Pulls real BTC/USDT 1h OHLCV and runs the full pipeline (fetch → quality gate → features →
strategy → risk-sized backtest → walk-forward/metrics), proving it is reproducible. **Caveats:**
it uses **Kraken** as a provisional source because Binance is geo-blocked from many hosts (HTTP
451), applies the Binance VIP0 cost model, and over ~30 days yields too few trades — the
sample-size gate correctly reports the result as not meaningful. This validates the harness, it
does **not** close P0.

### Run the paper dry-run loop (needs network)

```bash
python -m src.dry_run --data-exchange kraken --pair BTC/USDT --timeframe 1h --iterations 1
# --iterations 0 runs forever (polls every --poll-seconds); writes events/dry_run.jsonl
```

Runs the fast loop on closed candles in **paper** mode: OHLCV comes from a read-only data
exchange, but every order is routed to a **simulated** paper exchange — **no real capital, no
real order ever leaves the process**. Each tick is appended to the (git-ignored) event log. This
validates signal logic and the order path forward; it does **not** validate fills (§2), and one
tick on flat data just prints `hold`.

### Secrets / live trading

No keys are needed for tests or the demo. Real keys (trade-only, withdrawals disabled,
IP-whitelisted) belong in a git-ignored `.env` — copy [`.env.example`](.env.example) — and are
only used at **P4** after the §14 go-live checklist. There is intentionally **no command that
trades real capital** in this repository yet.

### What does NOT exist yet

Live trading on real capital (gated to P4), the LLM slow loop (`llm/**`, P1, needs Ollama), and
the operator UI (`ui/**`, P3). The paper dry-run loop (`src/dry_run.py`) exists; a real P0/P3
close still needs the operator's actual venue, more history, and a sustained ≥30-day run.

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
