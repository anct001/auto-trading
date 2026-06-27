"""Tests for src/core/console.py — UTF-8-safe stdio for CLI entrypoints.

Regression guard: on a legacy Windows codepage (e.g. cp1258) a bare ``print("→")`` raised
UnicodeEncodeError and crashed the dry-run / backtest tools. force_utf8_stdio() makes the
display streams encode non-ASCII without crashing, and is a safe no-op on odd streams.
"""
from __future__ import annotations

import io
import sys

from src.core.console import force_utf8_stdio


def test_force_utf8_stdio_makes_non_ascii_printable(monkeypatch):
    # Simulate a legacy-codepage console: a text stream that cannot encode "→".
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1258", errors="strict")
    monkeypatch.setattr(sys, "stdout", legacy)

    force_utf8_stdio()
    print("step → done §8")  # would raise UnicodeEncodeError on cp1258 before reconfigure
    sys.stdout.flush()

    assert "→".encode() in raw.getvalue()


def test_force_utf8_stdio_is_noop_on_streams_without_reconfigure(monkeypatch):
    # A plain object with no reconfigure() must be tolerated, not crash.
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    force_utf8_stdio()  # no exception
