"""backtest/runner.py — §8 — Freqtrade integration; StaticPairlist for reproducibility.

Reproducible backtests: same data+config -> identical result. Requires a StaticPairlist. Models the operator's actual fee tier + non-zero slippage. A green number is not proof (§8).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
