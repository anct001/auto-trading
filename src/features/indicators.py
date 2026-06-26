"""src/features/indicators.py — §15 — single feature path: RSI / MACD / ATR / EMA / Bollinger.

ONE shared indicator code path used by BOTH backtest and live to eliminate train-serve skew. The bot reads raw values here; charts are operator-only (§12).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
