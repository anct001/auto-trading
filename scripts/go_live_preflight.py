#!/usr/bin/env python3
"""§14 go-live pre-flight CLI — readiness report + human sign-off registry. Never trades.

    python scripts/go_live_preflight.py                      # print the report
    python scripts/go_live_preflight.py --list-slugs         # sign-off vocabulary
    python scripts/go_live_preflight.py --sign forward-dry-run --operator "A. Nguyen"

Exits 0 only if EVERY auto-check passes and EVERY operator item is signed (it won't, until the
operator has genuinely done the operational gates). Signing records who/when in ops/signoff.json;
it never performs the gated work itself (Inv 4/5).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.core.console import force_utf8_stdio
from src.core.preflight import is_ready, manual_slugs, record_signoff, run_preflight

_MARK = {"PASS": "[x]", "FAIL": "[!]", "WARN": "[~]", "MANUAL": "[ ]", "SIGNED": "[x]"}


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list-slugs", action="store_true",
                    help="print the sign-off slugs and exit")
    ap.add_argument("--sign", metavar="SLUG",
                    help="record the operator's sign-off for one §14 item")
    ap.add_argument("--operator", default="",
                    help="human name recorded with --sign (required)")
    ap.add_argument("--date", default="", help="override the recorded date (default: today)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]

    if args.list_slugs:
        for s in manual_slugs():
            print(s)
        return 0

    if args.sign:
        try:
            record_signoff(root, args.sign, args.operator, date=args.date)
        except ValueError as e:
            print(f"refused: {e}")
            return 2
        print(f"signed: {args.sign} by {args.operator.strip()}"
              " — recorded in ops/signoff.json (this records, it does not verify)")
        print()

    checks = run_preflight(root)
    print("== §14 GO-LIVE PRE-FLIGHT (no orders placed; no money moved) ==\n")
    for c in checks:
        line = f" {_MARK.get(c.status, '[?]')} {c.status:6} {c.item}"
        print(line + (f"  — {c.detail}" if c.detail else ""))
    auto = [c for c in checks if c.status in ("PASS", "FAIL", "WARN")]
    npass = sum(1 for c in auto if c.status == "PASS")
    manual = sum(1 for c in checks if c.status == "MANUAL")
    signed = sum(1 for c in checks if c.status == "SIGNED")
    print(f"\nauto-checks: {npass}/{len(auto)} pass · operator items signed: "
          f"{signed}/{signed + manual}")
    ready = is_ready(checks)
    print("\nVERDICT: " + ("READY for §14 sign-off" if ready
                           else "*** NOT READY — DO NOT DEPLOY REAL CAPITAL ***"))
    print("Real capital is touched only after every box is checked, by the human operator "
          "(Inv 4/5).")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
