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

from dataclasses import dataclass
from pathlib import Path

PASS, FAIL, WARN, MANUAL = "PASS", "FAIL", "WARN", "MANUAL"


@dataclass(frozen=True)
class Check:
    item: str
    status: str
    detail: str = ""


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


# §14 items that no script can confirm — only the operator can, with evidence + sign-off
_MANUAL_ITEMS = [
    "P0-P3 gates all passed & re-proven from clean",
    "Forward dry-run >= N_days (30), no crash, signal metrics ~ backtest",
    "A validated strategy with net-of-cost+tax edge (current EMA-cross has NONE)",
    "Risk drills each fired: per-trade/correlation/daily soft+hard/drawdown kill/breakers",
    "Exchange-side protective stop attaches at fill & survives killed process (naked test)",
    "Manual over-limit order rejected; UI read-only except kill-switch + risk-gated order",
    "Exchange entity/endpoint confirmed; testnet read-balance + small order; key trade-only/"
    "withdrawals-off/IP-whitelisted",
    "Restart-safety: kill mid-trade -> clean recovery, no double-trade",
    "Capacity + order-feasibility (minNotional/lot/tick at 0.5% size) verified",
    "De-peg guard live-tested; human-heartbeat dead-man + demotion-to-paper configured",
    "Capital-on-exchange cap set; after-tax edge modeled; jurisdiction/ToS/tax confirmed (§11)",
    "Capital is smallest meaningful amount; spot only; no leverage",
    "HUMAN SIGN-OFF recorded",
]


def run_preflight(root: Path | str = ".") -> list[Check]:
    """Run auto-checks and append the operator-only (MANUAL) §14 items."""
    root = Path(root)
    checks = _auto_checks(root)
    checks += [Check(it, MANUAL) for it in _MANUAL_ITEMS]
    return checks


def is_ready(checks: list[Check]) -> bool:
    """Ready only if every check is PASS (no FAIL/WARN/MANUAL outstanding). Stays False pre-P4."""
    return bool(checks) and all(c.status == PASS for c in checks)
