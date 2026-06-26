"""src/risk/exchange_assert.py — §4 exchange-config safety assertions.

MONEY CODE (TDD mandatory). The in-bot risk engine disappears if the VPS restarts, Docker dies,
or ccxt misbehaves — so on startup and every reconnect the bot asserts the exchange-side account
state matches intent: spot mode, leverage = 1, margin disabled, futures disabled, reduceOnly on
exits. Any mismatch (or a field the exchange won't confirm) → **STOP and alert, do not trade**.
This is the exchange-level complement to the in-bot limits.
"""
from __future__ import annotations

from typing import Any

from src.risk.config import RiskConfig

_SENTINEL = object()


class ExchangeStateError(RuntimeError):
    """Raised when the exchange-side state does not match intent — a hard STOP."""

    def __init__(self, mismatches: list[str]):
        self.mismatches = tuple(mismatches)
        super().__init__("exchange state mismatch (do not trade): " + ", ".join(mismatches))


def exchange_mismatches(actual: dict[str, Any], cfg: RiskConfig) -> list[str]:
    """Return the list of asserted fields whose actual value ≠ intended (empty = all good).

    A field missing from ``actual`` is a mismatch: if we cannot confirm it, we must STOP.
    """
    mismatches: list[str] = []
    for key, expected in cfg.exchange_assertions.items():
        got = actual.get(key, _SENTINEL)
        if got is _SENTINEL or got != expected:
            mismatches.append(f"exchange_state:{key}")
    return mismatches


def assert_exchange_state(actual: dict[str, Any], cfg: RiskConfig) -> None:
    """Raise ExchangeStateError if the exchange state does not match intent (startup/reconnect)."""
    mismatches = exchange_mismatches(actual, cfg)
    if mismatches:
        raise ExchangeStateError(mismatches)
