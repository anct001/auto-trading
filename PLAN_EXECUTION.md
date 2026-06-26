# PLAN_EXECUTION.md — Execution layer (money code, TDD-mandatory)

> Dedicated test-first plan for `src/execution/**` (§9). Money code: every slice is
> test-first, red→green→refactor. The execution layer is **downstream of the risk gate** — it
> only ever submits orders the risk engine already approved (Invariant 3/9). It talks to the
> exchange via an injected ccxt-like client, so all logic is testable against a mock (no
> network, no real capital). Real fills are only observed at P4 (§2 parity caveat).

## Non-negotiables this plan encodes in code

1. **No backdoor (Inv. 3/9):** the broker refuses to submit anything that isn't a *approved*
   `RiskDecision`. The only way to reach the exchange is through an approval.
2. **Idempotency (§9):** every order carries a client order id; re-submitting the same id never
   double-trades.
3. **Precision & minimums (§9):** round to lot-step / tick-size before sending (down, per R1).
4. **Exchange-side protective stops attached at fill (§4):** a crash/disconnect never leaves a
   naked position.
5. **Restart-safe reconciliation (§9):** recover open positions from exchange truth on restart;
   never double-trade.
6. **TIF on resting orders (§9):** an explicit time-in-force/expiry so a stale limit doesn't rot.

## Injected exchange (mockable)

A `SupportsExchange` Protocol with the slice of ccxt we use: `create_order(symbol, type, side,
amount, price, params)`, `fetch_order(id, symbol)`, `cancel_order(id, symbol)`,
`fetch_open_orders(symbol)`, `fetch_positions()`. Tests pass a `FakeExchange` recording calls.

## Slices

### E0 — Execution types
- **Do:** `Fill` (order_id, qty, price, status), `OrderStatus` constants (open/closed/canceled/
  partial), `SubmitResult` (exchange id, client_order_id, status, filled, remaining). Reuse
  `risk.types.Order`.
- **Proof:** `pytest tests/execution/test_types.py`.

### E1 — Broker: idempotent submit + precision + TIF + approval-gated (`broker.py`) ✅
> Done: `Broker.submit(decision, ...)` refuses unapproved decisions (exchange never called),
> idempotent per client_order_id, floors qty/price to lot/tick (shared `floor_to_step`), sets
> TIF, tracks partial fills from the reply. 6 tests. (E0 types folded in as `SubmitResult`.)
- **Do:** `Broker(exchange, markets)`; `submit(decision, *, client_order_id, order_type, tif)`:
  - **refuse** if `not decision.approved` (Inv. 3) — exchange never called.
  - **idempotent:** a repeated client_order_id returns the cached result, no second create.
  - **precision:** qty floored to lot-step, price to tick-size before sending.
  - **TIF:** explicit timeInForce in params; client_order_id passed for idempotency.
  - track submitted orders; expose partial-fill (filled/remaining) from the exchange reply.
- **Proof:** `pytest tests/execution/test_broker.py` — refuse-unapproved, create args, idempotent
  same-id, precision rounding, TIF attached, partial-fill tracked.

### E2 — Exchange-side protective stops at fill (`stops.py`) ✅
> Done: `protective_stop_price()` (entry − mult×ATR, tick-floored) + `StopManager.attach`/
> `attach_protective` placing a reduceOnly sell stop, idempotent per id. 5 tests.
- **Do:** on a buy fill, attach a reduceOnly protective stop (OCO/bracket where supported) at a
  stop price derived from the entry (e.g. entry − atr_stop_mult×ATR). Idempotent; reduceOnly.
- **Proof:** `pytest tests/execution/test_stops.py` — stop attached at fill, reduceOnly, price
  correct, not duplicated.

### E3 — Restart-safe reconciliation (`reconcile.py`)
- **Do:** `reconcile(local_state, exchange_truth)` → the corrected state + actions: adopt
  exchange-open orders/positions, drop local ghosts, never re-submit an order that already
  exists (no double-trade). Detect a naked position (no protective stop) → flag to re-attach.
- **Proof:** `pytest tests/execution/test_reconcile.py` — adopt unknown exchange position,
  reconcile filled-while-down order, no double-submit, naked-position flagged.

### E4 — Convergence (Inv. 3) end-to-end
- **Do:** an integration test proving the only path from a strategy/manual order to
  `exchange.create_order` is `engine.validate → (approved) → broker.submit`; an unapproved or
  over-limit order never reaches the exchange.
- **Proof:** `pytest tests/execution/test_convergence.py`.

## Definition of done

- [ ] Every slice green via TDD.
- [ ] Broker refuses unapproved orders; idempotent; precise; TIF set.
- [ ] Protective stop attaches at fill and is reduceOnly.
- [ ] Restart recovers from exchange truth without double-trading.
- [ ] Convergence test: no path to the exchange bypasses `engine.validate` (Inv. 3/9).
- [ ] (When CodeGraph is rebuilt) the graph shows every order path through `risk/engine`.
