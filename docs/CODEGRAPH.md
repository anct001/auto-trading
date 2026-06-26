# CODEGRAPH.md — human-readable module-dependency overview

A prose companion to the CodeGraph index (`.codegraph/`, git-ignored, rebuilt locally). This
file states the **intended** dependency shape so a human (or a reviewer without the index) can
sanity-check the architecture. The machine index is authoritative for *actual* edges; this file
is authoritative for *intended* boundaries.

> Scaffold note: the modules below are stubs today (no logic). This describes the target graph
> the code must grow into — and the boundaries it must never cross.

## The two loops, decoupled (§3)

```
  SLOW LOOP (LLM, async, off the trading path)        FAST LOOP (deterministic, the only trader)
  ┌───────────────────────────────────────┐          ┌──────────────────────────────────────────┐
  │ llm/orchestrator → llm/sentiment       │  writes  │ data/feed → data/quality → features/      │
  │ llm/journal (failure memory)           │  JSON →  │ indicators → strategy/* → risk/engine →    │
  │ llm/state_io (sentiment/regime state)  │  state   │ execution/{broker,stops,reconcile}         │
  └───────────────────────────────────────┘  files    └──────────────────────────────────────────┘
                                                │                    │
                                          read as OPTIONAL,    EXCHANGE (ccxt)
                                          stale-checked,
                                          NON-vetoing input
```

The fast loop reads slow-loop state files only as optional static features (TTL-checked,
neutral default, runs with files absent). There is **no edge from `llm/*` into the trade
decision** — that would violate Invariants 1 and 2.

## The convergence rule (Invariant 3 — the one to police)

**Every order path must converge on `risk/engine.validate()` before anything in
`execution/`.** Three entry points, one gate:

```
  strategy/* (bot signal) ───┐
  llm-influenced sizing  ────┼──▶  risk/engine.validate()  ──▶  execution/broker  ──▶  exchange
  ui/orders (manual entry) ──┘            │
                                          ├── risk/limits      (per-trade, daily, drawdown, correlation)
                                          ├── risk/sizing      (vol-aware + minNotional/lot feasibility)
                                          ├── risk/exchange_assert (spot, lev=1, margin off)
                                          ├── risk/depeg       (quote-stablecoin guard)
                                          └── risk/killswitch  (kill + dead-man's switches)
```

If CodeGraph ever shows an `execution/*` call reachable from a strategy or the UI **without**
passing through `risk/engine`, that is an Invariant-3 violation — fix it, do not ship it.

## Foundational dependencies (used by everything)

- `core/config` — declarative, hash-locked config (§15). Read by risk, strategy, backtest.
- `core/secrets` — env/vault keys (§10). Read by execution/llm connectivity only; never logged.
- `core/clock` — NTP/skew-checked time (§9). Read by data + execution.
- `events/log` — append-only, secret-redacted audit (§15). **Written by** risk, execution, and
  llm; the audit substrate that lets the system answer "why" for any order.

## Single feature path (§15)

`features/indicators` is the **one** code path computing RSI/MACD/ATR/EMA/Bollinger, shared by
`backtest/runner` and the live fast loop, to eliminate train-serve skew. Nothing should compute
indicators independently.

## UI boundary (§12)

`ui/api` and `ui/{dashboard,market,orders}` are read-only except the kill-switch and a manual
order — and a manual order routes through `risk/engine` like any bot order. The bot consumes raw
indicator values in code; charts are operator-facing only.
