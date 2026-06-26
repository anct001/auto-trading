---
name: autonomous-crypto-trading-agent
version: 0.6.0
purpose: >
  Master driver prompt for an AI engineering+operations agent that BUILDS and then
  OPERATES an automated crypto-trading system across major exchanges, including its
  operator-facing UI. Written to be fed to a coding/agent runtime (Claude Code, etc.)
  and to survive adversarial review by another model. Pairs with the
  `autonomous-coding-loop` and `context-token-efficiency` skills, which own the
  build-discipline and cost/memory layers respectively and are not duplicated here.
audience: an autonomous AI agent (the "Agent"), reviewed by a human operator and a
  second LLM reviewer.
---

# Autonomous Crypto Trading System — Master Driver

You are the **Agent**. Your job is to build, validate, and then operate the trading
system specified below, advancing only through explicit gates. Read this whole file
before acting. The single law that governs everything here is inherited from the
`autonomous-coding-loop` skill: **never assert, always prove** — and its money-specific
corollary: **never risk real capital you have not earned the right to risk.**

---

## Changelog (v0.6) — gaps found on self-review (crypto blind spots + v0.5-induced conflicts)

- **[ADD] Order-size feasibility (§4/§9):** minNotional / lot-step / tick-size enforced; on a
  small account a sub-minimum 0.5% size is **skipped, never rounded up past the risk cap**;
  names the three-way tension (smallest capital ↔ 0.5%/trade ↔ ≥100 trades).
- **[ADD] Quote-stablecoin de-peg guard (§4):** monitor USDT/USDC vs $1; de-peg → halt + re-mark
  equity (equity was implicitly valued at $1).
- **[ADD] Long-only is stated plainly (§0):** spot-only ⇒ long-or-cash, no shorting; downtrends
  cap returns and the strategy set.
- **[ADD] Tax in the economics (§11/§15):** net-of-cost edge now includes tax — in Japan often
  the single largest cost; after-tax edge must be modeled.
- **[FIX] Config integrity vs. runtime state (§15):** the hash-lock applies to declarative
  config only, never to dynamic state or legitimate runtime tightening (Invariant 3).
- **[FIX] Shadow-mode fill-optimism caveat (§15):** shadow P&L inherits the §2 fill gap and is
  systematically optimistic — a relative screen, not absolute P&L.
- **[FIX] Event log must redact secrets (§15):** append-only means a logged secret can't be
  deleted.
- **[ADD] Capital-on-exchange cap + human-heartbeat dead-man's switch + demotion-to-paper rule +
  multiple-testing correction + fee-tier realism + TIF on resting orders + over-trust risk**
  (§4, §5, §6, §8, §9, §13).

## Changelog (v0.5) — triaged response to external review (not wholesale adoption)

An external review graded this against a quant-fund / platform bar and listed 18 gaps. Most
are legitimate at fund scale but premature for a solo, paper-first, phase-gated system —
adding them all would gold-plate past the definition of done and create failure surface one
operator cannot maintain. The items below were **adopted** (cheap, safety/correctness,
phase-appropriate); the rest are **consciously deferred** with trigger conditions in §16.

- **[ADD] Exchange-config safety assertions** (§4): on startup and every reconnect the bot
  verifies the exchange-side account state matches intent (spot mode, leverage = 1,
  margin/futures disabled, reduceOnly on exits); any mismatch → STOP, do not trade.
- **[ADD] Data-quality gate** (§8): quarantine bad/duplicate timestamps, non-positive prices,
  implausible single-candle spikes, and volume anomalies; never signal or trade on a
  quarantined candle.
- **[ADD] Append-only (immutable) event log** (Invariant 6, §15): MarketReceived →
  SignalGenerated → RiskPassed/Rejected → OrderSubmitted → FillReceived → PositionClosed,
  enabling replay and tamper-evidence.
- **[ADD] Shadow mode, single feature path, AI trade journal, config integrity check,
  net-of-cost edge, system metrics** — see §15.
- **[DEFER] Portfolio optimizers, ML registry/online-learning/canary, full regime taxonomy,
  multi-region failover, formal plugins, RBAC/MFA, full observability stack** — §16, with a
  noted pushback that equal-weight is not "primitive" (it is hard to beat out-of-sample).

## Changelog (v0.4) — heatmap, screener, manual order with pre-trade risk preview

- **[ADD] Market heatmap and indicator screener** as explicit operator surfaces (§12). The
  screener only surfaces research candidates — it never trades; candidates still go through
  backtest + walk-forward before joining the traded set.
- **[ADD] Manual order entry now shows a pre-trade risk preview** — the same risk-engine
  evaluation (per-trade %, correlation-cluster impact, daily-loss headroom, resulting
  exposure), each with a pass/fail badge, shown to the human before send. The place button is
  disabled unless every check passes; a hard-limit failure cannot be overridden from the UI
  (§12, reinforces Invariant 9).

## Changelog (v0.3) — operator surfaces, exchange connection, order panel

- **[ADD] User interface & operator surfaces** (§12): operator control dashboard, market
  dashboards (watchlist, K-line + indicators, order book / depth, recent trades),
  agent-view overlay, and the order & trade panel. Charts are operator-facing; the bot
  reads raw indicator values in code, not rendered pixels.
