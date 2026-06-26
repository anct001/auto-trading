"""Tests for src/data/quality.py — §8.1 data-quality gate (the P0 spine).

One test per defect class. The contract: a quarantined candle is separated out with a reason
and never appears in the clean frame, so it can never reach a signal or trade (§4 breakers).
Tests are written before the implementation (TDD).
"""
from __future__ import annotations

import pandas as pd

from src.data import quality

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _clean_frame(n=20, close0=100.0):
    """Well-formed candles: close walks +1/bar, tight ±0.5 range, constant volume."""
    rows = []
    for i in range(n):
        c = close0 + i
        rows.append([T0 + i * HOUR, c, c + 0.5, c - 0.5, c, 10.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_clean_frame_passes_untouched():
    df = _clean_frame()
    res = quality.check_quality(df)
    assert len(res.clean) == len(df)
    assert res.quarantined.empty
    assert "reason" not in res.clean.columns


def test_empty_frame():
    res = quality.check_quality(_clean_frame(0))
    assert res.clean.empty and res.quarantined.empty


def test_duplicate_timestamp_quarantined():
    df = _clean_frame(4)
    df.loc[2, "timestamp"] = df.loc[1, "timestamp"]  # row 2 duplicates row 1's ts
    res = quality.check_quality(df)
    assert len(res.quarantined) == 1
    assert res.quarantined["reason"].iloc[0] == "duplicate_timestamp"
    assert len(res.clean) == 3


def test_out_of_order_timestamp_quarantined():
    df = _clean_frame(5)
    df.loc[3, "timestamp"] = T0 + 1 * HOUR + pd.Timedelta(minutes=30)  # goes backwards vs row 2
    res = quality.check_quality(df)
    assert (res.quarantined["reason"] == "out_of_order").any()
    assert len(res.clean) == 4


def test_non_positive_price_quarantined():
    df = _clean_frame(5)
    df.loc[2, "close"] = 0.0
    df.loc[4, "low"] = -1.0
    res = quality.check_quality(df)
    assert set(res.quarantined["reason"]) == {"non_positive_price"}
    assert len(res.quarantined) == 2


def test_ohlc_inconsistent_quarantined():
    df = _clean_frame(5)
    df.loc[2, "high"] = df.loc[2, "low"] - 1.0  # high below low: impossible
    res = quality.check_quality(df)
    assert (res.quarantined["reason"] == "ohlc_inconsistent").any()
    assert len(res.clean) == 4


def test_negative_volume_quarantined():
    df = _clean_frame(5)
    df.loc[3, "volume"] = -5.0
    res = quality.check_quality(df)
    assert (res.quarantined["reason"] == "volume_anomaly").any()
    assert len(res.clean) == 4


def test_price_spike_quarantined():
    df = _clean_frame(20)
    df.loc[18, "high"] = df.loc[18, "close"] + 200.0  # single-candle blowout vs ~1.5 ATR
    res = quality.check_quality(df, spike_atr_mult=10.0)
    assert (res.quarantined["reason"] == "spike").any()
    assert len(res.clean) == 19


def test_quarantined_rows_carry_reason_and_are_excluded_from_clean():
    df = _clean_frame(6)
    df.loc[2, "close"] = -1.0
    res = quality.check_quality(df)
    assert "reason" in res.quarantined.columns
    assert len(res.clean) + len(res.quarantined) == len(df)
    # the original timestamps in clean and quarantined are disjoint
    assert set(res.clean["timestamp"]).isdisjoint(set(res.quarantined["timestamp"]))
