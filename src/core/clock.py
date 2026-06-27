"""src/core/clock.py — §9 NTP-synced clock; reject on skew.

A skewed local clock silently corrupts candle-close alignment, order timestamps, and TIF
expiry. So clock skew is a circuit breaker (§4): if local time drifts from a trusted reference
beyond a threshold, raise — pause and alert, never trade on a skewed clock.

The reference time source is injected (a callable returning reference epoch-ms, e.g. from NTP or
the exchange's server time) so the check is testable and has no hidden network dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable


class ClockSkewError(RuntimeError):
    """Raised when local time drifts from the reference beyond the allowed skew."""


def now_ms() -> int:
    """Local wall-clock time in UTC epoch milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def check_skew(*, local_ms: int, reference_ms: int, max_skew_ms: int) -> int:
    """Return local−reference skew (ms) if within tolerance, else raise ClockSkewError."""
    skew = local_ms - reference_ms
    if abs(skew) > max_skew_ms:
        raise ClockSkewError(
            f"clock skew {skew}ms exceeds ±{max_skew_ms}ms — pause and alert, do not trade"
        )
    return skew


def assert_clock_sane(
    *,
    reference_now_ms: Callable[[], int],
    local_now_ms: Callable[[], int] = now_ms,
    max_skew_ms: int = 1000,
) -> int:
    """Sample local + reference time and assert they agree within ``max_skew_ms``."""
    return check_skew(
        local_ms=local_now_ms(), reference_ms=reference_now_ms(), max_skew_ms=max_skew_ms
    )
