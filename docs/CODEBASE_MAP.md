# CODEBASE_MAP.md — where everything lives

The map an agent reads to orient before touching code. Every component points to the
`docs/MASTER_DRIVER.md` section it implements, so the spec (§) and the code stay one vocabulary.

## Repository layout

```
autonomous-crypto-trading-agent/
├── CLAUDE.md                     # agent entrypoint (invariants + routing) — read first
├── AGENTS.md → CLAUDE.md         # alias for Codex/Copilot; GEMINI.md alias for Gemini CLI
├── README.md                     # human entrypoint
├── PROGRESS.md                   # current phase + checkpoint state (updated each session)
├── .codegraph/                   # CodeGraph index (tree-sitter + SQLite); query, don't grep
├── .claude/
│   ├── settings.json             # Superpowers marketplace + plugin wiring
│   └── skills/
│       ├── trading-invariants/   # project skill: the hard rules (overrides generic defaults)
│       ├── autonomous-coding-loop/
│       └── context-token-efficiency/
├── docs/
│   ├── MASTER_DRIVER.md          # canonical spec (v0.6, §0–§16) — authoritative
│   ├── CODEBASE_MAP.md           # this file
│   ├── CODEGRAPH.md              # human-readable module-dependency overview
│   ├── adr/                      # architecture decision records (0001…)
│   └── phases/                   # P0…P5: per-phase spec + DONE-GATE
├── src/
│   ├── fast_loop.py              # §3 fast (deterministic) loop — composes the whole pipeline/tick
│   ├── core/                     # config, secrets, clock — used by everything
│   │   ├── config.py             # declarative config; hash-locked (§15 integrity check)
│   │   ├── secrets.py            # env/vault loader; never logged (§10)
│   │   └── clock.py              # NTP / skew reject (§9)
│   ├── data/                     # §2 data layer
│   │   ├── feed.py               # ccxt OHLCV fetch
│   │   ├── store.py              # parquet store
│   │   └── quality.py            # §8 data-quality gate (bad/dup ts, ≤0 price, spikes, vol)
│   ├── features/                 # §15 single feature path (backtest AND live share this)
│   │   ├── indicators.py         # RSI / MACD / ATR / EMA / Bollinger
│   │   └── cache.py              # lightweight feature cache (no full feature store yet)
│   ├── strategy/                 # §5 strategies — deterministic, declare target regime
│   │   ├── base.py               # interface (emits intent, never sizes)
│   │   ├── ema_cross.py          # the "deliberately dumb" first strategy
│   │   └── shadow.py             # §15 shadow-mode harness (hypothetical P&L, never trades)
│   ├── risk/                     # §4 RISK ENGINE — first-class; the single gate (Invariant 3)
│   │   ├── engine.py             # validate(order) → pass/reject; ALL orders pass here
│   │   ├── limits.py             # per-trade, daily soft/hard, drawdown, correlation/beta
│   │   ├── sizing.py             # vol-aware + minNotional/lot feasibility (§4/§9)
│   │   ├── exchange_assert.py    # §4 exchange-config assertions (spot, lev=1, margin off)
│   │   ├── depeg.py              # §4 quote-stablecoin de-peg guard
│   │   └── killswitch.py         # kill-switch + dead-man's (process AND human heartbeat)
│   ├── execution/                # §9 execution
│   │   ├── broker.py             # idempotent orders, precision, TIF (§9)
│   │   ├── stops.py              # exchange-side protective stops at fill (§4)
│   │   └── reconcile.py          # restart-safe reconciliation vs exchange truth
│   ├── llm/                      # §3 slow loop — OUT of the trading path
│   │   ├── orchestrator.py       # Ollama; async; writes state files only
│   │   ├── sentiment.py          # bounded size-haircut signal (FLOOR ≥ 0.5)
│   │   ├── journal.py            # §15 trade journal / failure memory + hypotheses-tried count
│   │   └── state_io.py           # sentiment_state.json / regime_state.json (TTL)
│   ├── events/                   # §15 append-only event log (immutable, secret-redacted)
│   │   └── log.py
│   └── ui/                       # §12 operator surfaces (read-only + kill-switch + gated order)
│       ├── api.py                # FastAPI + SSE
│       ├── dashboard/            # equity, P&L vs limits, drawdown, decision log
│       ├── market/               # watchlist, coin detail (k-line+indicators), heatmap, screener
│       └── orders/               # order & trade panel + manual order (routes through risk)
├── backtest/                     # §8 harness
│   ├── runner.py                 # deterministic backtest (P0); Freqtrade at P3 — ADR/PLAN
│   ├── metrics.py                # §8.7 metric suite (CAGR/Calmar/Sharpe/Sortino…) + sample gate
│   ├── walkforward.py            # out-of-sample + walk-forward + deflated-Sharpe (§5)
│   └── replay/                   # §15 adversarial replay (flash crash, gap, outage)
├── config/
│   ├── strategy/*.json           # hash-locked (§15)
│   └── risk/*.json               # hash-locked (§15)
├── tests/                        # TDD — risk/ is the most-tested module
│   ├── risk/                     # limits, sizing, depeg, exchange_assert, killswitch
│   └── execution/
└── ops/
    ├── docker-compose.yml
    ├── metrics.py                # §15 metrics/alerting (Prometheus exporter — principle)
    └── runbook.md                # incident runbook + demotion-to-paper procedure (§6)
```

