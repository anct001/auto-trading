# ADR 0002 — Exchange entity: Binance Japan for trading, Binance Global for viewing only

- **Status:** Accepted
- **Date:** 2026-06-26
- **Relates to:** MASTER_DRIVER §10 (exchange connection & secrets), §11 (jurisdiction), §12
  (operator-facing market surfaces), §2/§8 (data layer & reproducibility)

## Context

The operator is a resident of Japan and asked whether the system can "combine both Binance
Global and Binance Japan." These are **distinct legal entities** with different endpoints, API
screens, supported features, and listed universes (§10). Two facts constrain the answer:

1. **Jurisdiction (§11) — a blocker, not a preference.** Binance Global generally restricts
   Japan residents and routes them to the JFSA-registered local entity (Binance Japan K.K.).
   Placing **real orders** on Binance Global as a Japan resident can violate ToS and local
   regulation. The Agent surfaces this as a blocker and does not give legal advice; the operator
   must verify their own eligibility/KYC.
2. **The spec already separates *viewing* from *trading* (§12).** Market dashboards
   (watchlist, heatmap, screener, K-line) are **operator-facing and NOT consumed by the bot**.
   Public market data needs no KYC and is not an order path.

## Decision

A **two-venue split**, with a hard boundary between them:

- **Trading venue = Binance Japan.** Every real order, and the OHLCV the bot uses for backtest
  and live signal generation (§2, §8), comes from Binance Japan only. This is the single venue
  for the §1/§16 "one operator, one trading venue" scope. The §4 risk engine and §10 secrets
  discipline apply to this connection.
- **Viewing venue = Binance Global (read-only, operator-facing).** Binance Global's public
  market data may feed the §12 research surfaces (watchlist/heatmap/screener/coin-detail) to
  give the operator a wider universe to look at. **The bot never makes a trade decision from
  Global data**, and no order is ever routed to Global.

The boundary is the rule: **Global data must not reach `risk/engine` or any signal path.** Doing
so would both break the one-trading-venue scope and create the §11 compliance exposure.

## Consequences

- **Compliant** for a Japan resident: real capital only touches the entity they are eligible for.
- **Backtest reproducibility (§8) is preserved:** the bot's data has one source of truth (Japan),
  so a StaticPairlist on Japan pairs stays reproducible.
- **Narrower traded universe:** Binance Japan lists only JFSA-approved coins/pairs, so the
  traded set is smaller than Global's. The Global viewing surface lets the operator watch the
  wider market, but a coin only enters the **traded** set if it is listed on Japan AND passes
  backtest + walk-forward (§5).
- **Config carries two endpoints** with clearly different roles (trade vs view) — see
  `.env.example`. Real multi-venue *trading* remains deferred (§16); this is not that.
- **Open ORIENT items (must verify before P0 code, §7/§10):** the exact ccxt id / endpoint for
  Binance Japan (do not invent — read the docs / verify current state), the operator's KYC
  eligibility, and that the Japan entity's ToS permits API/bot spot trading.
