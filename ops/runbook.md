# ops/runbook.md — incident runbook + demotion-to-paper procedure

> Scaffold. Fill in concrete commands as the system grows. The kill-switch is sacred; when in
> doubt, **flatten and halt**, then diagnose.

## The one move that always works

**Manual human kill (Invariant 3):** flatten all positions + stop new entries. Always
available, overrides everything (including any runtime control or the LLM). Know how to trigger
it from the operator dashboard (§12) *and* directly, in case the UI is down.

## Incident response (triage order)

1. **Capital at risk?** If unsure → kill-switch first, investigate second.
2. **Circuit breaker tripped** (volatility spike, API errors, stale/gapped data, websocket
   disconnect, clock skew — §4): the bot pauses and alerts. Confirm data integrity (§8 quality
   gate) before re-enabling. Never trade on stale data.
3. **Exchange-config assertion failed** (§4): spot mode / leverage=1 / margin off / reduceOnly
   mismatch → bot STOPS. Do not override; fix the exchange-side state, re-assert, then resume.
4. **De-peg guard fired** (§4): quote stablecoin off $1 beyond threshold → entries halted,
   equity re-marked. Decide on open positions deliberately; do not auto-trade through a de-peg.
5. **Naked position suspected** (stop didn't attach): reconcile vs exchange truth (§9);
   exchange-side stops should have attached at fill — verify, re-attach if needed.
6. **Restart:** state is restart-safe (§9) — recover open positions, never double-trade. Verify
   reconciliation before the loop resumes trading.

## Demotion-to-paper procedure (§6 — the ladder going down)

Advancement is gated; so is retreat. Auto-revert a deployment to paper when **live performance
deviates from backtest expectation beyond a threshold over a window** (e.g. live Sharpe or
drawdown breaches its band for X days). A strategy that fails its live band, or whose edge has
decayed below net-of-cost (§15, incl. tax §11), is **retired** from the traded set — not left
running.

Steps:
1. Trigger the kill-switch (flatten + halt) for the affected deployment.
2. Switch it back to dry-run / paper; record the reason in the event log (§15) and `PROGRESS.md`.
3. Re-validate from a clean state before any re-promotion (DONE-GATE discipline).

## Heartbeats (§4)

- **Process dead-man's switch:** heartbeat loss → cancel resting entry orders.
- **Human-heartbeat dead-man's switch:** no operator check-in for N days (default 7) → flatten
  and halt until re-enabled. (The operator may be ill or away while capital runs unattended.)
