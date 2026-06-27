# HANDOFF.md — session snapshot for continuing (read after CLAUDE.md)

> A compact, current state-of-play so a fresh session (e.g. Claude Code in local mode) can
> continue without re-deriving everything. The authoritative spec is `docs/MASTER_DRIVER.md`;
> the running detail log is `PROGRESS.md`; this file is the one-page orientation.

## Where things stand

- **Branch:** `claude/new-session-vcxyyp` · **Phase:** P0 (not closed) · **Tests:** 248 passing,
  `ruff` clean. Everything is committed and pushed.
- **What this is:** a solo-operator, paper-first, phase-gated crypto trading system. Deterministic
  engine makes every trade; a local LLM (later) is an out-of-loop researcher only.

## What's built (tested) vs. stub

| Built (real logic, tested) | Still stub / not started |
|---|---|
| `data/` feed (closed candles), store (Parquet, reproducible), quality gate (§8.1) | `features/cache.py` |
| `features/indicators` (EMA, ATR — single feature path) | `llm/**` (P1 sentiment — needs Ollama) |
| `strategy/` base + `ema_cross` (intent-only) | `ui/**` (P3 operator surfaces) |
| `backtest/` runner (+protective stop), metrics, walk-forward | `strategy/shadow.py` (P2/P3) |
| `risk/**` FULL engine R0–R7 (the single gate, sizing, limits, de-peg, exchange-assert, killswitch, runtime tighten-only) | — |
| `execution/**` broker, stops, reconcile, convergence (E1–E4) | — |
| `events/log` (append-only, secret-redacted) · `core/` (config hash-lock, secrets, clock) | — |
| `fast_loop.py` (the §3 keystone) · `dry_run.py` (paper CLI) | — |

## Decisions locked this session (don't re-litigate)

- **Exchange entity (ADR 0002):** trade on **Binance Japan**; use **Binance Global** public data
  for *viewing only* — Global data must never reach a signal/order path.
- **ORIENT:** pair **BTC/USDT**, timeframe **1h**, fees **VIP0 + BNB = 0.075%** maker/taker,
  slippage 0.05% (`config/backtest/costs.json`, hash-locked).
- **Deviation (§7):** in-house deterministic backtest runner instead of Freqtrade for P0;
  Freqtrade returns at P3 for dry-run parity (see `PLAN.md` Slice 6).
- **Config is hash-locked** (`config/config.lock.json`, §15) — re-lock after any intentional
  config change (regenerate via `core.config.build_manifest` + `write_manifest`).

## Security/correctness audit — 13 findings, ALL fixed (don't re-find)

Fail-closed risk inputs (#1), unforgeable single-gate Approval (#2), backtest enforces the
protective stop (#3), event-log redacts secret values incl. AIza/AKIA tokens (#4), missing-price
fails closed not crash (#5), non-positive stop rejected (#6), idempotency-on-restart documented
(#7), `re_arm` operator gate (#8), partial-stop naked detection (#9), price>0 + epsilon (#11).
#12/#13 are conscious notes. See the audit entry in `PROGRESS.md`.

## ⚠️ Environment caveats for local mode

- **Binance is geo-blocked from the cloud build env (HTTP 451).** On your **local machine /
  static-IP VPS** you should be able to reach Binance Japan — that's where the real P0 close
  happens. The provisional data demo uses **Kraken** (`scripts/first_real_backtest.py`,
  `dry_run.py --data-exchange kraken`).
- **Unverified:** confirm **BTC/USDT is listed on Binance Japan** (its primary quote is JPY); if
  not, fall back to **BTC/JPY**. Confirm KYC/ToS allow API/bot spot trading (§11).
- **No keys are needed** for tests or the demos. Real keys → P4 only, in a git-ignored `.env`.

## How to run (full detail in README "Quickstart")

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 248 offline tests
ruff check .
python -m src.dry_run --data-exchange kraken --pair BTC/USDT --timeframe 1h --iterations 1
```

## Recommended next steps (in order)

1. **Close P0 — operational, not code:** run `dry_run.py --iterations 0` (or a real backtest) on
   your venue from a network that can reach it, with enough history for a **≥100-trade,
   multi-regime** sample; compare dry-run signal metrics to backtest (§8.9). Verify BTC/USDT on
   Binance Japan.
2. **Then P1 (LLM, optional):** wire `llm/**` sentiment as a **bounded size-haircut, FLOOR=1.0**,
   forward-validated via ablation (§6). Needs Ollama (see `ops/HARDWARE.md`).
3. **Then P3:** `ui/**` operator dashboard + `strategy/shadow.py`, and reintroduce Freqtrade for
   dry-run fill parity.
4. **Never skip a gate; the kill-switch is sacred; no real capital before the §14 checklist.**

## Hardware

See **`ops/HARDWARE.md`**. Short version: the trading box is hardware-trivial (cheap static-IP
VPS, no GPU); the only real hardware cost is the **optional** local LLM (GPU or Apple Silicon),
which is off the trading path. You can reach P4 with no GPU.
