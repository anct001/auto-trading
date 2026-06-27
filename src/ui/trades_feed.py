"""src/ui/trades_feed.py — public market trade-tape shaper (§12, read-only).

Turns a ccxt-style recent-trades list into a compact tape for the operator terminal (newest
first): time, price, amount, side. Pure/deterministic; the tape is market color for the human —
never a signal path (the bot trades on closed candles, §12).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _hms(ts_ms) -> str:
    if ts_ms is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%H:%M:%S")
    except (ValueError, OSError, TypeError):
        return ""


def format_trades(raw: list[dict], *, limit: int = 30) -> list[dict]:
    """Newest-first, capped tape rows from ccxt trades (each: timestamp/price/amount/side)."""
    rows = []
    for t in reversed(raw[-limit:]):
        rows.append({
            "time": _hms(t.get("timestamp")),
            "price": float(t.get("price", 0.0) or 0.0),
            "amount": float(t.get("amount", 0.0) or 0.0),
            "side": str(t.get("side") or ""),
        })
    return rows
