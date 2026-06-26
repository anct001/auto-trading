# backtest/replay/ — §15 adversarial replay harness

Replays recorded/synthetic adversarial scenarios through the system to prove it survives them,
not just average markets:

- **Flash crash** — gap-down through a stop; does the exchange-side stop / drawdown gate behave?
- **Data gap / outage** — missing or stale candles; does the §8 quality gate quarantine and the
  §4 circuit breaker pause (never trade on stale data)?
- **Exchange / websocket outage** — disconnect + reconnect; restart-safe reconciliation, no
  double-trade (§9).
- **Quote-stablecoin de-peg** — does the §4 de-peg guard halt entries and re-mark equity?

Drives the risk-engine drills required by the §14 go-live checklist. Scaffold only — no
scenarios implemented yet.
