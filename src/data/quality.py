"""src/data/quality.py — §8.1 data-quality gate (the P0 spine).

Inspects an OHLCV frame and separates **clean** candles from **quarantined** ones. A quarantined
candle is never used to compute a signal or place a trade — this is the same principle as the
§4 circuit breakers ("never trade on bad/stale data"), applied per-candle in both backtest and
live (one path, §15).

Defect classes (each row gets the first reason that matches, in this priority order):

  1. ``duplicate_timestamp`` — a timestamp seen earlier in the frame (keep the first occurrence).
  2. ``out_of_order``        — a timestamp earlier than the previous row's.
  3. ``non_positive_price``  — any of O/H/L/C <= 0.
  4. ``ohlc_inconsistent``   — high < low, or high below open/close, or low above open/close.
  5. ``volume_anomaly``      — negative volume.
  6. ``spike``               — true range > ``spike_atr_mult`` × the prior rolling ATR
                               (an implausible single-candle blowout).

The gate is pure/deterministic: same input → same partition (supports §8.8 reproducibility).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.data.feed import OHLCV_COLUMNS

# Priority order: structural timestamp issues first, then price validity, then the statistical
# spike check (which depends on a clean-enough history to mean anything).
_REASON_ORDER = [
    "duplicate_timestamp",
    "out_of_order",
    "non_positive_price",
    "ohlc_inconsistent",
    "volume_anomaly",
    "spike",
]


@dataclass(frozen=True)
class QualityResult:
    """Partition of an OHLCV frame. ``clean`` has the original columns; ``quarantined`` adds a
    ``reason`` column naming the defect."""

    clean: pd.DataFrame
    quarantined: pd.DataFrame


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    hl = df["high"] - df["low"]
    hc = (df["high"] - prev_close).abs()
    lc = (df["low"] - prev_close).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


def check_quality(
    df: pd.DataFrame, *, atr_period: int = 14, spike_atr_mult: float = 10.0
) -> QualityResult:
    """Partition ``df`` into clean and quarantined candles. See module docstring for rules."""
    work = df.reset_index(drop=True)
    n = len(work)
    reason = pd.Series([""] * n, index=work.index, dtype="object")

    if n == 0:
        empty = work.assign(reason=pd.Series(dtype="object"))
        return QualityResult(clean=work[OHLCV_COLUMNS], quarantined=empty)

    def mark(mask: pd.Series, label: str) -> None:
        # first reason wins: only set rows not already flagged
        reason.loc[mask & (reason == "")] = label

    ts = pd.to_datetime(work["timestamp"], utc=True)
    mark(ts.duplicated(keep="first"), "duplicate_timestamp")
    mark(ts.diff() < pd.Timedelta(0), "out_of_order")

    o, h, low, c = work["open"], work["high"], work["low"], work["close"]
    mark((o <= 0) | (h <= 0) | (low <= 0) | (c <= 0), "non_positive_price")
    mark(
        (h < low)
        | (h < o)
        | (h < c)
        | (low > o)
        | (low > c),
        "ohlc_inconsistent",
    )
    mark(work["volume"] < 0, "volume_anomaly")

    # Spike: true range vs the PRIOR rolling ATR (shifted, so the spike candle does not inflate
    # its own threshold). Only meaningful once enough history exists and ATR > 0.
    tr = _true_range(work)
    atr_prev = tr.rolling(atr_period).mean().shift(1)
    spike = (atr_prev > 0) & (tr > spike_atr_mult * atr_prev)
    mark(spike.fillna(False), "spike")

    is_bad = reason != ""
    clean = work.loc[~is_bad, OHLCV_COLUMNS].reset_index(drop=True)
    quarantined = work.loc[is_bad, OHLCV_COLUMNS].copy()
    quarantined["reason"] = reason.loc[is_bad].values
    quarantined = quarantined.reset_index(drop=True)
    return QualityResult(clean=clean, quarantined=quarantined)