## Component → spec section (quick index)

| Directory | Implements | Key invariant it upholds |
|-----------|-----------|--------------------------|
| `src/data/` + `quality.py` | §2 data, §8 quality gate | never trade on bad/stale data |
| `src/features/` | §15 single feature path | no train-serve skew |
| `src/strategy/` | §5 | strategy proposes, never sizes/executes |
| `src/risk/` | §4 | **the single gate — Invariant 3** |
| `src/execution/` | §9 | exchange-side stops; restart-safe |
| `src/llm/` | §3 | LLM out of the trading path (Inv. 1, 2) |
| `src/events/` | §15 event log | immutable audit, secrets redacted (Inv. 6) |
| `src/ui/` | §12 | read-only + kill-switch; manual order risk-gated (Inv. 3) |
| `backtest/` | §8 | reproducible; fills unproven until P4 |
| `src/core/` | §10, §15 | secrets discipline; config integrity |

## Using CodeGraph on this repo

The agent queries the pre-built index instead of exploring file-by-file:

- **Before editing risk logic:** `codegraph_context "risk engine validate order"` → finds
  `src/risk/engine.py` and its callers without a grep sweep.
- **Blast radius before a refactor:** query callers of `sizing.compute_size` before changing
  the minNotional/lot logic — every execution path and the manual-order UI depend on it.
- **Architecture questions:** "what writes to the event log?" resolves to `events/log.py`
  callers across risk/execution/llm.
- **Convergence check:** the graph should show **every order path converging on
  `src/risk/engine.py`** before `src/execution/`. If CodeGraph ever shows an execution call
  that bypasses risk, that is an Invariant-3 violation — fix it, don't ship it.

CodeGraph indexes AST, not runtime behavior; it complements `CLAUDE.md` and this map, it does
not replace them. Build the index early (P0) so it grows with the repo; the precision win
applies at any size, the token/cost win compounds as the repo grows.

## Superpowers methodology ↔ phases

| Phase (MASTER_DRIVER §6) | Superpowers skills in play |
|--------------------------|----------------------------|
| P0 data harness | writing-plans + TDD (data quality, risk limits) + verification-before-completion |
| P1 LLM research feature | brainstorming (feature design) + forward-validation as the gate |
| P2 hypothesis generation | brainstorming (per hypothesis) → writing-plans → walk-forward; log hypotheses-tried |
| P3 monitor/orchestrate | subagent-driven-development for UI + shadow mode |
| P4 tiny real capital | verification-before-completion ≡ §14 go-live checklist; human partner sign-off |
| P5 scale | systematic-debugging on any live/backtest divergence; demotion rule (§6) |

TDD is mandatory in `src/risk/**` and `src/execution/**` (money code). Every phase boundary is
a `verification-before-completion` / DONE-GATE event: re-prove the whole system from clean,
not just the new slice.
