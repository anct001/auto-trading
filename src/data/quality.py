"""src/data/quality.py — §8 — data-quality gate (the P0 spine).

Quarantines duplicate/out-of-order timestamps, non-positive prices, implausible single-candle spikes (>N*ATR), and volume anomalies. A quarantined candle is NEVER used to compute a signal or place a trade (§4 circuit breakers).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
