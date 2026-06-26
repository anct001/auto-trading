"""src/risk/limits.py — §4 — per-trade, daily soft/hard, drawdown, correlation/beta caps.

MONEY CODE (TDD mandatory). Per-trade <=0.5%; soft -2%/day stops entries, hard -4%/day flattens; drawdown >=12% kill-switch (non-overridable); correlation-cluster (>0.7 corr) cap 40%; per-asset 25%; gross <=100% (no leverage pre-P5).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
