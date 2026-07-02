# Strategy search findings — 2026-06-28 (edge research, P2 §5)

Honest research log. Reproduce: `python scripts/strategy_search.py --exchange bybit --pair
BTC/USDT --timeframe {1h,4h,1d} --days 730 --min-trades 20`. Method: each candidate run through
out-of-sample **walk-forward** + **Deflated Sharpe Ratio** (Bailey & López de Prado), all sharing
ONE journal so the **multiple-testing correction** counts them together (§5). An edge must clear
DSR ≥ 0.95 **and** beat buy-and-hold net of cost. Cost model = bitbank published fees (costs.json).

## Results (BTC/USDT, Bybit, ~2 years)

| TF | strategy | OOS trades | OOS Sharpe | DSR | verdict |
|----|----------|-----------:|-----------:|----:|---------|
| 1h | ema_12_26 | 251 | −2.48 | 0.003 | REJECTED |
| 1h | ema_5_20 | 447 | −3.34 | 0.000 | REJECTED |
| 1h | rsi_14_30_70 | 59 | −0.30 | 0.122 | REJECTED |
| 1h | donchian_20 | 160 | −1.56 | 0.001 | REJECTED |
| 1h | donchian_55 | 63 | −0.90 | 0.018 | REJECTED |
| 4h | rsi_14_30_70 | 21 | **+0.34** | 0.353 | REJECTED (under-sampled) |
| 4h | (others) | 17–116 | −1.2…−1.7 | <0.02 | REJECTED |
| 1d | ema_12_26 | 5 | +0.02 | 0.478 | REJECTED (no sample) |

Buy & hold over the window: **−3.6% to −4.3%** (the asset itself drifted down — no free trend).

## Conclusions (truthful)

1. **No edge.** DSR never approaches 0.95 on any strategy/timeframe; every candidate is rejected.
   Simple TA on BTC has **no validated edge** here — consistent with theory (liquid, efficient).
2. **Cost-frequency is the killer.** Sharpe rises monotonically as frequency falls (1h −3.34 →
   4h −1.4 → 1d ~0): fees churn away returns at high frequency. `ema_5_20` (447 trades) is worst.
3. **Lower frequency trades sample for cost.** 4h/1d cut cost drag but collapse trade count
   (1–21 trades) → can't satisfy the ≥100-trade sample-size gate → can't validate even if positive.
4. **Even passive holding lost** this window, so "beat buy-and-hold" was a low bar and still nothing
   cleared the statistical bar. There was no easy money in this period/asset.

## Where a real edge would have to come from (not curve-fitting)

More parameter tuning of these families is futile — the DSR with N=5 trials already shows nothing,
and tuning harder just inflates the multiple-testing bar (§5). A genuine edge needs a *different
information source*, e.g.: cross-sectional / relative-value across pairs, a real regime model that
sits a strategy out of its bad regime, funding-rate or order-flow signals, or alternative data —
each still validated forward-only with the deflated-Sharpe discipline. **Until something clears
the gate, go-live stays blocked — correctly.**

## Follow-up: cross-sectional momentum (a1) + regime filtering (a2) — also no edge

Tried the two "different information source" ideas. Both built, tested, and evaluated honestly:

**a1 — cross-sectional momentum** (`backtest/cross_sectional.py`, `scripts/cross_sectional_search.py`):
rank a 7-pair basket (BTC/ETH/SOL/XRP/ADA/DOGE/BNB-USDT) each bar, hold the strongest top_k.
Run on Bybit 1h, 1y: **every config −62% to −90%**, all negative Sharpe — *worse* than equal-weight
buy-and-hold of the basket (−47%). In a falling market, momentum-chasing rotated into the assets
about to fall hardest. No edge; arguably anti-edge here.

**a2 — regime filtering** (`strategy/regime_classify.py`, Kaufman Efficiency Ratio → trend/range):
gate a strategy's entries to its target regime. Added `donchian_20+regime` and `rsi+regime` to the
search (4h, 2y): the filtered variants were **slightly worse** (donchian Sharpe −1.46 vs −1.28;
rsi 0.20 vs 0.26) and cut trade counts (worsening the sample). The ER filter didn't rescue them.

**Net:** simple TA, cross-sectional momentum, and regime filtering all fail the deflated-Sharpe
gate on BTC/crypto. The infrastructure to *test* edges rigorously now exists; the missing piece is
a genuine signal, which is a research problem, not a coding one. Go-live remains correctly blocked.

## Follow-up: funding-rate carry (a3) — infra built, awaits a real run

Acted on the doc's own recommendation ("a genuine edge needs a *different information source*, e.g.
funding-rate or order-flow signals"). Built the funding path, TDD, invariant-safe (spot long-or-flat,
no leverage, no shorting — funding is only the *signal*):

- `src/data/funding.py` — `fetch_funding_history` (paginate `fetch_funding_rate_history`, drop the
  still-forming interval, causal) + `align_funding` (attach the latest funding known **at or before**
  each candle via `merge_asof` backward — no look-ahead §8.4).
- `src/strategy/funding_carry.py` — `FundingCarry`: go long spot when smoothed funding ≤ threshold
  (contrarian to crowd positioning), else flat. Intent-only, causal, 2 params (threshold, smooth).
- `scripts/funding_edge_search.py` — runs a small variant set through the SAME walk-forward +
  Deflated-Sharpe gate + shared journal (§5), must beat buy-and-hold.

**Status: NOT yet evaluated on real data.** The cloud build env is geo-blocked from Bybit (403
CloudFront, like Binance's 451), so the real fetch+search must run on the **operator's machine**:

    python scripts/funding_edge_search.py --exchange bybit --pair BTC/USDT \
        --perp BTC/USDT:USDT --timeframe 1h --days 730

Offline integration is proven (synthetic funding → align → FundingCarry → walk-forward → DSR runs
end-to-end and correctly rejects random data). Until the real run clears DSR ≥ 0.95 **and** beats
buy-and-hold, go-live stays blocked — same honest bar as every other candidate.
