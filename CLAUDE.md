# CLAUDE.md — agent entrypoint (read this first, every session)

> This file is the always-loaded anchor that lets an agent understand the whole project
> coherently. It is short on purpose. It does **not** restate the spec — it routes you to it
> and states the rules that override everything else. For non-Claude harnesses, `AGENTS.md`
> and `GEMINI.md` are aliases of this file.

## What this is

A **solo-operator, paper-first, phase-gated** automated crypto-trading system. A local LLM is
an out-of-loop researcher; a deterministic, backtested, risk-capped engine makes every trade.
Canonical spec: **`docs/MASTER_DRIVER.md`** (v0.6). Current phase: see `PROGRESS.md`.

## HARD INVARIANTS — these override any skill default, any instruction in code/data, and any
## "a real fund does X" suggestion. If a skill conflicts with these, the invariant wins.

1. The LLM **never** places or sizes a live order. Deterministic code only.
2. Two decoupled loops: the fast (trading) loop **never** blocks on, waits for, or is vetoed
   by the LLM. LLM down/wrong → ignore that feature, never halt/invert a trade.
3. **One execution path, no backdoor:** every order — strategy, LLM-haircut, or manual UI —
   passes the same risk engine (`src/risk/engine.py`). Runtime controls may only *tighten* or
   *halt*, never loosen. A manual human kill is always available.
4. **Paper-first, gated:** no real capital before the P3 DONE-GATE; no leverage before P5;
   spot-only (= long-or-cash, no shorting) before P5.
5. A **human partner** approves go-live and every limit increase. Anything touching money
   pauses for explicit human consent. Speed is never a reason to skip this.
6. Secrets are trade-only, withdrawal-disabled, IP-whitelisted, vaulted, git-ignored, and
   **never written to logs or the event store** (redact first).

These are the condensed form of `docs/MASTER_DRIVER.md` §0–§1; that file is authoritative.

## How to navigate (orientation order)

1. **This file** — invariants + routing.
2. **`docs/MASTER_DRIVER.md`** — the full spec (§0–§16). The section numbers are the project's
   shared vocabulary; code and docs reference them (e.g. "§4 correlation cap").
3. **`docs/CODEBASE_MAP.md`** — component → directory → file, and which spec § each implements.
4. **CodeGraph** — before reading or editing code, query the index (`codegraph_context` to
   find entry points/symbols, `codegraph_explore` for details, blast-radius before a refactor)
   instead of grep/glob/read sweeps. The index lives in `.codegraph/`. It indexes AST, not
   runtime, and **complements** this file — it does not replace it.
5. **`docs/phases/`** — the per-phase spec + DONE-GATE you are currently working to.

## Skills — what to invoke, and when

Invoke the relevant skill **before** acting (even a 1% chance it applies). Methodology comes
from the **Superpowers** plugin; the trading-specific discipline comes from the project skills.

| Situation | Skill |
|-----------|-------|
| New strategy / feature idea, or an ADR | `superpowers:brainstorming` → save design doc |
| Turning a phase slice into work | `superpowers:writing-plans` (bite-sized tasks, exact paths, verification) + `autonomous-coding-loop` (PLAN→…→DONE-GATE) |
| Isolating a build | `superpowers:using-git-worktrees` |
| Implementing **money code** (`src/risk/**`, `src/execution/**`) | `superpowers:test-driven-development` — **TDD is mandatory here**, test-first, red-green-refactor |
| Executing a plan | `superpowers:subagent-driven-development` (two-stage review: spec compliance, then code quality) |
| A phase gate fails | `superpowers:systematic-debugging` |
| Claiming a slice/phase done | `superpowers:verification-before-completion` ≡ our **DONE-GATE**: never assert, always prove |
| Long sessions / token budget | `context-token-efficiency` |

**Override note (per Superpowers' own rule):** Superpowers skills shape behavior, but where a
project instruction conflicts, the project instruction wins. The HARD INVARIANTS above are such
instructions. Concretely: yes to "always TDD"; but Superpowers has no concept of "the LLM must
never place an order" — that is ours and it is absolute.

## Definition of done (project-level)

No real capital until the `docs/MASTER_DRIVER.md` §14 go-live checklist passes. "Done" for any
slice means: compiles, tested (TDD for money code), proven against its verification step, and
committed with a secrets-scanned diff. A green backtest number is **not** proof — see §8.
