"""src/risk/depeg.py — §4 — quote-stablecoin de-peg guard.

MONEY CODE (TDD mandatory). Monitor USDT/USDC vs $1; on de-peg beyond threshold (>1-2%) halt new entries, re-mark equity, and alert. A de-peg silently corrupts all risk/P&L math otherwise.

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
