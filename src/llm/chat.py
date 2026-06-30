"""src/llm/chat.py — operator chat assistant (READ-ONLY, explain-only).

A natural-language assistant the operator can ask about the bot's CURRENT state ("why are we
flat?", "what's my drawdown?", "explain the last rejection"). It is strictly an EXPLAINER:

  - **Inv 1** (the LLM never places/sizes an order) holds BY CONSTRUCTION: this module imports
    nothing from the risk / execution / order path. It can only read a snapshot dict and return
    text — there is no tool, no callback, no write. (An import-safety test pins this.)
  - **Inv 2** (the fast loop never waits on the LLM): nothing in the trading loop calls this; it
    runs only when the operator asks, on the UI thread. Ollama down → a fail-soft message, never an
    effect on trading.
  - **Inv 6** (no secrets in logs/state): the context is built from the already-redacted dashboard
    snapshot, and `build_context_summary` curates an explicit field whitelist — a future dashboard
    field cannot silently leak into the prompt.

The model is steered by a strong system prompt to REFUSE any request to act ("place", "buy",
"change the limit"): it must answer that it is read-only and point to the deterministic,
risk-gated manual controls. The transport is injectable so prompt-building, parsing, and the
fail-soft contract are unit-tested without an Ollama server.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Callable

ChatTransport = Callable[[str, dict], str]

_DEFAULT_HOST = "http://localhost:11434"
_MAX_QUESTION_CHARS = 1000
_MAX_LOG_LINES = 12

SYSTEM_PROMPT = (
    "You are a READ-ONLY assistant embedded in a solo-operator crypto trading dashboard. "
    "You can ONLY explain the current state shown to you. You CANNOT place, size, cancel, or "
    "modify any order; you CANNOT change risk limits, configuration, or the kill-switch. If the "
    "operator asks you to do any of those, reply that you are read-only — all trading is "
    "deterministic and risk-gated — and point them to the manual order form (which still passes "
    "the risk engine) or the kill-switch control. Answer concisely from the STATE only; if the "
    "state does not contain the answer, say so. Never invent numbers."
)


def _fmt_pct(x: object) -> str:
    try:
        return f"{float(x):+.2f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"


def build_context_summary(snapshot: dict) -> str:
    """Render a compact, WHITELISTED text summary of the dashboard snapshot for the prompt.

    Only named fields flow into the prompt (no secrets ever do — the dashboard payload is already
    redacted, and curating fields means a newly-added field can't leak by accident)."""
    if not snapshot:
        return "(no live state available)"
    lines: list[str] = [
        f"Equity: {snapshot.get('equity')}",
        f"Day return: {_fmt_pct(snapshot.get('day_return_pct'))} "
        f"(soft limit {_fmt_pct(snapshot.get('daily_soft_pct'))}, "
        f"hard {_fmt_pct(snapshot.get('daily_hard_pct'))})",
        f"Drawdown: {_fmt_pct(snapshot.get('drawdown_pct'))} "
        f"(kill-switch at {_fmt_pct(snapshot.get('killswitch_pct'))})",
        f"Gross exposure: {_fmt_pct(snapshot.get('gross_exposure_pct'))} "
        f"(cap {_fmt_pct(snapshot.get('gross_cap_pct'))})",
    ]
    if snapshot.get("killswitch_engaged"):
        lines.append(f"Kill-switch: ENGAGED ({snapshot.get('killswitch_reason')})")
    else:
        lines.append("Kill-switch: armed")

    positions = snapshot.get("positions") or []
    if positions:
        lines.append("Positions:")
        for p in positions:
            lines.append(
                f"  - {p.get('pair')}: qty {p.get('qty')} @ {p.get('entry_price')}, "
                f"value {p.get('value')}, uPnL {p.get('unrealized_pnl')}, "
                f"exposure {_fmt_pct(p.get('exposure_pct'))}"
            )
    else:
        lines.append("Positions: flat (no open positions)")

    log = snapshot.get("decision_log") or []
    if log:
        lines.append("Recent decisions (newest first):")
        for e in log[:_MAX_LOG_LINES]:
            lines.append(
                f"  - {e.get('timestamp')} {e.get('type')} {e.get('pair')} {e.get('detail')}"
            )
    return "\n".join(lines)


def build_messages(question: str, snapshot: dict) -> list[dict]:
    """Build the Ollama /api/chat message list: system + state + the operator's question. Pure."""
    q = (question or "").strip()[:_MAX_QUESTION_CHARS]
    user = f"CURRENT STATE:\n{build_context_summary(snapshot)}\n\nOPERATOR QUESTION: {q}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_chat_response(raw: str) -> str:
    """Extract the assistant text from an Ollama /api/chat reply (or raise ValueError)."""
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed chat response: {e}") from e
    if isinstance(body, dict):
        msg = body.get("message")
        if isinstance(msg, dict) and "content" in msg:
            return str(msg["content"]).strip()
        if "response" in body:  # /api/generate-shaped fallback
            return str(body["response"]).strip()
    raise ValueError("no message content in chat response")


def _http_post(url: str, payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (local, trusted host)
        return resp.read().decode("utf-8")


class OllamaChat:
    """Read-only operator chat via a local Ollama model. Off the trading path (Inv 1/2)."""

    def __init__(
        self,
        *,
        model: str = "llama3.1",
        host: str = _DEFAULT_HOST,
        temperature: float = 0.2,
        num_predict: int = 400,
        transport: ChatTransport | None = None,
    ):
        self.model = model
        self.url = f"{host.rstrip('/')}/api/chat"
        self.temperature = temperature
        self.num_predict = num_predict
        self._transport = transport or _http_post

    def answer(self, question: str, snapshot: dict) -> str:
        payload = {
            "model": self.model,
            "messages": build_messages(question, snapshot),
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.num_predict},
        }
        return parse_chat_response(self._transport(self.url, payload))


def answer(question: str, snapshot: dict, *, client: OllamaChat) -> dict:
    """Fail-soft entry point for the UI: returns ``{"answer", "error"}``. Never raises.

    A blank question is refused without calling the model. Any backend failure (Ollama down,
    malformed reply) yields a graceful 'unavailable' answer — the dashboard keeps working and the
    trading loop is unaffected (Inv 2)."""
    q = (question or "").strip()
    if not q:
        return {"answer": "", "error": "empty question"}
    try:
        return {"answer": client.answer(q, snapshot), "error": None}
    except Exception as e:  # noqa: BLE001 — fail-soft: a chat error must never reach the UI/loop
        return {"answer": f"(assistant unavailable: {e})", "error": str(e)}
