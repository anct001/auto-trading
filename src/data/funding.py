"""src/data/funding.py — §8 perpetual **funding-rate** history as a research signal source.

Funding rate is a *different information source* than price/TA (which showed no edge — see
docs/research/strategy_search_findings.md): it reflects perp-market positioning (longs pay shorts
when positive, and vice versa). We use it ONLY as a signal for a **spot long-or-flat** strategy —
never to trade the perp, never with leverage (Inv 4, spot-only before P5). Per ADR 0002 the funding
series is *viewing/research* data (a perp venue like Bybit); it feeds research + forward-validated
backtests, and any resulting strategy is still validated with the deflated-Sharpe discipline (§5).

Two pieces, both causal (§8.4):
  - :func:`fetch_funding_history` — paginate ``fetch_funding_rate_history`` (mirrors feed.py), drop
    the still-forming interval, de-dupe, ascending, UTC tz-aware. Exchange injected → testable.
  - :func:`align_funding` — attach the *latest funding known at or before each candle close* to an
    OHLCV frame (``merge_asof`` backward). No look-ahead: a candle only ever sees past funding.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

import pandas as pd


class SupportsFetchFunding(Protocol):
    def fetch_funding_rate_history(
        self, symbol: str, since: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def to_funding_frame(rows: list[dict[str, Any]], *, now_ms: int) -> pd.DataFrame:
    """Normalize raw ccxt funding rows into a typed, ascending, closed-only frame.

    Columns: ``timestamp`` (UTC tz-aware) + ``funding_rate`` (float). Rows with a timestamp at or
    after ``now_ms`` (the still-forming / predicted interval) are dropped — no look-ahead."""
    clean: dict[int, float] = {}
    for r in rows:
        ts = r.get("timestamp")
        rate = r.get("fundingRate")
        if ts is None or rate is None:
            continue
        ts = int(ts)
        if ts >= now_ms:  # still-forming / predicted funding — not yet settled
            continue
        clean[ts] = float(rate)  # last write wins on duplicate timestamps
    if not clean:
        return pd.DataFrame({"timestamp": pd.Series([], dtype="datetime64[ns, UTC]"),
                             "funding_rate": pd.Series([], dtype="float64")})
    ordered = sorted(clean)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ordered, unit="ms", utc=True),
        "funding_rate": [clean[t] for t in ordered],
    })


def fetch_funding_history(
    exchange: SupportsFetchFunding,
    symbol: str,
    *,
    since_ms: int,
    until_ms: int | None = None,
    page_limit: int = 200,
    now_ms: int | None = None,
    max_pages: int = 10_000,
) -> pd.DataFrame:
    """Page through funding history from ``since_ms`` (mirrors feed.fetch_ohlcv_history).

    De-dupes by timestamp; a page that makes no forward progress stops the loop. Returns the typed,
    closed-only frame from :func:`to_funding_frame`."""
    cutoff = _now_ms() if now_ms is None else int(now_ms)
    end = cutoff if until_ms is None else min(int(until_ms), cutoff)

    collected: dict[int, dict[str, Any]] = {}
    cursor = int(since_ms)
    last_seen: int | None = None
    for _ in range(max_pages):
        if cursor >= end:
            break
        page = exchange.fetch_funding_rate_history(symbol, since=cursor, limit=page_limit)
        if not page:
            break
        stamped = [r for r in page if r.get("timestamp") is not None]
        for r in stamped:
            collected[int(r["timestamp"])] = r
        if not stamped:
            break
        page_last = max(int(r["timestamp"]) for r in stamped)
        if last_seen is not None and page_last <= last_seen:
            break  # no forward progress (exchange ignored `since`) → stop
        last_seen = page_last
        cursor = page_last + 1

    return to_funding_frame(list(collected.values()), now_ms=cutoff)


def align_funding(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Attach a ``funding`` column: the latest funding_rate known at or before each candle.

    Uses ``merge_asof`` backward on ``timestamp`` — a candle only ever sees funding stamped at or
    before its own timestamp (causal, no look-ahead §8.4). Candles before the first funding print
    get NaN (the strategy treats NaN as 'no long signal')."""
    if "timestamp" not in ohlcv.columns:
        raise KeyError("ohlcv frame must have a 'timestamp' column to align funding")
    left = ohlcv.sort_values("timestamp").reset_index(drop=True)
    if funding is None or len(funding) == 0:
        out = left.copy()
        out["funding"] = pd.Series([float("nan")] * len(out), dtype="float64")
        return out
    right = funding.sort_values("timestamp").reset_index(drop=True)[["timestamp", "funding_rate"]]
    merged = pd.merge_asof(left, right, on="timestamp", direction="backward")
    merged = merged.rename(columns={"funding_rate": "funding"})
    return merged
