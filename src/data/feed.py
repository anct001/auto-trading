"""src/data/feed.py — §2 ccxt OHLCV fetch (closed candles only).

Fetches OHLCV via ccxt and returns ONLY fully-closed candles. The still-forming candle is
dropped: acting on it would be look-ahead (§8.4). This module does the fetch + transform; the
data-quality gate (§8.1, src/data/quality.py) is a separate downstream step.

Trading-venue data only (Binance Japan per ADR 0002). Sandbox/testnet by default (§10) —
real keys and live endpoints are a P4 concern.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Protocol

import pandas as pd

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]

# Supported timeframe units → milliseconds. Covers ccxt's common spot timeframes.
_UNIT_MS = {
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}
_TIMEFRAME_RE = re.compile(r"^(\d+)([smhdw])$")


class SupportsFetchOHLCV(Protocol):
    """The slice of the ccxt exchange API this module needs (keeps it mockable)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = ..., since: int | None = ..., limit: int | None = ...
    ) -> list[list[float]]: ...


def timeframe_to_ms(timeframe: str) -> int:
    """Parse a ccxt-style timeframe (e.g. '1h', '15m', '1d') into milliseconds.

    Raises ValueError on anything unsupported or non-positive — we never guess a duration,
    because the duration is what decides whether a candle has closed.
    """
    match = _TIMEFRAME_RE.match(timeframe.strip()) if isinstance(timeframe, str) else None
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    qty = int(match.group(1))
    if qty <= 0:
        raise ValueError(f"timeframe quantity must be positive: {timeframe!r}")
    return qty * _UNIT_MS[match.group(2)]


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def to_closed_frame(
    raw: list[list[Any]], timeframe: str, *, now_ms: int | None = None
) -> pd.DataFrame:
    """Transform raw ccxt OHLCV rows into a typed, closed-candles-only DataFrame.

    - drops the still-forming candle (open_ts + timeframe > now) — no look-ahead (§8.4)
    - sorts ascending by timestamp
    - timestamp is tz-aware UTC; OHLCV columns are numeric

    Pass ``now_ms`` for deterministic behavior in tests; defaults to the current UTC time.
    """
    duration_ms = timeframe_to_ms(timeframe)
    cutoff = _now_ms() if now_ms is None else int(now_ms)

    df = pd.DataFrame(list(raw), columns=OHLCV_COLUMNS)
    if not df.empty:
        ts = pd.to_numeric(df["timestamp"])
        # a candle is closed once now has reached its close time (open_ts + duration)
        df = df[ts + duration_ms <= cutoff].copy()
        df = df.sort_values("timestamp").reset_index(drop=True)

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in _PRICE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[OHLCV_COLUMNS]


def fetch_closed_ohlcv(
    exchange: SupportsFetchOHLCV,
    symbol: str,
    timeframe: str,
    limit: int = 500,
    *,
    now_ms: int | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV from ``exchange`` and return only fully-closed candles.

    ``exchange`` is any object exposing ccxt's ``fetch_ohlcv`` — injected so the network is
    mockable in tests. The exchange is expected to already be configured (sandbox, endpoint).
    """
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=None, limit=limit)
    return to_closed_frame(raw, timeframe, now_ms=now_ms)
