"""src/execution/broker.py — §9 — idempotent orders, precision, TIF.

MONEY CODE (TDD mandatory). Client-order-id idempotency, partial-fill handling, post-only/limit where it cuts fees, explicit TIF on resting orders. Rounds to lot-step/tick-size and enforces minNotional BEFORE sending.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