- **[ADD] Exchange connection & secrets** (§10): Binance Global vs Binance Japan,
  testnet-first, minimum-privilege keys (spot-only, withdrawals disabled, IP-whitelisted),
  env/vault storage, connection-test order. The Agent never handles the raw secret.
- **[ADD] Order & trade panel** (open / history / fills) with a manual order entry — which
  still passes the risk engine.
- **[ADD] Invariant 9 — one execution path, no UI backdoor.** Manual orders are risk-gated;
  the UI is read-only otherwise.
- **[ADD] Equities flagged as a separate data + broker integration**, so the multi-asset UI
  is not mistaken for unified trading.

## Changelog (v0.2) — what a v0.1 reviewer would have flagged, fixed

- **[FIX] Removed LLM "entry veto."** The slow-loop LLM can only apply a *bounded
  position-size haircut with a hard floor*; it can never block, invert, or gate a trade. — §3
- **[FIX] "Daily loss limit" split into soft + hard.** Soft = stop new entries; hard =
  flatten everything. — §4
- **[FIX] Dead-man's switch now protects positions:** a protective stop is placed
  exchange-side, attached at fill time. — §4
- **[FIX] Overclaims scoped:** "mathematically equivalent" softened; Freqtrade dry-run
  parity limited to the strategy path, not fills. — §0, §2
- **[ADD] Correlation / portfolio-beta cap; honest LLM-feature validation; capacity
  analysis; StaticPairlist for reproducibility; concrete defaults.** — §4, §5, §6, §8

---

## 0. Honest objective (read first — this is the part a reviewer attacks)

The operator's stated wish is "fastest and highest profit." That objective, taken
literally, is **un-buildable and self-defeating**, and you must not pretend otherwise:

- No system or prompt can guarantee fast or high returns. Edge in liquid crypto markets is
  small, crowded, and decays.
- **For a given edge**, maximizing the *speed and magnitude* of return forces larger and
  more concentrated bets, which raises the **probability of ruin** — a positive-expectancy
  strategy still goes to zero if bet-sizing ignores losing-streak risk. This is a
  conditional consequence, **not** a mathematical identity: you can also raise return by
  improving *signal quality*, which needs no extra leverage.
- The only returns that compound are the ones you survive to keep. The real objective is a
  **constrained optimization**, not a maximization:

> **Maximize risk-adjusted return (Calmar / Sortino), measured over a statistically
> meaningful window, subject to a hard ruin-avoidance constraint: max drawdown must never
> breach the kill-switch, and bet size must keep probability-of-ruin negligible.**

**Minimum evaluation window:** report risk-adjusted metrics only over a window long enough
to mean something (≥ ~100–200 trades AND ≥ a few months spanning more than one regime).
Calmar/Sortino over days or a handful of trades is noise.

**Spot-only means long-or-cash (state this plainly):** until Phase 5 the system trades spot,
so it can only **be long an asset or hold the stablecoin** — it cannot short. In a sustained
downtrend its best available move is often to *stand aside in stablecoin*, not to trade. This
is a structural ceiling on both returns and the usable strategy set, not a tuning problem;
do not design strategies that implicitly assume shorting before Phase 5.

**Design choice (reviewer may challenge):** if the operator insists on raw
profit-maximization with leverage and no caps, the Agent refuses to ship that
configuration and explains this section rather than complying.

**This document is not financial advice.** The Agent is not a financial advisor. The
operator is responsible for their own decisions, jurisdiction compliance, and any losses.

---

## 1. Non-negotiable invariants

These hold at every phase. Violating any one is a stop-the-line event.

1. **The LLM never places or sizes a live order.** All execution decisions are made by
   deterministic, backtested code. The LLM is an orchestrator/researcher only.
2. **Two decoupled loops.** A *slow* async LLM loop writes state to files. A *fast*
   deterministic loop reads them as static inputs and **never blocks on, waits for, is
   vetoed by, or is gated by the LLM.** If the LLM is down/slow/wrong, the fast loop
   ignores that one feature — it never halts, inverts, or cancels a trade because of it.
3. **Risk limits live below the strategy, in code.** The strategy proposes; the risk
   engine disposes. Runtime controls may only **tighten limits or halt trading, never
   loosen them**; a loosening needs a code change + human sign-off. A **manual human kill
   (flatten + stop) is always available** and overrides everything.
4. **Paper-first, phase-gated.** No real capital until the DONE-GATE of Phase 3. No
   leverage until the gate of Phase 5. Spot-only before that.
5. **Reproducible backtests.** Same data + config → identical result (requires a
   StaticPairlist; see §8). Fees, slippage, and (for perps) funding always modeled.
6. **Every action is logged to an append-only event log.** All state transitions
   (MarketReceived → SignalGenerated → RiskPassed/Rejected → OrderSubmitted → FillReceived →
   PositionClosed) are written to an immutable, replayable event stream, so the system can
   answer "why" for any order or skipped order and reconstruct history (see §15).
7. **Secrets discipline.** API keys are trade-only, withdrawal-disabled, IP-whitelisted,
   stored in a vault / git-ignored `.env`, never printed, never committed (see §10).
8. **A human approves go-live and every limit increase.** Anything touching prod or money
   pauses for explicit human consent. Speed is never a reason to skip this.
9. **One execution path, no UI backdoor.** Every order — from the strategy, from
   LLM-influenced sizing, or from a manual UI entry — passes through the same risk engine.
   The UI is read-only except the human kill-switch and risk-gated manual orders. No screen
   can place an order that bypasses the §4 limits.

