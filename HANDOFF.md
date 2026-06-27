# HANDOFF.md — session snapshot for continuing (read after CLAUDE.md)

> A compact, current state-of-play so a fresh session (e.g. Claude Code in local mode) can
> continue without re-deriving everything. The authoritative spec is `docs/MASTER_DRIVER.md`;
> the running detail log is `PROGRESS.md`; this file is the one-page orientation.

## Where things stand

- **Branch:** `claude/new-session-vcxyyp` · **Phase:** P0 harness proven · **P1** sentiment built
  (inert at FLOOR=1.0) · **P2** hypothesis-validation machinery built · **P3** deterministic cores
  built (shadow / risk-preview / dashboard model); none formally closed (all await operator-side
  operational steps + Ollama + UI transport) · **Tests:** 361 passing, `ruff` clean. Committed
  (local; push when a remote is configured).
- **What this is:** a solo-operator, paper-first, phase-gated crypto trading system. Deterministic
  engine makes every trade; a local LLM is an out-of-loop researcher (sentiment size-haircut only).

## What's built (tested) vs. stub

| Built (real logic, tested) | Still stub / not started |
|---|---|
| `data/` feed (closed candles + paginated history), store (Parquet, reproducible), quality gate (§8.1) | `features/cache.py` |
| `features/indicators` (EMA, ATR — single feature path) | `llm/journal.py` (P2 trade journal) |
| `strategy/` base + `ema_cross` + **shadow (P3 hypothetical P&L)** | `ui/api.py` FastAPI/SSE transport + market charts/heatmap pixels |
| `backtest/` runner (+protective stop), metrics, walk-forward, **parity (§8.9)** | `strategy/shadow.py` (P2/P3) |
| `risk/**` FULL engine R0–R7 + sentiment size-multiplier (tighten-only) | — |
| `execution/**` broker, stops, reconcile, convergence (E1–E4) | — |
| `llm/` sentiment (P1, inert at FLOOR=1.0) + orchestrator + Ollama backend + **journal + hypothesis_gen (P2)** | — |
| `backtest/` **multiple_testing (deflated Sharpe) + hypothesis (validate_hypothesis)** (P2 §5) | — |
| `ui/` preview (Inv 9) + dashboard/model + api/server (stdlib HTTP + dashboard & **markets** pages) + screener + agent_view + **markets** | coin-detail K-line / order-book / order-panel pixels; live-state wiring |
| `events/log` (append-only, secret-redacted) · `core/` (config hash-lock, secrets, clock, console) | — |
| `fast_loop.py` (§3 keystone, sentiment+regime-wired) · `dry_run.py` (paper CLI, `--sentiment-state`, `--serve-ui`) · `ui/live.py` (UI↔live dry-run) | — |

## Decisions locked this session (don't re-litigate)

- **Exchange entity (ADR 0002, amended 2026-06-27):** trade on **bitbank** (JFSA; ccxt had no
  Binance Japan entity, bitbank is the only ccxt JFSA venue with OHLCV). **Binance Global** public
  data is *viewing only* — Global data must never reach a signal/order path. bitbank has no public
  sandbox → paper/dry-run until P4.
- **ORIENT:** pair **BTC/JPY**, timeframe **1h**, fees **bitbank maker 0.00% / taker 0.10%**,
  slippage 0.05% (`config/backtest/costs.json`, hash-locked). Operator confirms live fee schedule.
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

- **Binance is geo-blocked from the cloud build env (HTTP 451).** Confirmed (2026-06-27) that
  the **operator's local Windows machine CAN reach** Binance/Bybit/OKX/KuCoin/Coinbase — no 451.
  That's where the real P0 close happens. The provisional harness now uses **Bybit** (deep
  history, non-Binance per ADR 0002) in `scripts/first_real_backtest.py`; `dry_run.py` still
  defaults to Kraken but accepts `--data-exchange`. **Kraken public OHLC caps at ~720 candles** —
  fine for a dry-run tick, too few for a backtest sample.
- **This console is cp1258 (Vietnamese), not UTF-8.** CLIs call `core.console.force_utf8_stdio()`
  so non-ASCII output (§, →, —) doesn't crash; any new printing entrypoint must do the same.
- **Operator to verify (bitbank):** confirm KYC + that bitbank's ToS allow API/bot spot trading
  (§11), and the live fee schedule (§8.3). BTC/JPY is confirmed listed+active via ccxt.
- **No keys are needed** for tests or the demos. Real keys → P4 only, in a git-ignored `.env`.

