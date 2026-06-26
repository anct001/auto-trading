"""src/risk/sizing.py — §4/§9 — vol-aware sizing + minNotional/lot/tick feasibility.

MONEY CODE (TDD mandatory). Inverse-ATR / fixed-fractional; cap at fractional-Kelly (<=1/2), never raw Kelly. If the risk-capped size is below the exchange minimum, SKIP and flag — never round up past the risk cap.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