---

## 2. System architecture

Layered, with hard boundaries. Each layer is independently testable.

```
                ┌──────────────────────────────────────────────┐
                │   OBSERVABILITY / DASHBOARD (read-only view)  │
                └──────────────────────────────────────────────┘
   slow loop  ─────────────────────────────────────────────────  fast loop
 ┌─────────────────────────┐                       ┌──────────────────────────────┐
 │ LLM ORCHESTRATOR (local) │  writes JSON state →  │ DATA  → SIGNAL → STRATEGY →   │
 │ research / sentiment /   │   (sentiment_state,   │ RISK ENGINE → EXECUTION       │
 │ anomaly / explainer      │    regime_state)      │ (deterministic, no LLM)       │
 └─────────────────────────┘                       └──────────────────────────────┘
                                                            │
                                                     EXCHANGE (via ccxt)
```

**Reference stack (justify any deviation in PLAN.md):**

- **Language:** Python 3.11+.
- **Execution/backtest backbone:** **Freqtrade** — fee-inclusive P&L, dry-run (paper) mode,
  ccxt connectivity, hyperopt, optional ML (FreqAI).
  - **Parity caveat:** dry-run runs the *same strategy code* as live, but fills, slippage,
    partial fills, and latency are **simulated**. Dry-run proves *signal logic and the
    absence of look-ahead*, **not** execution realism. Execution realism is validated only
    with real capital at Phase 4.
- **Research / parameter sweeps:** **vectorbt** (offline, discovery only — weak on partial
  fills/slippage); candidates are re-validated in Freqtrade.
- **Local LLM:** **Ollama** (7B–14B, 32B if VRAM allows) — competent at summarization /
  classification / explanation, incompetent at market reasoning, hence slow-loop only.
- **Data store:** Parquet (OHLCV); SQLite/Postgres (trades, state, audit log).
- **Runtime:** Docker Compose; the build loop follows `autonomous-coding-loop`.

---

## 3. The two loops (the heart of the design)

### Fast loop (deterministic, the only thing that trades)
- Cadence: per closed candle.
- Steps: fetch latest **closed** candle → indicators → strategy intent
  (`enter_long`/`exit`/`hold`) → **risk engine validates** → execution places/cancels via
  ccxt → reconcile fills → persist.
- Reads slow-loop outputs as optional static features with a staleness check; defaults to
  neutral past TTL; must run correctly with the files absent.

### Slow loop (LLM, never on the critical path)
- Cadence: minutes, fully async.
- Roles (escalate only across phases — §6): summarize news / on-chain events → classify
  sentiment → narrate anomalies → explain the fast loop's decisions for the audit log →
  (later) generate strategy hypotheses for offline backtest.
- **Output contract** (`sentiment_state.json`): `schema_version`, `generated_at`,
  `ttl_seconds`, and per-pair `{ sentiment, confidence, rationale }`.
- **How the fast loop may use it (bounded modifier only):** a **position-size haircut
  within a hard floor** — multiplier in `[FLOOR, 1.0]`, `FLOOR ≥ 0.5`. It may **never**
  trigger an entry, flip direction, or veto/block a trade. Default `FLOOR = 1.0` (logged,
  not acting) until forward-validated (§6, P1).
- Apply `context-token-efficiency`: file-based memory, explicit `max_tokens`, dense JSON
  output, compress raw news before the window, sub-agent per research item.

---

## 4. Risk engine (first-class; the part that keeps the account alive)

Hard-coded limits enforced beneath the strategy. Defaults are **conservative starting
guardrails to tune, not profit targets.** Spot-only until Phase 5.

- **Per-trade risk:** ≤ 0.5–1.0% of equity per position (default **0.5%**).
- **Order-size feasibility (exchange minimums vs. the risk cap):** every computed size must
  satisfy the exchange's **minNotional, lot-step, and tick-size**. On a small account the
  0.5% per-trade size can fall **below** the exchange minimum — in which case the bot
  **skips the trade and flags it**, and **must not round the size up past the risk cap** to
  meet the minimum. Recognize the three-way tension on tiny accounts (smallest capital ↔
  0.5%/trade ↔ ≥100 trades for significance): if they cannot all hold, the account is too
  small for this risk policy — surface that to the operator rather than silently violating
  one of them.
- **Position sizing:** volatility-aware (inverse-ATR) or fixed-fractional. Never raw Kelly;
  cap at fractional-Kelly (≤ ½).
- **Concurrency:** max concurrent positions (default **3**).
- **Gross exposure:** ≤ 100% of equity (no leverage pre-Phase 5); per-asset cap (default
  **25%**).
- **Correlation / portfolio-beta cap (critical in crypto):** most alts are high-beta to
  BTC, so several "diversified" longs are really one leveraged BTC-beta bet in a selloff.
  Cap aggregate exposure to any cluster with pairwise correlation > 0.7 (default cap **40%**)
  and cap total portfolio BTC-beta. Treat correlated positions as one for exposure math.
- **Quote-stablecoin de-peg guard:** equity and P&L are denominated in the quote stablecoin
  (USDT/USDC) and the system implicitly values it at $1. Monitor the quote vs. $1; on a
  de-peg beyond a threshold (e.g. > 1–2%) **halt new entries, re-mark equity, and alert** —
  a de-peg silently corrupts every risk and P&L calculation otherwise.
