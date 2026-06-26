"""src/core/clock.py — §9 — NTP-synced clock; reject on skew.

Provides a skew-checked time source. Clock skew is a circuit-breaker (§4): pause and alert, never trade on a skewed clock.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
