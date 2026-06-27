"""src/ui/orderbook.py — §12 order-book depth view (operator-facing, read-only).

Turns a ccxt-style order book (`{"bids": [[price, amount], ...], "asks": [...]}`) into a depth
payload for the coin-detail page: top-N levels with cumulative depth, plus best bid/ask, mid, and
spread. Pure and deterministic; never a signal path (§12) — the bot trades on closed candles, not
the book. (Book depth also informs the §5 capacity check, but that is a separate, deliberate step.)
"""
from __future__ import annotations


def _rows(levels: list, n: int) -> list[dict]:
    out, cum = [], 0.0
    for entry in levels[:n]:
        price, amount = float(entry[0]), float(entry[1])
        cum += amount
        out.append({"price": price, "amount": amount, "cum": cum})
    return out


def build_orderbook_view(book: dict, *, levels: int = 15) -> dict:
    """Top-``levels`` bids/asks with cumulative depth + best bid/ask, mid, and spread."""
    bids = _rows(book.get("bids") or [], levels)
    asks = _rows(book.get("asks") or [], levels)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    mid = (best_bid + best_ask) / 2.0 if (best_bid is not None and best_ask is not None) else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    spread_pct = (spread / mid * 100.0) if (spread is not None and mid) else None
    return {
        "bids": bids, "asks": asks,
        "best_bid": best_bid, "best_ask": best_ask,
        "mid": mid, "spread": spread, "spread_pct": spread_pct,
    }
