#!/usr/bin/env python3
"""§14 go-live pre-flight CLI — prints the readiness report. Places no orders, moves no money.

    python scripts/go_live_preflight.py

Exits 0 only if EVERY check passes (it won't, until the operator has done the operational gates
and recorded sign-off). This is the honest "are we allowed to risk real capital yet?" gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.core.console import force_utf8_stdio
from src.core.preflight import is_ready, run_preflight

_MARK = {"PASS": "[x]", "FAIL": "[!]", "WARN": "[~]", "MANUAL": "[ ]"}


def main() -> int:
    force_utf8_stdio()
    root = Path(__file__).resolve().parents[1]
    checks = run_preflight(root)
    print("== §14 GO-LIVE PRE-FLIGHT (no orders placed; no money moved) ==\n")
    for c in checks:
        line = f" {_MARK.get(c.status, '[?]')} {c.status:6} {c.item}"
        print(line + (f"  — {c.detail}" if c.detail else ""))
    auto = [c for c in checks if c.status in ("PASS", "FAIL", "WARN")]
    npass = sum(1 for c in auto if c.status == "PASS")
    manual = sum(1 for c in checks if c.status == "MANUAL")
    print(f"\nauto-checks: {npass}/{len(auto)} pass · operator/manual items pending: {manual}")
    ready = is_ready(checks)
    print("\nVERDICT: " + ("READY for §14 sign-off" if ready
                           else "*** NOT READY — DO NOT DEPLOY REAL CAPITAL ***"))
    print("Real capital is touched only after every box is checked, by the human operator "
          "(Inv 4/5).")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
