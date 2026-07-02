"""src/core/preflight.py — §14 go-live pre-flight (readiness report; NEVER trades).

A structured check of the §14 go-live checklist before *any* real capital (P4). It auto-verifies
the items that can be checked in-process (config integrity, leverage off, sentiment feature off,
kill-switch works, secrets hygiene) and lists the operational/human gates that only the operator
can confirm (≥N-day dry-run, risk drills fired, KYC/ToS/tax, human sign-off, …).

It is a **gate, not an actuator**: it places no orders and moves no money. ``is_ready`` returns
True only if every auto-check PASSes *and* there are no outstanding MANUAL/operational items — so
it stays False until the operator has genuinely done the gated work and recorded sign-off
(Invariant 4/5). The point is to make "are we allowed to risk real money yet?" answerable and
honest, not to enable speed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PASS, FAIL, WARN, MANUAL, SIGNED = "PASS", "FAIL", "WARN", "MANUAL", "SIGNED"


@dataclass(frozen=True)
class Check:
    item: str
    status: str
    detail: str = ""
    slug: str = ""  # stable id for operator sign-off; auto-checks have none


def _auto_checks(root: Path) -> list[Check]:
    out: list[Check] = []

    # config integrity (§15) — locked config matches the committed files
    try:
        from src.core import config as cfgmod
        lock = cfgmod.load_manifest(root / "config" / "config.lock.json")
        cfgmod.verify_configs([root / rel for rel in lock],
                              {str(root / rel): h for rel, h in lock.items()})
        out.append(Check("Config integrity (hash-lock §15)", PASS))
    except Exception as e:
        out.append(Check("Config integrity (hash-lock §15)", FAIL, str(e)[:80]))

    # risk config invariants
    try:
        from src.risk.config import RiskConfig
        rc = RiskConfig.load(root / "config" / "risk" / "default.json")
        out.append(Check("Leverage off pre-P5 (Inv 4)", PASS if rc.leverage == 0 else FAIL,
                         f"leverage={rc.leverage}"))
        out.append(Check("LLM sentiment haircut disabled until validated (§6)",
                         PASS if rc.sentiment_floor == 1.0 else WARN,
                         f"sentiment_floor={rc.sentiment_floor}"))
    except Exception as e:
        out.append(Check("Risk config loads", FAIL, str(e)[:80]))

    # kill-switch is functional and arms->halts
    try:
        from src.risk.killswitch import KillSwitch
        ks = KillSwitch()
        ok = ks.allows_trading()
        ks.manual_kill()
        out.append(Check("Kill-switch arms & halts (Inv 5)",
                         PASS if (ok and ks.is_halted) else FAIL))
    except Exception as e:
        out.append(Check("Kill-switch works", FAIL, str(e)[:80]))

    # secrets hygiene — the committed template must not carry a real key (Inv 6)
    env_example = (root / ".env.example")
    leak = False
    if env_example.exists():
        for line in env_example.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(("TRADING_EXCHANGE_API_KEY=", "TRADING_EXCHANGE_API_SECRET=")):
                if line.split("=", 1)[1].strip():
                    leak = True
    out.append(Check("No secret in tracked .env.example (Inv 6)", FAIL if leak else PASS))

    return out


# §14 items that no script can confirm — only the operator can, with evidence + sign-off.
# The slug is the stable id recorded in ops/signoff.json; the text may be reworded freely.
_MANUAL_ITEMS: list[tuple[str, str]] = [
    ("gates-reproven", "P0-P3 gates all passed & re-proven from clean"),
    ("forward-dry-run", "Forward dry-run >= N_days (30), no crash, signal metrics ~ backtest"),
    ("validated-edge", "A validated strategy with net-of-cost+tax edge (current EMA-cross has NONE)"),
    ("risk-drills", "Risk drills each fired: per-trade/correlation/daily soft+hard/drawdown kill/breakers"),
    ("naked-stop", "Exchange-side protective stop attaches at fill & survives killed process (naked test)"),
    ("ui-write-guardrails", "Manual over-limit order rejected; UI read-only except kill-switch + risk-gated order"),
    ("exchange-keys", "Exchange entity/endpoint confirmed; testnet read-balance + small order; key trade-only/"
                      "withdrawals-off/IP-whitelisted"),
    ("restart-safety", "Restart-safety: kill mid-trade -> clean recovery, no double-trade"),
    ("capacity-feasibility", "Capacity + order-feasibility (minNotional/lot/tick at 0.5% size) verified"),
    ("depeg-deadman", "De-peg guard live-tested; human-heartbeat dead-man + demotion-to-paper configured"),
    ("jurisdiction-tax", "Capital-on-exchange cap set; after-tax edge modeled; jurisdiction/ToS/tax confirmed (§11)"),
    ("minimal-capital", "Capital is smallest meaningful amount; spot only; no leverage"),
    ("human-signoff", "HUMAN SIGN-OFF recorded"),
]

_SIGNOFF_REL = Path("ops") / "signoff.json"


def manual_slugs() -> list[str]:
    """Stable ids of the operator-only §14 items (the sign-off vocabulary)."""
    return [slug for slug, _ in _MANUAL_ITEMS]


def _load_signoffs(root: Path) -> dict[str, dict]:
    """Read ops/signoff.json; anything missing/corrupt/malformed → no sign-offs (fail-closed)."""
    try:
        data = json.loads((root / _SIGNOFF_REL).read_text(encoding="utf-8"))
        raw = data.get("signoffs") or {}
        valid = manual_slugs()
        return {slug: entry for slug, entry in raw.items()
                if slug in valid and isinstance(entry, dict)
                and str(entry.get("operator", "")).strip() and str(entry.get("date", "")).strip()}
    except Exception:  # noqa: BLE001 — a broken registry must read as "nothing signed"
        return {}


def record_signoff(root: Path | str, slug: str, operator: str, date: str = "") -> None:
    """Record a human sign-off for one §14 item. Refuses unknown slugs and empty operators.

    This only *records* the human's confirmation — it never performs the gated work, and the
    verdict still requires every auto-check to PASS (Inv 4/5).
    """
    root = Path(root)
    if slug not in manual_slugs():
        raise ValueError(f"unknown sign-off slug: {slug!r} (see manual_slugs())")
    operator = operator.strip()
    if not operator:
        raise ValueError("operator name required — sign-off must name the human (Inv 5)")
    if not date:
        from datetime import date as _date
        date = _date.today().isoformat()
    path = root / _SIGNOFF_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:  # noqa: BLE001 — missing/corrupt file starts fresh
        data = {}
    signoffs = data.get("signoffs")
    if not isinstance(signoffs, dict):
        signoffs = {}
    signoffs[slug] = {"operator": operator, "date": date}
    data["signoffs"] = signoffs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_preflight(root: Path | str = ".") -> list[Check]:
    """Run auto-checks and append the operator-only §14 items (SIGNED if recorded, else MANUAL)."""
    root = Path(root)
    checks = _auto_checks(root)
    signoffs = _load_signoffs(root)
    for slug, text in _MANUAL_ITEMS:
        s = signoffs.get(slug)
        if s:
            checks.append(Check(text, SIGNED, f"signed by {s['operator']} on {s['date']}",
                                slug=slug))
        else:
            checks.append(Check(text, MANUAL, slug=slug))
    return checks


def is_ready(checks: list[Check]) -> bool:
    """Ready only when every auto-check PASSes and every operator item is SIGNED.

    FAIL/WARN can never be signed away; an empty report is never ready. Stays False pre-P4.
    """
    return bool(checks) and all(c.status in (PASS, SIGNED) for c in checks)
