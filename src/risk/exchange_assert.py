"""src/risk/exchange_assert.py — §4 — exchange-config safety assertions.

MONEY CODE (TDD mandatory). On startup and every reconnect, assert exchange-side state matches intent: spot mode, leverage=1, margin/futures disabled, reduceOnly on exits. Any mismatch -> STOP and alert, do not trade.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
