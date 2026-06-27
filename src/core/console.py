"""src/core/console.py — make stdout/stderr UTF-8 safe for CLI entrypoints.

The project's docstrings and CLI output use non-ASCII characters (§, —, →). On Windows the
console codepage is often a legacy charmap (e.g. cp1258 on a Vietnamese locale), so a bare
``print("→")`` raises ``UnicodeEncodeError`` and crashes the tool. Entrypoints call
:func:`force_utf8_stdio` first so output degrades gracefully instead of crashing.

This only touches the *display* streams; it never affects the event log or stored data
(those are written as UTF-8 explicitly elsewhere).
"""
from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 with replacement, where supported.

    Idempotent and safe to call on any platform. If a stream cannot be reconfigured
    (already detached, or not a real text stream), it is left untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Stream detached or doesn't support reconfiguration — leave it as-is.
            pass