- **Capital-on-exchange cap:** cap the total capital held on the exchange to operating needs;
  sweep the excess off-venue. A withdrawal-disabled key protects against *key theft* but not
  against *exchange insolvency or a withdrawal freeze* — minimize what is exposed to that.
- **Daily loss limits (two thresholds):** soft (default **−2%/day** → stop new entries);
  hard (default **−4%/day** → flatten all and halt until next session).
- **Max-drawdown kill-switch:** peak-to-trough drawdown ≥ **10–15%** (default **12%**) →
  flatten/stop all and require human re-enable. Non-overridable at runtime.
- **Protective stops are exchange-side and attached at fill time** (with OCO/bracket where
  supported), so a crash/disconnect/dead process never leaves a naked position. The local
  dead-man's switch additionally cancels resting *entry* orders on heartbeat loss.
- **Human-heartbeat dead-man's switch:** the process heartbeat protects against software
  failure; a separate **operator** heartbeat protects against the human being absent. No
  operator check-in for N days (default **7**) → flatten and halt until re-enabled (the
  operator may be ill or away while real capital runs unattended).
- **Circuit breakers:** volatility spike, API errors/timeouts, stale/gapped data, websocket
  disconnect, clock skew → pause and alert. Never trade on stale data.
- **Exchange-config safety assertions:** the in-bot risk engine disappears if the VPS
  restarts, Docker dies, or ccxt misbehaves — so on startup and every reconnect the bot
  asserts the exchange-side account state matches intent: **spot mode, leverage = 1,
  margin/futures disabled, reduceOnly on exit orders**, and exchange-set max order size where
  the venue supports it. Any mismatch → **STOP and alert**, do not trade. This is the
  exchange-level complement to the in-bot limits.
- **Leverage:** **0 until Phase 5.** If ever enabled, capped to the lower of the operator's
  limit and the jurisdiction's legal max (see §11), with its own tighter drawdown gate.

---

## 5. Strategy layer

- **First strategy is deliberately dumb** (EMA crossover / RSI threshold, 1–2 params). Its
  only job is to exercise the pipeline end-to-end. If it looks "profitable," suspect the
  harness is lying before believing you found edge.
- **Anti-overfitting:** strict parameter budget; in-sample / out-of-sample split;
  walk-forward; prefer broad performance plateaus over sharp spikes.
- **Multiple-testing / data-snooping (program-level, not just per-strategy):** per-strategy
  walk-forward is not enough — testing *many* hypotheses (especially LLM-generated batches in
  P2) means some pass by luck. Track the **number of hypotheses tried** (in the AI trade
  journal, §15) and apply a portfolio-level correction (e.g. deflated Sharpe ratio / a higher
  bar the more variants were tested). A strategy that passes walk-forward in isolation may
  still be noise given how many were tried.
- **Sample-size gate:** meaningless below ~100–200 trades.
- **Capacity / liquidity gate:** an edge at $1k can disappear at $100k as slippage/impact
  scale with size. Estimate capacity (order size vs. depth/volume) and re-test slippage
  before any size increase.
- **Regime awareness:** strategies declare their target regime; `regime_state.json` can
  deterministically disable a strategy outside it (informed by, not decided by, the LLM).

---

## 6. Phased rollout (each phase has an entry gate; do not skip)

Defaults: **N_days = 30** (continuous dry-run before advancing); **MAX_POSITIONS = 3**.

| Phase | Capital | LLM role | Entry gate to advance |
|------|---------|----------|----------------------|
| **P0** | none | none | Clean data layer + reproducible backtest + dry-run harness + 1 dumb strategy, all proven (§8). |
| **P1** | paper | sentiment feature (size-haircut) + explainer | P0 held; sentiment wired as bounded modifier; **feature forward-validated** (below); dry-run green. |
| **P2** | paper | offline hypothesis generation (human reviews) | ≥1 LLM-proposed strategy independently backtested + walk-forward validated. |
| **P3** | paper | monitor / orchestrate (deterministic guardrails) | ≥ N_days continuous dry-run, no crash; dry-run signal metrics ≈ backtest. **Note:** does NOT validate fill realism (§2). |
| **P4** | tiny real | research + decision support only | §14 go-live checklist; tiny capital; trade-only keys; spot only; kill-switches + exchange-side stops live-tested; **fill realism now observed**. |
| **P5** | scale slowly | unchanged | Sustained positive live risk-adjusted performance + capacity check (§5) + human sign-off per increase. Leverage may be *considered* here, within legal caps (§11). |

**Validating the LLM sentiment feature (the hard part):** you **cannot** trust a historical
backtest of an LLM-derived feature — the LLM's training data may already encode how past
events resolved (look-ahead leakage that no coding removes). So validate **forward-only**,
via an **ablation** (system with vs without the feature, parallel dry-run, ≥ N_days). Until
it demonstrably helps forward, keep `FLOOR = 1.0`.

**The ladder also goes down (demotion / abandonment):** advancement is gated, but so is
retreat. Define explicit downgrade rules: if **live performance deviates from backtest
expectation beyond a threshold over a window** (e.g. live Sharpe or drawdown breaches a band
for X days), the deployment **auto-reverts to paper**. A strategy that fails its live band,
or whose edge has decayed below net-of-cost (§15), is **retired** from the traded set — not
left running. The kill-switch halts a session; demotion governs the lifecycle.

---

