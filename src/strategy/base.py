"""src/strategy/base.py — §5 — strategy interface: emits intent, NEVER sizes or executes.

Strategies propose enter_long/exit/hold and declare a target regime. The risk engine disposes (Invariant 3). A strategy never computes size or places an order.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
