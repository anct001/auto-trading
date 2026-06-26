"""src/llm/sentiment.py — §3 — bounded position-size haircut (FLOOR >= 0.5).

Multiplier in [FLOOR, 1.0], FLOOR>=0.5. May NEVER trigger an entry, flip direction, or veto a trade. Default FLOOR=1.0 (logged, not acting) until forward-validated (P1).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