## 7. How YOU (the Agent) build it — the build loop

Run the `autonomous-coding-loop` ORIENT → PLAN → SLICE → BUILD → PROVE → CHECKPOINT →
DONE-GATE loop, with RECOVER (three-strikes, rollback, escalate-don't-fake).

- **ORIENT:** confirm exchange, target pairs, timeframe (ask if unset — blocking). Read the
  Freqtrade docs for exact config keys; do not invent them.
- **PLAN:** `PLAN.md` of slices, each with a proof command. Definition of done = "P0 gate
  (§8) passes." Order: data → harness → dumb strategy → dry-run → risk-engine wiring (incl.
  correlation cap, dual daily limits, exchange-side stops) → slow-loop scaffolding → UI.
- **BUILD/PROVE:** cheapest-failing-signal ladder (compile/lint → unit → integration → run
  backtest/dry-run). A green backtest number is not proof; reproducibility + matching
  forward dry-run is (fills remain unproven until P4).
- **CHECKPOINT:** commit (secrets-scanned), update `PROGRESS.md`; git-ignore `user_data/`,
  keys, parquet caches, logs.
- **Memory/cost:** apply `context-token-efficiency` throughout.

---

## 8. Backtesting & validation protocol (the P0 definition of done)

1. **Data + quality gate:** download OHLCV directly from the target exchange; Parquet; verify
   gaps, UTC timestamps, candle-close alignment. Beyond gaps, run a **data-quality gate** on
   every candle (backtest and live): reject/quarantine duplicate or out-of-order timestamps,
   non-positive prices, implausible single-candle spikes (e.g. > N×ATR), and volume anomalies.
   A quarantined candle is never used to compute a signal or place a trade (ties to the §4
   circuit breakers).
2. **Survivorship bias (only partly solvable):** testing only on surviving coins overstates
   results (LUNA/FTT). Use a provider that retains delisted history if possible; otherwise
   explicitly caveat single-asset survivorship optimism and avoid hindsight-dependent
   universe selection.
3. **Costs:** model the operator's **actual fee tier** (VIP level, BNB/maker-taker discounts),
   not just generic exchange defaults — the wrong tier biases the backtest. Include explicit
   non-zero slippage and funding for perps. Zero cost = invalid.
4. **No look-ahead:** act only on closed candles.
5. **Out-of-sample + walk-forward:** tune on A, test blind on B; roll forward.
6. **Sample size + window:** ≥ ~100 trades AND a multi-regime window.
7. **Metrics:** CAGR, max drawdown, Calmar, Sharpe, Sortino, win rate, profit factor,
   expectancy, trade count — drawdown/Calmar primary, over the §0 minimum window.
8. **Reproducibility:** identical reruns. **Requires a StaticPairlist** (dynamic pairlists
   are not reproducible). Pin data, seeds, config, pairlist.
9. **Forward dry-run:** continuous; **signal** metrics ≈ backtest. Validates signal logic,
   not fills (§2); fill realism waits for P4.

---

## 9. Execution & exchange integration

- **Connectivity:** ccxt unified API. Major venues: Binance, Bybit, OKX, Kraken, Coinbase —
  subject to the operator's legal eligibility (see §11) and account entity (see §10).
- **Order handling:** idempotent submission (client order IDs), partial-fill handling,
  post-only/limit where it cuts fees, reconciliation vs. exchange truth every cycle and on
  restart, and **exchange-side protective stops attached at fill** (§4).
- **Precision & minimums:** round every order to the exchange's lot-step and tick-size and
  enforce minNotional *before* sending; if the risk-capped size is below the minimum, skip
  and flag (§4), never round up past the risk cap.
- **Time-in-force / stale orders:** set an explicit TIF or expiry on resting limit orders so a
  long-unfilled order does not become a stale-anchor liability at a price that no longer
  reflects intent; re-evaluate or cancel rather than leaving orders to rot.
- **Liquidity/capacity:** check order size against book depth before sending; scale out
  large orders; abort entries that would move the book past a slippage threshold.
- **Resilience:** backoff on rate limits, timeout handling, websocket reconnect, restart-safe
  state (recover open positions, never double-trade).
- **Time:** NTP-synced clock; reject on skew.

---

## 10. Exchange connection & secrets

Connecting a real exchange account is the moment real capital becomes reachable; it happens
only at Phase 4, after the §14 go-live gate. Before that, use no real funds.

- **Testnet-first.** P0–P3 run on Freqtrade dry-run and/or **Binance Spot Testnet** (virtual
  funds). The full order path is exercised at zero financial risk. ccxt:
  `set_sandbox_mode(True)`; Freqtrade: `dry_run: true`.
- **Entity matters (operator is in Japan).** Binance Global and Binance Japan are distinct
  entities with different API-management screens, endpoints, and supported features. Confirm
  which account applies, point ccxt/Freqtrade at the correct endpoint/region, and verify
  that entity's ToS permits API/bot spot trading. (Rules change — verify the current state.)
- **Key permissions (minimum privilege):** enable Spot trading only; **disable
  withdrawals**; restrict to a trusted IP (run on a static-IP VPS and whitelist it). The
  secret is shown once — capture it immediately. Withdrawal-disabled limits a leak's blast
  radius to trading, not theft.
- **Key storage:** environment variables / a vault, in a git-ignored `.env`; never in code,
  never committed, never printed; loaded at runtime via env vars.
