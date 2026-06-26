"""src/risk/killswitch.py — §4 — kill-switch + dead-man's switches (process AND human).

MONEY CODE (TDD mandatory). Manual human kill (flatten+stop) overrides everything. Process heartbeat -> cancels resting entries; operator heartbeat absent N days (default 7) -> flatten and halt until re-enabled.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
