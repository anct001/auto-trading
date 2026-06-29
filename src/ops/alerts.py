"""src/ops/alerts.py — operator alerting (§12 "alerts on breaker/limit/kill", §15).

Turns the dashboard read-model into alerts and dispatches them to pluggable sinks (print, event
log, webhook → Telegram/push/Slack). Two design choices a pro alerter needs:

  - **Edge-triggered:** ``Alerter`` only emits an alert when a condition *becomes* active (and
    re-arms when it clears) — so a persistent kill-switch doesn't spam every poll.
  - **Fail-soft dispatch:** a broken sink (webhook down) never breaks the loop or other sinks.

Pure evaluation (``evaluate_alerts``) is separate from delivery, so the logic is fully testable
without any network. Alerting only *reports* — it never trades or moves money.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

CRITICAL, WARN, INFO = "critical", "warn", "info"


@dataclass(frozen=True)
class Alert:
    level: str
    code: str
    message: str


def evaluate_alerts(d: dict) -> list[Alert]:
    """Derive the active alerts from a dashboard payload (see ui.dashboard.model)."""
    out: list[Alert] = []
    if d.get("killswitch_engaged"):
        out.append(Alert(CRITICAL, "kill_switch", "Kill-switch engaged — trading halted"))
    day, soft, hard = d.get("day_return_pct", 0.0), d.get("daily_soft_pct", 0.0), d.get("daily_hard_pct", 0.0)
    if day <= hard:
        out.append(Alert(CRITICAL, "daily_hard_limit", f"Daily loss {day:.2f}% ≤ hard {hard}%"))
    elif day <= soft:
        out.append(Alert(WARN, "daily_soft_limit", f"Daily loss {day:.2f}% ≤ soft {soft}%"))
    dd, ks = d.get("drawdown_pct", 0.0), d.get("killswitch_pct", 0.0)
    if ks and dd <= ks:
        out.append(Alert(CRITICAL, "max_drawdown", f"Drawdown {dd:.2f}% ≤ kill {ks}%"))
    elif ks and dd <= ks / 2:
        out.append(Alert(WARN, "drawdown_warning", f"Drawdown {dd:.2f}% nearing kill {ks}%"))
    gross, cap = d.get("gross_exposure_pct", 0.0), d.get("gross_cap_pct", 0.0)
    if cap and gross > cap:
        out.append(Alert(WARN, "gross_exposure", f"Gross exposure {gross:.1f}% > cap {cap}%"))
    err = (d.get("health") or {}).get("last_error")
    if err:
        out.append(Alert(WARN, "loop_error", f"Loop error: {err}"))
    return out


def print_sink(a: Alert) -> None:
    print(f"[ALERT:{a.level}] {a.code} — {a.message}")


def event_log_sink(events) -> Callable[[Alert], None]:
    """Sink that appends each alert to the §15 event log."""
    def _sink(a: Alert) -> None:
        events.append("Alert", {"level": a.level, "code": a.code, "message": a.message})
    return _sink


def webhook_sink(url: str) -> Callable[[Alert], None]:
    """Sink that POSTs each alert as JSON to a webhook (Telegram/Slack/push). Fail-soft."""
    import urllib.request

    def _sink(a: Alert) -> None:
        try:
            body = json.dumps({"level": a.level, "code": a.code, "text": a.message}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 (operator-configured URL)
        except Exception:
            pass  # a down webhook must never break the loop
    return _sink


class Alerter:
    """Edge-triggered alert dispatcher: emits an alert only when its condition becomes active."""

    def __init__(self, sinks: list[Callable[[Alert], None]] | None = None):
        self.sinks = list(sinks or [])
        self._active: set[str] = set()

    def update(self, dashboard: dict) -> list[Alert]:
        """Evaluate, dispatch only the NEWLY active alerts, re-arm cleared ones; return the new ones."""
        current = evaluate_alerts(dashboard)
        by_code = {a.code: a for a in current}
        new = [a for code, a in by_code.items() if code not in self._active]
        self._active = set(by_code)  # cleared conditions drop out → re-arm
        for a in new:
            for sink in self.sinks:
                try:
                    sink(a)
                except Exception:
                    pass  # fail-soft: one bad sink must not break the rest
        return new