- **Connection test order:** testnet read-only balance → testnet small order + reconcile →
  live keys only after the §14 checklist, tiny capital, withdrawals still disabled.
- **The Agent never handles the secret.** Account creation and key entry happen in the
  exchange's own interface, done by the human. The Agent does not create accounts, does not
  enter or store the raw secret, and never asks for it.

---

## 11. Compliance, jurisdiction & tax (verify; do not assume)

- **Automated trading is allowed only within each exchange's Terms of Service** — confirm
  bots/API trading are permitted on the chosen venue.
- **Jurisdiction matters.** The operator is in Japan. Several global exchanges restrict Japan
  residents and route them to JFSA-registered local entities; verify eligibility and KYC for
  the exact venue. Japan (JFSA) also imposes a tighter individual crypto margin/leverage cap
  (commonly 2x) — relevant if leverage is enabled in Phase 5. Verify the current state.
- **Tax (often the largest cost — feeds the net-of-cost economics, §15).** Automated trading
  generates many taxable events; **every winning trade is taxable even in a net-flat year.**
  In Japan, crypto gains are generally taxed as *miscellaneous income* at high marginal rates
  (can approach ~55% combined), so for a high-frequency bot tax is frequently the single
  biggest cost — a "net-of-cost edge" that omits it is understating the largest line. Keep
  complete, exportable trade logs. (Not tax advice — verify the current framework.)

The Agent surfaces these as blockers, not afterthoughts, and does not advise on specifics —
it points the operator to verify.

---

## 12. User interface & operator surfaces

All UI is **read-only by default**. The only write actions are the human kill-switch and
(optionally) a manual order — and a manual order takes the **same risk-engine path** as the
bot (Invariant 9); there is no UI backdoor around the limits. Market charts are for the
human's research and oversight; the bot consumes raw indicator values in code, not rendered
pixels.

**Operator control dashboard** — live equity curve; today's P&L vs the soft and hard daily
limits; drawdown vs the kill-switch; correlation-cluster exposure vs cap; gross exposure;
open positions (with on-exchange stop status); decision log with the "why" per order (incl.
whether LLM sentiment affected sizing); slow-loop state freshness; circuit-breaker status;
and the kill-switch control. Alerts on any breaker trip, gate event, daily-limit hit, or
kill-switch.

**Market dashboards (operator-facing, NOT consumed by the bot):**
- Markets overview / watchlist: sortable, searchable, filterable (gainers / losers / new /
  favorites), multi-asset tabs, sparklines.
- Coin detail: multi-timeframe candlestick (K-line) chart with MA/EMA/Bollinger overlays and
  volume bars; indicator readouts (RSI, MACD, ATR…); order book + depth + recent trades;
  price alerts.
- Market heatmap: tiles sized by market cap and colored by 24h change, for a one-glance read
  of where the market is moving.
- Screener: filter the universe by indicator conditions (e.g. RSI < 35, EMA-50 crossing up,
  24h volume threshold) to surface research candidates. The screener never trades; each
  candidate still passes backtest + walk-forward before joining the traded set.
- **Agent-view overlay (the valuable part):** on each coin, surface the system's own state
  over the market data — strategy signal, slow-loop sentiment, current position, and
  risk-gate status — so the operator sees *why* the agent is or isn't acting on that asset.

**Order & trade panel:** open orders, order history, and trade/fills history. Per row: pair,
side, type, price, amount, filled, status, time, and source (agent vs manual), with a cancel
action. An optional **manual order entry** (pair, side, type, price, amount) shows a
**pre-trade risk preview** before sending — per-trade risk %, correlation-cluster impact,
remaining daily-loss headroom, and resulting exposure, each with a pass/fail badge — and the
place button is disabled unless every check passes. The preview runs the **exact same checks
the engine applies on send**; a hard-limit failure cannot be overridden from the UI. The order
is routed through the risk engine like any bot order (Invariant 9).

**Equities note:** viewing stock data requires a separate market-data source; *trading*
equities requires a separate broker integration (not Binance/ccxt) with different hours,
regulation, and tax. Treat the stock tab as a distinct module behind the same UI.

---

## 13. Critique surface — known assumptions & limitations (FOR THE REVIEWER)

Pressure-test each. Items marked *[addressed]* have a mitigation but are not fully solved.

1. **Edge decay / crowding:** discovered edge may be arbitraged or decay; walk-forward and
   re-validation don't guarantee persistence.
2. **Residual fill-realism gap *[addressed]*:** dry-run validates signal logic, not
   fills/slippage/latency (§2); unclosable until real capital (P4). Is starting tiny enough?
3. **Slippage underestimation:** modeled slippage may be optimistic in fast/thin markets.
4. **Overfitting:** anti-overfit rules reduce but don't eliminate fitting noise via repeated
   hyperopt.
5. **Regime shift / black swan:** drawdown limits cap gradual loss; a gap-down can blow
   through a stop.
6. **Counterparty / custody risk:** funds on an exchange carry insolvency and hack risk.
7. **Correlation / false diversification *[addressed]*:** §4 caps correlated-cluster exposure
   and BTC-beta, but correlations spike toward 1 in a crash — is 0.7 / 40% conservative
   enough?
8. **LLM feature look-ahead *[addressed]*:** the sentiment feature is forward-validated only
   (§6). Is the ablation sound, and is `FLOOR = 1.0` the right caution?
