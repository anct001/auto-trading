"""src/events/log.py — §15 — append-only event log (immutable, secret-redacted).

MarketReceived -> SignalGenerated -> RiskPassed/Rejected -> OrderSubmitted -> FillReceived -> PositionClosed. Immutable and replayable. Secrets MUST be redacted before write — append-only means a leaked secret cannot be deleted (Inv. 6).

Scaffold only — no logic yet. See docs/MASTER_DRIVER.md for the authoritative spec.
"""

# TODO(P0+): implement per docs/MASTER_DRIVER.md and the relevant docs/phases/ gate.
