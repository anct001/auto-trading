# RUNBOOK.md — operator operations guide

> Step-by-step for the things **only you (the operator) can do** — the gates that are operational,
> not code. The code paths are built and tested; this ties the existing tooling together into the
> sequence that actually closes P0→P3 and prepares P4. Authoritative spec: `docs/MASTER_DRIVER.md`;
> current state: `PROGRESS.md`; orientation: `HANDOFF.md`. Nothing here spends real money.

## 0. Golden rules (never bypass)

- **No real capital before the §14 go-live checklist passes** and you have recorded HUMAN SIGN-OFF.
- The LLM never places or sizes an order. If Ollama is down, trading is unaffected — never "fix" it
  by letting the model act.
- Every limit increase / go-live pauses for explicit human consent. Speed is never a reason to skip.
- Run everything below from **your own machine** (the cloud build env is geo-blocked from Bybit/
  Binance — 451/403). bitbank is the trade venue; Bybit is deep-history *research/view* data only.

## 1. One-time environment setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q          # expect all green
ruff check .       # expect clean
```

The console here is cp1258 (Vietnamese); every entrypoint calls `core.console.force_utf8_stdio()`
so non-ASCII (§, →, —) doesn't crash. If you add a new printing entrypoint, call it too.

**No API keys are needed** for anything in this runbook. Real keys are P4-only, in a git-ignored
`.env` (never committed, never logged), trade-only + withdrawals-disabled + IP-whitelisted.

## 2. Sanity: one paper tick on the real venue

```bash
python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --timeframe 1h --iterations 1
```

Expect a `MarketReceived` + `SignalGenerated` in `events/dry_run.jsonl` (the runner pre-warms a
rolling buffer because bitbank serves only ~12 closed 1h candles per fetch). No order on a `hold`.

## 3. Signal parity (§8.9) — prove no train/serve skew

Reproduces the live rolling-window decision over real history and diffs it against the full-series
backtest. Green = identical intents.

```bash
python scripts/dryrun_parity.py --exchange bitbank --pair BTC/JPY --days 30
# deeper history (research venue): 
python scripts/dryrun_parity.py --exchange bybit --pair BTC/USDT --days 365
```

Expect `rate = 1.0000` (zero mismatches). Re-run this against the **event log** of your long
dry-run (step 5) to confirm parity holds in production.

## 4. Research an edge (the real go-live blocker)

The current EMA-cross has **no validated edge** (−22% / 2.5y). TA, cross-sectional momentum, and
regime filtering all fail the deflated-Sharpe gate (`docs/research/strategy_search_findings.md`).
Until something clears **DSR ≥ 0.95 AND beats buy-and-hold**, go-live stays correctly blocked.

```bash
# TA / cross-sectional / regime families:
python scripts/strategy_search.py --exchange bybit --pair BTC/USDT --timeframe 1h --days 730
# a3 — funding-rate carry (a different information source; needs a perp for funding):
python scripts/funding_edge_search.py --exchange bybit --pair BTC/USDT \
    --perp BTC/USDT:USDT --timeframe 1h --days 730
```

Read the printed table honestly: a green `VALIDATED` line is the first real edge, not a foregone
conclusion. If nothing validates, that is the correct, expected result — do **not** lower the DSR
bar or curve-fit to force a pass (§5 punishes exactly that).

## 5. The ≥30-day forward dry-run (P3 gate)

Run the paper loop continuously for **≥30 days**, no crash, with the operator dashboard up:

```bash
# writes (manual order / kill-switch) protected by a token if the UI is reachable off localhost:
export UI_AUTH_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')"
python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --timeframe 1h \
    --iterations 0 --serve-ui --ui-port 8787 --alert-webhook "<your Telegram/Slack URL>"
```

- Dashboard: `http://127.0.0.1:8787/` (also `/markets` `/coin` `/orders` `/chat` `/terminal` `/pro`).
- A per-tick error (network blip) is caught, recorded as `last_error`, and skipped — the loop keeps
  running (this is what makes ≥30 days survivable). If it ever exits, that's a P3-gate failure —
  capture the log and debug before restarting the clock.
- After the run, re-run **step 3 parity** against the produced event log.

## 6. Restart-safety (§9) — kill mid-trade, expect clean recovery

While a paper position is open, hard-kill the process, then restart it. Confirm via the event log +
`execution/reconcile` that: the exchange-side protective stop is still present (no naked position),
no order is double-submitted (idempotent client-order-id + reconcile), and local state matches
exchange truth. This is a required §14 item ("kill mid-trade → clean recovery, no double-trade").

## 7. Optional: local LLM (P1 sentiment + P2 hypotheses + the /chat assistant)

All LLM features are **off the trading path** and fail-soft; skip this entirely and trading is
unaffected. To enable:

```bash
# install Ollama (see ollama.com), then:
ollama serve &
ollama pull llama3.1
```

- **P1 sentiment ablation:** run the slow loop to produce `sentiment_state.json`, then run parallel
  ≥30-day dry-runs feature-on vs feature-off. Keep `sentiment_floor = 1.0` in
  `config/risk/default.json` until it *demonstrably* helps forward; only then lower it (≥0.5) and
  **re-lock the config** (§8, §15). Never backtest the feature (look-ahead, §6).
- **P2 hypotheses:** run the offline proposer → **you approve each** → `validate_hypothesis` on real
  bitbank BTC/JPY → clear the gate with ≥1 net-of-cost+tax edge surviving the §5 correction.
- **/chat assistant:** `--serve-ui` already wires it (`--chat-model llama3.1`). Read-only — it
  explains state, never trades.

## 8. Config integrity (§15) — after any intentional config change

`config/config.lock.json` hash-locks the declarative configs. After an intentional change:

```bash
python -c "from src.core.config import build_manifest, write_manifest; \
  import pathlib; root=pathlib.Path('.'); \
  write_manifest(build_manifest([root/'config/risk/default.json', \
    root/'config/strategy/ema_cross.json', root/'config/backtest/costs.json']), \
    root/'config/config.lock.json')"
pytest -q   # the lock test must pass (catches unlocked drift)
```

Re-locking is a deliberate act with human sign-off — never automate it into a hook.

## 9. Go-live pre-flight (§14) — the gate, not the trigger

```bash
python scripts/go_live_preflight.py
```

Auto-checks (config lock, leverage=0, sentiment_floor=1.0, kill-switch, no secret in `.env.example`)
plus the operator-only §14 items. `is_ready` stays **False** until every gate is genuinely done and
HUMAN SIGN-OFF is recorded. Placing no orders, it never moves money — it only tells you the truth
about readiness. When (and only when) it is green and you have signed off, P4 begins with the
smallest meaningful capital, spot-only, no leverage.

## Quick reference

| Goal | Command |
|------|---------|
| Install + verify | `pip install -e ".[dev]" && pytest -q && ruff check .` |
| One paper tick | `python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --iterations 1` |
| Signal parity | `python scripts/dryrun_parity.py --exchange bitbank --pair BTC/JPY --days 30` |
| TA edge search | `python scripts/strategy_search.py --exchange bybit --pair BTC/USDT --days 730` |
| Funding edge search | `python scripts/funding_edge_search.py --exchange bybit --pair BTC/USDT --perp BTC/USDT:USDT --days 730` |
| Long dry-run + UI | `python -m src.dry_run --data-exchange bitbank --pair BTC/JPY --iterations 0 --serve-ui` |
| Go-live pre-flight | `python scripts/go_live_preflight.py` |
