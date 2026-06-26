"""Tests for src/data/store.py — §2 Parquet store; reproducible & idempotent (§8.8).

Reproducibility is a P0 gate requirement: the stored data must be a stable, deterministic
function of what was written, and re-storing the same range must not change the content.
"""
from __future__ import annotations

import pandas as pd

from src.data import store

HOUR = pd.Timedelta(hours=1)
T0 = pd.Timestamp("2024-01-01T00:00:00Z")


def _frame(n, start=T0, close0=100.0):
    rows = []
    for i in range(n):
        c = close0 + i
        rows.append([start + i * HOUR, c, c + 1, c - 1, c, 10.0 + i])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_round_trip_is_identical(tmp_path):
    df = _frame(5)
    written = store.write_ohlcv(df, tmp_path / "btc.parquet")
    read = store.read_ohlcv(tmp_path / "btc.parquet")
    pd.testing.assert_frame_equal(read, written)


def test_write_sorts_and_dedupes(tmp_path):
    df = _frame(3)
    shuffled = pd.concat([df.iloc[[2]], df.iloc[[0]], df.iloc[[1]], df.iloc[[0]]], ignore_index=True)
    written = store.write_ohlcv(shuffled, tmp_path / "btc.parquet")
    assert written["timestamp"].is_monotonic_increasing
    assert written["timestamp"].is_unique
    assert len(written) == 3


def test_upsert_is_idempotent(tmp_path):
    df = _frame(5)
    path = tmp_path / "btc.parquet"
    store.upsert_ohlcv(df, path)
    store.upsert_ohlcv(df, path)  # same range again
    read = store.read_ohlcv(path)
    pd.testing.assert_frame_equal(read, store.write_ohlcv(df, tmp_path / "ref.parquet"))


def test_upsert_merges_new_candles(tmp_path):
    path = tmp_path / "btc.parquet"
    store.upsert_ohlcv(_frame(3), path)
    store.upsert_ohlcv(_frame(3, start=T0 + 3 * HOUR, close0=200.0), path)
    read = store.read_ohlcv(path)
    assert len(read) == 6
    assert read["timestamp"].is_monotonic_increasing and read["timestamp"].is_unique


def test_upsert_conflict_new_value_wins(tmp_path):
    path = tmp_path / "btc.parquet"
    store.upsert_ohlcv(_frame(3, close0=100.0), path)
    # overlapping timestamps, corrected prices — the newer write must win
    store.upsert_ohlcv(_frame(3, close0=999.0), path)
    read = store.read_ohlcv(path)
    assert len(read) == 3
    assert read["close"].tolist() == [999.0, 1000.0, 1001.0]


def test_read_missing_returns_empty_typed_frame(tmp_path):
    read = store.read_ohlcv(tmp_path / "nope.parquet")
    assert read.empty
    assert list(read.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
