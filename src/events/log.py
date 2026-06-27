"""src/events/log.py — §15 append-only event log (immutable, secret-redacted).

The system's audit substrate (Invariant 6): an immutable, replayable stream of state
transitions — MarketReceived → SignalGenerated → RiskPassed/Rejected → OrderSubmitted →
FillReceived → PositionClosed — so the system can answer "why" for any order or skipped order
and reconstruct history.

Two hard properties:
  - **append-only:** there is no update/delete API; the on-disk JSONL only grows.
  - **secrets redacted before write:** append-only means a leaked secret could never be deleted,
    so any key/secret/token field is replaced with REDACTED *before* it touches the file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# canonical event types (the §15 transition chain)
MARKET_RECEIVED = "MarketReceived"
SIGNAL_GENERATED = "SignalGenerated"
RISK_PASSED = "RiskPassed"
RISK_REJECTED = "RiskRejected"
ORDER_SUBMITTED = "OrderSubmitted"
FILL_RECEIVED = "FillReceived"
POSITION_CLOSED = "PositionClosed"

REDACTED = "***REDACTED***"

# field-name substrings that mark a value as secret (redact before write — §10/§15)
_SECRET_HINTS = (
    "secret", "token", "password", "passwd", "api_key", "apikey",
    "apisecret", "private_key", "privatekey", "access_key", "accesskey", "signature",
)


def _is_secret_key(key: str) -> bool:
    low = key.lower()
    return any(hint in low for hint in _SECRET_HINTS)


# Secrets can also hide in VALUES (a credentialed URL, a key pasted into an error/free-text field).
# These patterns catch the realistic cases without over-redacting ordinary text.
_URL_CREDENTIALS = re.compile(r"(\w+://)[^/\s:@]+:[^/\s@]+@")
_KEY_TOKEN = re.compile(
    r"(?:AKIA[0-9A-Z]{12,}"          # AWS access key id
    r"|AIza[0-9A-Za-z_\-]{20,}"      # Google/Gemini API key
    r"|sk-[A-Za-z0-9]{12,}"          # OpenAI-style
    r"|ghp_[A-Za-z0-9]{20,}"         # GitHub token
    r"|xox[baprs]-[A-Za-z0-9\-]{10,})"  # Slack token
)


def _redact_value(value: Any) -> Any:
    """Redact secret-looking substrings inside a string value (URLs creds, known key tokens)."""
    if not isinstance(value, str):
        return value
    value = _URL_CREDENTIALS.sub(r"\1" + REDACTED + "@", value)
    value = _KEY_TOKEN.sub(REDACTED, value)
    return value


def redact(obj: Any) -> Any:
    """Recursively redact secret-named fields AND secret-looking values (dicts/lists traversed)."""
    if isinstance(obj, dict):
        return {k: (REDACTED if _is_secret_key(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return _redact_value(obj)


@dataclass(frozen=True)
class Event:
    type: str
    timestamp: str
    payload: dict[str, Any]


class EventLog:
    """Append-only JSONL event log. No update/delete by construction."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self, event_type: str, payload: dict[str, Any] | None = None, *, timestamp: str | None = None
    ) -> Event:
        """Append one event. The payload is redacted before it is written (irreversibly)."""
        event = Event(
            type=event_type,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            payload=redact(dict(payload or {})),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": event.type, "timestamp": event.timestamp,
                                 "payload": event.payload}) + "\n")
        return event

    def read_all(self) -> list[Event]:
        """Replay the full event stream in append order."""
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            events.append(Event(type=rec["type"], timestamp=rec["timestamp"], payload=rec["payload"]))
        return events


def log_risk_decision(log: EventLog, order, decision) -> Event:
    """Emit a RiskPassed/RiskRejected event for a risk-engine verdict (§15)."""
    event_type = RISK_PASSED if decision.approved else RISK_REJECTED
    return log.append(
        event_type,
        {
            "pair": order.pair,
            "side": order.side,
            "qty": order.qty,
            "price": order.price,
            "source": order.source,
            "reasons": list(decision.reasons),
        },
    )
