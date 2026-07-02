# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow SemVer-ish pre-1.0 rules (minor = feature wave, patch = fixes).

**Release process:** bump `version` in `pyproject.toml` + `__version__` in `src/__init__.py`,
add a section here, then tag (`git tag -a vX.Y.Z && git push origin vX.Y.Z`). A version is tagged
at least at every **phase DONE-GATE** (P0…P5, see `PROGRESS.md`); `v1.0.0` is reserved for the
first P4 go-live sign-off. The `/status` page shows the running version.

## [0.7.1] — 2026-07-02

The audit wave: a full re-analysis of the project produced 10 findings; all fixed, in order,
with tests (suite 603 → 637).

### Fixed
- **Daily-loss limit re-anchors at UTC midnight** (`PaperAccount.roll_day`): the day-loss soft/hard
  limits were permanently anchored to launch-day equity; long dry-runs now roll correctly
  (`DayRolled` event).
- **Restart no longer wipes paper state**: the dry-run checkpoints account/positions/trades every
  tick (`state/dry_run.checkpoint.json`, atomic write, auto-resume, corrupt file → fresh start
  with a warning; `--checkpoint off` disables).
- **Protective stops actually fire in paper mode**: resting reduceOnly stops are swept against
  every candle BEFORE the decision — gap-through fills at the open, otherwise at the stop; the
  realized loss feeds the day-loss/kill-switch path (`StopTriggered` event). Previously they were
  registered but never simulated, understating risk.
- **Replay metrics use the full equity history** — long replays no longer silently drop the
  oldest points from Sharpe/MaxDD/VaR (the 5k-point UI cap now grows with the dataset).
- **The live CLI consumes the hash-locked strategy config** (`config/strategy/ema_cross.json`)
  instead of hard-coded EMA 20/50, and market constraints (min-notional/lot/tick) come from the
  venue's ccxt metadata (TICK_SIZE vs DECIMAL_PLACES handled; fail-soft to the old defaults with
  a printed reason).
- **`autotrader` works from any directory**: all default paths anchor to the repo root
  (`AUTOTRADER_ROOT` env override → marker walk-up → package location), not the cwd.

### Added
- **CSRF hardening**: POSTs with a non-localhost `Origin` are rejected (403); `/api/chat` joined
  the token-protected writes (a cloud-provider call spends the operator's API credits).
- **§14 sign-off registry**: each operator-only go-live item has a stable slug; sign-offs are
  recorded (who/when) via `go_live_preflight.py --sign <slug> --operator <name>` into
  `ops/signoff.json`; the report and `/status` show MANUAL → SIGNED; readiness requires every
  auto-check PASS **and** all 13 items signed. Fail-closed on a corrupt registry.
- **Candle-aligned polling**: the dry-run sleeps until the next candle close + 5s grace instead
  of a blind 60s spin (`--poll-seconds` now overrides; default = auto).
- **CSV export**: `GET /api/trades.csv` (+ a ⬇ CSV link on the dashboard) for spreadsheets/tax.
- **CI**: advisory mypy step (non-blocking while annotations are tightened).

## [0.7.0] — 2026-07-01

The operator-experience wave: everything a solo operator sees, on top of the frozen trading core.

### Added
- **Replay mode**: run the paper loop over real PAST data (`--replay-days`, `--replay-file`,
  `--save-history`) — same order path as live, causal, reproducible.
- **Multi-pair & multi-strategy comparison** (`--pairs`, `--strategies`) with the `/replay`
  dashboard: sortable metric table (return, win rate, PF, MaxDD, Calmar, Sharpe, Sortino,
  VaR/CVaR 95, Monte-Carlo DD p95/p99, exposure, time-in-market), threshold colouring,
  per-run equity chart + trades.
- **Read-only AI assistant** (`/chat`): explains live state with decision-log citations and
  multi-turn memory; provider switchable per question — local Ollama (default), Anthropic,
  OpenAI-compatible, Gemini (keys from env only, masked, never logged).
- **Funding-rate research path** (a3): `data/funding.py`, `FundingCarry` strategy,
  `scripts/funding_edge_search.py` under the same walk-forward + Deflated-Sharpe gate.
- **Operator polish**: unified top-nav + favicon, bilingual EN/VI `/help` glossary, `/status`
  health page (phase, config, §14 pre-flight lights, AI backends), column tooltips,
  GitHub Actions CI, `autotrader` / `autotrader-ui` console commands, `autotrader init`
  first-run wizard writing a git-ignored `config/app.toml` profile.
- **Security**: optional `X-Auth-Token` gate on the mutating UI writes (manual order,
  kill-switch); constant-time compare; browser prompts and retries.

### Changed
- Live-loop sizing now clamps to the per-asset cap (`clamp_per_asset=True` in the fast loop) so
  entries land feasibly instead of being futilely rejected; backtest path byte-for-byte unchanged.
- Dashboard KPI colours are sign-aware (a loss never renders green); sentiment tile reads
  off/fresh/stale.

### Notes
- Still **paper-only**; go-live remains blocked by the §14 gates (correct). No validated edge yet:
  TA, cross-sectional momentum, regime filtering all fail DSR ≥ 0.95; funding-rate search awaits a
  real-data run on the operator's machine.

## [0.6.0] — 2026-06-28

Baseline matching spec v0.6 (`docs/MASTER_DRIVER.md`): P0 data harness (feed/store/quality gate,
reproducible backtest, walk-forward, parity), full risk engine R0–R7 (single gate, Inv 3/9),
execution E1–E4 (idempotent broker, exchange-side stops, restart-safe reconcile), fast-loop
orchestrator, paper dry-run CLI, event log with secret redaction, config hash-lock, P1 sentiment
pipeline (inert at FLOOR=1.0), P2 hypothesis validation (Deflated Sharpe), P3 deterministic cores +
the full §12 operator UI (dashboard/markets/coin/orders/terminal/pro), 13-finding security audit
fixed, venue pinned to bitbank BTC/JPY (ADR 0002).