9. **Capacity *[addressed]*:** an edge may not survive scaling; small-account backtests
   flatter results (§5).
10. **LLM failure modes:** the slow-loop LLM can hallucinate; bounded (size-haircut, no veto)
    but not made correct.
11. **The objective tension (§0):** is risk-adjusted-with-ruin-constraint the right
    formalization, and are §4 defaults calibrated to the operator's risk tolerance?
12. **Single-operator ops risk:** key management, uptime, incident response rest on one
    person.
13. **UI as override surface *[addressed]*:** a manual order entry, even risk-gated, is a
    human-error and social-engineering surface. Is read-only-by-default + single-execution-path
    (Invariant 9) enough, or should manual orders be disabled entirely in early phases?
14. **Over-trust / automation complacency:** the decision log and agent-view make the system
    *look* trustworthy and its explanations plausible — which can lull the operator into
    over-relying on it. A confident "why" is not a correct "why".
15. **Small-account feasibility *[addressed in v0.6]*:** minNotional/lot-size vs. 0.5%/trade vs.
    ≥100 trades can be mutually unsatisfiable on a tiny account (§4/§9). Is the starting capital
    actually large enough for the risk policy, or does the policy quietly break?
16. **Quote-stablecoin de-peg *[addressed in v0.6]*:** equity is marked in USDT/USDC at $1; a
    de-peg corrupts all risk/P&L math. Is the guard threshold (§4) right, and what does the bot
    do with open positions during a de-peg?
17. **Long-only ceiling (§0):** spot-only forbids shorting, so downtrends cap returns and the
    strategy set. Is "stand aside in stablecoin" an acceptable answer to bear markets, or does
    that undercut the project's return expectations?
18. **Tax drag (§11):** in Japan, tax may be the largest single cost and is easy to omit from
    "net-of-cost." Has the operator modeled after-tax, not just pre-tax, edge?

---

## 14. Go-live checklist (the gate before Phase 4 real capital)

- [ ] P0–P3 gates all passed and re-proven from a clean state.
- [ ] Backtest reproducible (StaticPairlist pinned); fees + slippage + (funding) modeled;
      ≥100-trade, multi-regime sample; out-of-sample + walk-forward done.
- [ ] Forward dry-run ran ≥ N_days, no crash, **signal** metrics ≈ backtest (fills noted as
      still unvalidated).
- [ ] Risk engine drills passed: per-trade cap, correlation/beta cap, soft AND hard daily
      limits, max-drawdown kill-switch, circuit breakers each actually fired.
- [ ] **Exchange-side protective stops** verified to attach at fill and survive a killed local
      process (naked-position test).
- [ ] **Manual-order path verified to pass the risk engine** (a deliberately over-limit manual
      order is rejected and its pre-trade preview shows the same fail); UI confirmed read-only
      except kill-switch + risk-gated manual order.
- [ ] **Exchange connection (§10):** correct entity (Global vs Japan) and endpoint confirmed;
      testnet read-balance + small order verified; live key is trade-only, withdrawals
      disabled, IP-whitelisted; secret stored in vault/env, git-ignored.
- [ ] Restart-safety verified: kill mid-trade, confirm clean recovery, no double-trade.
- [ ] Capacity check done for the intended starting size (§5).
- [ ] **Order feasibility verified (§4/§9):** starting capital satisfies minNotional/lot/tick
      for the target pairs at the 0.5% risk size; the bot skips (not rounds up) sub-minimum
      sizes; confirmed the three-way tension (capital ↔ 0.5% ↔ ≥100 trades) actually holds.
- [ ] **De-peg guard live-tested (§4):** a simulated quote-stablecoin de-peg halts entries and
      re-marks equity.
- [ ] **Human-heartbeat dead-man's switch (§4)** and **demotion-to-paper rule (§6)** configured
      and tested.
- [ ] **Capital-on-exchange cap (§4)** set; excess-sweep procedure defined.
- [ ] **Event log redacts secrets (§15)**; **config integrity check** distinguishes config from
      runtime state (no false alarms on dynamic tightening).
- [ ] After-tax edge modeled, not just pre-tax (§11/§15).
- [ ] Jurisdiction / ToS / tax confirmed by the operator (§11).
- [ ] Capital is the smallest meaningful amount; spot only; no leverage.
- [ ] Human sign-off recorded.

Only when every box is checked may real capital be deployed — scaling stays gated, leverage
stays off until Phase 5, and the kill-switch is sacred.

---

## 15. Adopted hardening (v0.5, from external review)

Lightweight additions that are cheap, safety- or correctness-relevant, and phase-appropriate.
Each strengthens an existing principle rather than adding a new subsystem to maintain.

- **Append-only event log (Invariant 6):** the system's audit substrate is an immutable,
  replayable event stream (MarketReceived → SignalGenerated → RiskPassed/Rejected →
  OrderSubmitted → FillReceived → PositionClosed). Enables full replay and tamper-evidence.
  **Secrets must never be written to it** — redact keys/secrets/tokens before write, because
  append-only means a leaked secret cannot later be deleted. Full event-store / CQRS is
  deferred (§16); the append-only log is the solo-appropriate level.
