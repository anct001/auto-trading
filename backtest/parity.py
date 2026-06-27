"""backtest/parity.py — §8.9 dry-run vs backtest SIGNAL parity check.

The P0 DONE-GATE's last item is "forward dry-run running; **signal** metrics ≈ backtest (fills
NOT validated — §2)". Both paths run the *same* deterministic strategy on closed candles, so the
strong, checkable form of "≈" is: on every candle timestamp they share, the strategy **intent**
must be identical. Fills/P&L legitimately differ (paper vs modeled), but a signal divergence
means the live path and the backtested path are not the same logic — a P0 blocker.

This module is pure/offline: it parses a dry-run event log into a per-candle signal series,
computes the backtest's signal series over the same data, and diffs them. The ≥N-day *duration*
of the dry-run is operational; this is the tool that turns that log into a pass/fail.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.events.log import MARKET_RECEIVED, SIGNAL_GENERATED, Event, EventLog

# A signal series is candle-close timestamp → strategy intent.
SignalMap = dict[pd.Timestamp, str]


@dataclass(frozen=True)
class ParityReport:
    overlap: int                                   # candle timestamps present in both series
    matches: int                                   # of the overlap, how many intents agree
    mismatches: list[tuple[pd.Timestamp, str, str]]  # (ts, dryrun_intent, backtest_intent)
    dryrun_only: int                               # candles the dry-run saw but the backtest didn't
    backtest_only: int                             # candles in the backtest the dry-run didn't reach

    @property
    def match_rate(self) -> float:
        return self.matches / self.overlap if self.overlap else 0.0

    @property
    def is_parity(self) -> bool:
        """Parity requires a non-empty overlap with zero mismatches — silence proves nothing."""
        return self.overlap > 0 and not self.mismatches

    def summary(self) -> str:
        head = (f"signal parity: overlap={self.overlap} matches={self.matches} "
                f"rate={self.match_rate:.4f} dryrun_only={self.dryrun_only} "
                f"backtest_only={self.backtest_only} -> {'PASS' if self.is_parity else 'FAIL'}")
        lines = [head]
        for ts, dr, bt in self.mismatches[:20]:
            lines.append(f"  MISMATCH {ts}: dry-run={dr!r} backtest={bt!r}")
        if len(self.mismatches) > 20:
            lines.append(f"  ... and {len(self.mismatches) - 20} more")
        return "\n".join(lines)


def dryrun_signals(events: list[Event], pair: str) -> SignalMap:
    """Reconstruct {candle_ts → intent} for ``pair`` from a dry-run event stream.

    Each tick logs MarketReceived (carrying the candle's close timestamp) immediately followed by
    SignalGenerated (the intent). We pair each SignalGenerated with the most recent MarketReceived
    of the same pair. Re-evaluations of the same candle overwrite — deterministic, so identical.
    """
    last_ts: dict[str, pd.Timestamp] = {}
    out: SignalMap = {}
    for ev in events:
        p = ev.payload.get("pair")
        if p != pair:
            continue
        if ev.type == MARKET_RECEIVED:
            last_ts[p] = pd.Timestamp(ev.payload["timestamp"])
        elif ev.type == SIGNAL_GENERATED and p in last_ts:
            out[last_ts[p]] = str(ev.payload["intent"])
    return out


def dryrun_signals_from_path(path: str | Path, pair: str) -> SignalMap:
    """Convenience: load an on-disk JSONL event log and extract the signal series."""
    return dryrun_signals(EventLog(path).read_all(), pair)


def backtest_signals(df: pd.DataFrame, strategy) -> SignalMap:
    """Compute the backtest's per-candle intent series, indexed by candle-close timestamp."""
    intents = strategy.generate_signals(df)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    return {pd.Timestamp(t): str(i) for t, i in zip(ts, intents)}


def compare_signals(dryrun: SignalMap, backtest: SignalMap) -> ParityReport:
    """Diff two signal series on their shared candle timestamps (the §8.9 check)."""
    shared = dryrun.keys() & backtest.keys()
    matches = 0
    mismatches: list[tuple[pd.Timestamp, str, str]] = []
    for ts in sorted(shared):
        dr, bt = dryrun[ts], backtest[ts]
        if dr == bt:
            matches += 1
        else:
            mismatches.append((ts, dr, bt))
    return ParityReport(
        overlap=len(shared),
        matches=matches,
        mismatches=mismatches,
        dryrun_only=len(dryrun.keys() - backtest.keys()),
        backtest_only=len(backtest.keys() - dryrun.keys()),
    )
