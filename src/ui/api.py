"""src/ui/api.py — §12 — FastAPI + SSE; read-only except kill-switch + risk-gated order.

All UI read-only by default. Only writes: human kill-switch and a manual order that takes the SAME risk-engine path (Invariant 9). No screen bypasses §4 limits.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
