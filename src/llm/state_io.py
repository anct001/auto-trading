"""src/llm/state_io.py — §3 — sentiment_state.json / regime_state.json (TTL).

Read/write the slow-loop output contract (schema_version, generated_at, ttl_seconds, per-pair payload). Fast loop defaults to neutral past TTL and runs with files absent.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
