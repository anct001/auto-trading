"""src/llm/orchestrator.py — §3 — slow loop (Ollama); async; writes state files only.

OUT of the trading path (Inv. 1, 2). Async, minutes cadence. Writes JSON state files; the fast loop reads them as optional, stale-checked, non-vetoing inputs.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