## How to run (full detail in README "Quickstart")

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 261 offline tests
ruff check .
# paper dry-run on the real venue (no keys, no real capital):
python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --timeframe 1h --iterations 1
# signal parity vs backtest on bitbank data (§8.9):
python scripts/dryrun_parity.py --exchange bitbank --pair BTC/JPY --days 30
```

## Recommended next steps (in order)

1. **Close P0 — only operator-account steps remain; venue now pinned (bitbank BTC/JPY).**
   Code path proven end-to-end on the real venue: `dry_run.py --data-exchange bitbank --pair
   BTC/JPY` runs the paper order path; `dryrun_parity.py --exchange bitbank --pair BTC/JPY`
   shows **519/519 signal parity (rate 1.0000)**. Sample-size/walk-forward proven on Bybit deep
   history (366 trades) — bitbank serves 1h one-day-per-call so deep pulls are slow. Remaining is
   the operator's bitbank account: **(a)** confirm bitbank KYC + ToS permits API/bot spot trading
   (§11); **(b)** confirm the live fee schedule / any maker-rebate campaign (§8.3 — costs.json is
   set to published 0.00%/0.10%); **(c)** run a live **≥30-day forward dry-run** on bitbank
   BTC/JPY, then `python scripts/dryrun_parity.py --exchange bitbank --pair BTC/JPY` against its
   event log to confirm parity holds in production. A *dumb* EMA-cross shows **no edge** on real
   data — P0 is about a proven pipeline, not this strategy's P&L.
2. **P1 (LLM sentiment) — code BUILT & tested, inert at FLOOR=1.0; only the ablation remains.**
   `llm/` has the full pipeline: `sentiment.size_haircut` (bounded modifier), `state_io`,
   `orchestrator.run_sentiment_cycle` (slow loop), `ollama_client` (stdlib backend). Wired into
   `FastLoop` + `dry_run.py --sentiment-state`. Remaining (operational, needs Ollama +
   `ops/HARDWARE.md`): run the slow loop to produce real `sentiment_state.json`, then the
   **ablation** — parallel dry-runs feature-on vs feature-off for ≥30 days — to decide *forward*
   if it helps (no historical backtest of the feature — look-ahead, §6). Keep `sentiment_floor`
   at **1.0** in `config/risk/default.json` until it demonstrably helps; lower it (≥0.5) + re-lock.
3. **P2 (hypothesis generation) — validation machinery BUILT & tested; needs Ollama + a human.**
   `llm/journal.py` (trial memory), `backtest/multiple_testing.py` (deflated Sharpe, §5),
   `backtest/hypothesis.validate_hypothesis` (walk-forward + DSR + journal), `llm/hypothesis_gen.py`
   (offline proposer, human-gated, strict param budget). Remaining (operational): run the proposer
   → **a human approves each** → `validate_hypothesis` on real bitbank BTC/JPY → clear the gate
   with ≥1 net-of-cost+tax validated edge that survives the §5 correction.
4. **P3 (monitor/orchestrate) — deterministic cores BUILT & tested.** `strategy/shadow.py`
   (`ShadowBook`), `ui/preview.py` (`preview_manual_order`, runs the exact engine — Inv 9),
   `ui/dashboard/model.py` (`build_dashboard`, read-only §12 view). Remaining: **(a) UI delivery**
   — **control dashboard DONE end-to-end**: `ui/server.py` (stdlib HTTP transport + `index_html()`
   page at `GET /`, `python -m src.ui.server`), `ui/screener.py`, `ui/agent_view.py`. Only the
   richer *market* surfaces (watchlist/coin-detail K-line/heatmap pixels, need a multi-asset feed)
   + `ui/markets.py` (`/markets` watchlist+heatmap, `/api/markets`) are DONE. Only coin-detail
   K-line / order-book / order-panel pixels remain (live-state wiring is DONE — `dry_run.py
   --serve-ui` serves the dashboard over the running paper loop); **(b) the gate
   itself (operational)** — a **≥30-day continuous dry-run** on bitbank BTC/JPY (no crash),
   `dryrun_parity` green, and **restart-safety** verified (kill mid-trade → clean reconcile, no
   double-trade, §9). Then reintroduce Freqtrade for dry-run fill parity.
5. **Never skip a gate; the kill-switch is sacred; no real capital before the §14 checklist.**

## Hardware

See **`ops/HARDWARE.md`**. Short version: the trading box is hardware-trivial (cheap static-IP
VPS, no GPU); the only real hardware cost is the **optional** local LLM (GPU or Apple Silicon),
which is off the trading path. You can reach P4 with no GPU.
