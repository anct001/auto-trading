"""src/data/store.py — §2 Parquet OHLCV store; reproducible & idempotent (§8.8).

Persists OHLCV with a pinned schema and deterministic ordering, so the stored data is a stable
function of what was written. ``upsert_ohlcv`` merges new candles into an existing file and is
idempotent: re-storing the same range does not change the content, and on a timestamp conflict
the newer write wins (e.g. a corrected candle replaces a provisional one).

Content stability (round-trip + idempotent upsert) is the guarantee that matters for §8.8
reproducibility. Parquet file *bytes* are not asserted stable — embedded writer metadata can
vary across library versions; the data is what must be deterministic.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.feed import OHLCV_COLUMNS, _PRICE_COLUMNS


# Microsecond precision is the canonical resolution: it round-trips exactly through Parquet
# (Arrow's default timestamp unit) and preserves ms-origin OHLCV losslessly, so an in-memory
# frame and a stored-then-read frame compare equal (§8.8 reproducibility).
_TS_DTYPE = "datetime64[us, UTC]"


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in _PRICE_COLUMNS})
    df.insert(0, "timestamp", pd.Series(dtype=_TS_DTYPE))
    return df[OHLCV_COLUMNS]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical form: required columns, UTC us-timestamps, sorted, deduped (last wins)."""
    if df.empty:
        return _empty_frame()
    out = df[OHLCV_COLUMNS].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).astype(_TS_DTYPE)
    for col in _PRICE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = (
        out.sort_values("timestamp", kind="stable")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )
    return out


def write_ohlcv(df: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Write OHLCV to Parquet in canonical form. Returns the canonical frame as stored."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = _normalize(df)
    out.to_parquet(path, index=False, engine="pyarrow")
    return out


def read_ohlcv(path: str | Path) -> pd.DataFrame:
    """Read OHLCV from Parquet (canonical form). Missing file → empty typed frame."""
    path = Path(path)
    if not path.exists():
        return _empty_frame()
    return _normalize(pd.read_parquet(path, engine="pyarrow"))


def upsert_ohlcv(df_new: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Merge ``df_new`` into the file at ``path`` (creating it if absent).

    Idempotent: re-upserting the same range leaves content unchanged. On a timestamp conflict
    the row from ``df_new`` wins (placed last before dedup-keep-last).
    """
    existing = read_ohlcv(path)
    combined = pd.concat([existing, _normalize(df_new)], ignore_index=True)
    return write_ohlcv(combined, path)