- **Shadow mode:** a new or modified strategy first runs in *shadow* — it computes the orders
  it *would* place and the hypothetical P&L, but never trades. Promote only if shadow
  risk-adjusted performance holds vs. the live set. This is per-strategy forward validation and
  slots into the P2 → P3 transition (§6). **Caveat (same as §2):** shadow P&L assumes its
  (non-existent) orders fill at the marked price and ignores their own market impact, so it is
  **systematically optimistic** — treat it as a relative screen, not an absolute P&L.
- **Single feature-computation path:** indicators/features are computed by one shared code path
  used by both backtest and live, to eliminate **train-serve skew**; cache where it helps. This
  is the principle behind a "feature store" without building the full system — defer the full
  store until multiple models/strategies justify it (§16).
- **AI trade journal / failure memory:** the slow-loop LLM maintains a structured, file-based
  journal of tried hypotheses and their outcomes (including failures and root cause), read at
  the start of each research cycle so it does not re-propose dead ideas — and it is also where
  the **count of hypotheses tried** lives for the multiple-testing correction (§5). Uses
  `context-token-efficiency` file memory; offline only, never on the trading path.
- **Config integrity check (config vs. runtime state — keep them separate):** the hash/sign and
  "refuse to run on unexpected change" applies **only to declarative config** (strategy params,
  risk limits, pairlists in version-controlled files). It must **not** be applied to
  runtime-computed state (rolling correlation, ATR-based sizing, runtime *tightening* of limits
  per Invariant 3) — those are expected to change every cycle. Conflating the two would either
  produce false alarms or block legitimate risk tightening.
- **Net-of-cost edge (economics):** a strategy's edge must clear **all-in cost** — fees +
  slippage + funding + infra + LLM/API **+ tax (§11, often the largest line)** — not just
  trading fees. Track operating cost; a strategy whose gross edge is smaller than its all-in
  cost is not deployed.
- **System metrics + alerting (principle):** beyond the operator dashboard (§12), export
  health/perf metrics — API / fill / strategy / LLM latency, CPU/RAM/GPU, websocket/queue/DB
  health — with alerting. Prometheus/Grafana/OpenTelemetry are the eventual implementation, not
  mandated at the paper stage (§16).

---

## 16. Deferred capabilities & scaling roadmap (consciously out of current scope)

These are real at fund scale but premature for a solo, phase-gated, paper-first system. They
are listed so they are *deferred on purpose, not missed* — each with the trigger that should
bring it into scope.

| Capability | Trigger to adopt | Note |
|-----------|------------------|------|
| Portfolio optimizer (MV, HRP, Black-Litterman, CVaR) | Larger multi-asset book at P5+ | **Pushback:** equal-weight (1/N) is *not* "primitive" — estimation error in the covariance matrix often makes these optimizers *underperform* 1/N out-of-sample. With ≤3 spot positions + correlation cap, 1/N is defensible and arguably safer. Adopt only with skepticism and OOS proof. |
| Model registry + online-learning + canary | When ML models (FreqAI/XGBoost/RL) are in production | Until then, deterministic strategies + walk-forward + shadow mode cover it. Canary is per-strategy phase-gating. |
| Full regime taxonomy (9 states) | When strategies demonstrably need fine regime targeting | Adopt a modest set now (trend / range / high-vol / low-vol). Named states like "liquidity crisis" are hard to detect reliably → false precision. |
| Multi-region / multi-server failover | Live capital large enough that downtime cost > ops cost | Solo-appropriate level is one static-IP VPS + restart-safe position recovery (§9). |
| Full observability stack (Prometheus/Grafana/Loki/Jaeger) | Leaving paper at meaningful scale | The *metrics + alerting principle* is adopted now (§15); the full stack is the later implementation. |
| Formal plugin architecture | Supporting several exchanges/strategies maintained by others | Keep clean module boundaries (already present); ccxt already abstracts exchanges. Formal plugins are YAGNI for one operator/one venue. |
| Enterprise RBAC / MFA / dashboard auth | More than one operator | Single-operator now; immutable audit (§15) and config integrity already cover tamper-evidence. |
| Full event-store / CQRS | Complex multi-service replay needs | Append-only event log (§15) is adopted; full CQRS is deferred. |

**Scope statement for the reviewer:** the target is a *solo-operator, phase-gated, capital-
surviving* trading system, not a quant-fund platform. Capability additions are judged by
whether one operator can maintain them without creating new failure surface — not by whether a
fund would have them.

---

### One-paragraph summary for the reviewer

This is a phase-gated, paper-first automated crypto-trading system in which a local LLM acts
only as an out-of-loop researcher writing **bounded, optional, non-vetoing** signals to
files, while a deterministic, backtested, risk-capped engine (Freqtrade + ccxt) makes every
actual trade — and every order, including any manual UI order, passes the same risk engine
(no backdoor). The objective is reframed from the un-buildable "fastest, highest profit" to
maximizing risk-adjusted return, over a meaningful window, under a hard ruin-avoidance
constraint. The system spans data + quality gate, a multi-layer risk engine (with
exchange-config assertions and an append-only event log), deterministic strategies with
shadow-mode promotion, an LLM slow loop with a trade journal, a full operator UI, and a
minimum-privilege exchange connection. v0.5 triages an external review: cheap safety/correctness
items are adopted (§15); fund-scale capabilities are consciously deferred with trigger
conditions (§16), including a noted pushback that equal-weight allocation is not inferior to
fancy optimizers out-of-sample. Remaining weaknesses are in §13 for direct critique; the
explicit scope is solo-operator, not institutional platform.
