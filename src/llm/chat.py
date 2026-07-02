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
import re
import urllib.request
from typing import Callable

ChatTransport = Callable[[str, dict], str]

_DEFAULT_HOST = "http://localhost:11434"
_MAX_QUESTION_CHARS = 1000
_MAX_LOG_LINES = 12
_MAX_TRADES = 5
_MAX_HISTORY_TURNS = 6
_CITE_RE = re.compile(r"\[D(\d+)\]")

SYSTEM_PROMPT = (
    "You are a READ-ONLY assistant embedded in a solo-operator crypto trading dashboard. "
    "You can ONLY explain the current state shown to you. You CANNOT place, size, cancel, or "
    "modify any order; you CANNOT change risk limits, configuration, or the kill-switch. If the "
    "operator asks you to do any of those, reply that you are read-only — all trading is "
    "deterministic and risk-gated — and point them to the manual order form (which still passes "
    "the risk engine) or the kill-switch control. Answer concisely from the STATE only; if the "
    "state does not contain the answer, say so. Never invent numbers. The decision-log entries are "
    "labelled [D1], [D2], …; when your answer refers to a specific past decision, cite its label in "
    "square brackets (e.g. [D2]). Only cite labels that actually appear in the state."
)


def _fmt_pct(x: object) -> str:
    try:
        return f"{float(x):+.2f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"


def _performance_line(perf: dict) -> str:
    pf = perf.get("profit_factor")
    pf_str = "∞" if pf is None else (f"{pf:.2f}" if isinstance(pf, (int, float)) else "n/a")
    wr = perf.get("win_rate")
    wr_str = f"{float(wr) * 100:.1f}%" if isinstance(wr, (int, float)) else "n/a"
    return (
        f"Performance (realized): {perf.get('trade_count', 0)} trades, win rate {wr_str}, "
        f"profit factor {pf_str}, expectancy {_fmt_pct(perf.get('expectancy'))}, "
        f"total P&L {perf.get('total_pnl')}, best {_fmt_pct(perf.get('best'))}, "
        f"worst {_fmt_pct(perf.get('worst'))}"
    )


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

    health = snapshot.get("health")
    if isinstance(health, dict):
        line = (f"Bot health: {'running' if health.get('running') else 'stopped'}, "
                f"{health.get('tick_count', 0)} ticks, last tick {health.get('last_tick_at')}")
        if health.get("last_error"):
            line += f", last error: {health.get('last_error')}"
        lines.append(line)

    perf = snapshot.get("performance")
    if isinstance(perf, dict) and perf.get("trade_count"):
        lines.append(_performance_line(perf))

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

    trades = snapshot.get("trades") or []
    if trades:
        lines.append("Recent closed trades (newest first):")
        for t in trades[:_MAX_TRADES]:
            lines.append(
                f"  - {t.get('exit_time')} {t.get('pair')} return {_fmt_pct(t.get('return'))}, "
                f"P&L {t.get('pnl')}"
            )

    log = snapshot.get("decision_log") or []
    if log:
        lines.append("Recent decisions (newest first):")
        for i, e in enumerate(log[:_MAX_LOG_LINES], start=1):
            lines.append(
                f"  [D{i}] {e.get('timestamp')} {e.get('type')} {e.get('pair')} {e.get('detail')}"
            )
    return "\n".join(lines)


def extract_citations(answer_text: str, snapshot: dict) -> list[dict]:
    """Map [D#] labels in the model's answer back to the real decision-log entries (deterministic).

    De-duped in order of first appearance; out-of-range labels are dropped. The indexing matches
    `build_context_summary` exactly (1-based over the same `_MAX_LOG_LINES` slice), so a citation
    always resolves to the entry the model was shown — the UI can render verifiable references."""
    log = (snapshot.get("decision_log") or [])[:_MAX_LOG_LINES]
    cites: list[dict] = []
    seen: set[int] = set()
    for m in _CITE_RE.finditer(answer_text or ""):
        n = int(m.group(1))
        if n in seen or n < 1 or n > len(log):
            continue
        seen.add(n)
        e = log[n - 1]
        cites.append({
            "ref": f"D{n}",
            "timestamp": e.get("timestamp"),
            "type": e.get("type"),
            "pair": e.get("pair"),
            "detail": e.get("detail"),
        })
    return cites


def _sanitize_history(history: object) -> list[dict]:
    """Coerce caller-supplied prior turns into safe {role, content} dicts. Never raises.

    Keeps only user/assistant roles with string content (trimmed), drops anything malformed, and
    bounds to the last ``_MAX_HISTORY_TURNS`` so a long conversation can't blow the context window.
    """
    if not isinstance(history, list):
        return []
    out: list[dict] = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content.strip()[:_MAX_QUESTION_CHARS]})
    return out[-_MAX_HISTORY_TURNS:]


def build_multi_pair_summary(snapshot: dict) -> str:
    """Render a multi-pair replay/dry-run comparison (from replay_dashboard.build_replay_comparison)
    for the prompt, so the assistant can analyse and look up across coins. Whitelisted fields only."""
    if not snapshot or not snapshot.get("table"):
        return "(no multi-pair replay data available)"
    is_strat = snapshot.get("dimension") == "strategy"
    what = "strategies" if is_strat else "pairs"
    header = (f"Multi-strategy replay comparison on {snapshot.get('coin')}"
              if is_strat else "Multi-pair replay comparison")
    lines = [
        f"{header} (start equity {snapshot.get('start_equity')}).",
        f"Available {what}: {', '.join(snapshot.get('pairs', []))}",
        f"Best by return: {snapshot.get('best_pair')} · worst: {snapshot.get('worst_pair')}",
        f"Per {what[:-1] if what.endswith('s') else what} (sorted by total return):",
    ]
    for row in snapshot["table"]:
        pf = row.get("profit_factor")
        pf_s = "inf" if pf is None else (f"{pf:.2f}" if isinstance(pf, (int, float)) else "n/a")
        wr = row.get("win_rate")
        wr_s = f"{float(wr) * 100:.1f}%" if isinstance(wr, (int, float)) else "n/a"
        lines.append(
            f"  - {row.get('pair')}: return {_fmt_pct((row.get('total_return') or 0) * 100)}, "
            f"trades {row.get('trades')}, win rate {wr_s}, profit factor {pf_s}, "
            f"max DD {_fmt_pct((row.get('max_drawdown') or 0) * 100)}, "
            f"Calmar {float(row.get('calmar') or 0):.2f}, sharpe {float(row.get('sharpe') or 0):.3f}, "
            f"sortino {float(row.get('sortino') or 0):.3f}, "
            f"VaR95 {_fmt_pct((row.get('var95') or 0) * 100)}, "
            f"CVaR95 {_fmt_pct((row.get('cvar95') or 0) * 100)}, "
            f"MC-drawdown p95 {_fmt_pct((row.get('mc_dd_p95') or 0) * 100)} / "
            f"p99 {_fmt_pct((row.get('mc_dd_p99') or 0) * 100)}, "
            f"avg exposure {_fmt_pct((row.get('avg_exposure') or 0) * 100)}, "
            f"time in market {_fmt_pct((row.get('time_in_market') or 0) * 100)}, "
            f"final equity {row.get('final_equity')}"
        )
    return "\n".join(lines)


def build_messages(question: str, snapshot: dict, history: object = None, *,
                   context_builder=build_context_summary) -> list[dict]:
    """Build the Ollama /api/chat message list: system + prior turns + state + question. Pure.

    The current state is attached only to the latest turn (freshest, not duplicated into history),
    so follow-ups ("why?") keep conversational context without re-sending stale snapshots.
    ``context_builder`` renders the snapshot into the STATE block (single-pair dashboard by
    default; pass ``build_multi_pair_summary`` for the cross-coin replay comparison)."""
    q = (question or "").strip()[:_MAX_QUESTION_CHARS]
    user = f"CURRENT STATE:\n{context_builder(snapshot)}\n\nOPERATOR QUESTION: {q}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_sanitize_history(history),
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


def _http_post_headers(url: str, payload: dict, headers: dict) -> str:
    """POST JSON with extra headers (auth). Used by the cloud backends; key never logged here."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (operator-configured host)
        return resp.read().decode("utf-8")


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split a build_messages list into (system_prompt_text, non-system turns) for APIs (Anthropic)
    that take the system prompt as a separate field rather than a ``role:"system"`` message."""
    system = ""
    turns: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            system = str(m.get("content", ""))
        else:
            turns.append(m)
    return system, turns


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

    def answer(self, question: str, snapshot: dict, history: object = None, *,
               context_builder=build_context_summary) -> str:
        payload = {
            "model": self.model,
            "messages": build_messages(question, snapshot, history, context_builder=context_builder),
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.num_predict},
        }
        return parse_chat_response(self._transport(self.url, payload))


# --- OPT-IN cloud providers (data LEAVES the machine — see build_chat_client) -----------------

def parse_anthropic_response(raw: str) -> str:
    """Extract assistant text from an Anthropic Messages API reply (or raise ValueError)."""
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed anthropic response: {e}") from e
    if isinstance(body, dict):
        if body.get("type") == "error":
            raise ValueError(f"anthropic error: {(body.get('error') or {}).get('message')}")
        content = body.get("content")
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if any(t.strip() for t in texts):
                return "".join(texts).strip()
    raise ValueError("no text content in anthropic response")


def parse_openai_response(raw: str) -> str:
    """Extract assistant text from an OpenAI-compatible chat/completions reply (or raise)."""
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed openai response: {e}") from e
    if isinstance(body, dict):
        if body.get("error"):
            raise ValueError(f"openai error: {(body.get('error') or {}).get('message')}")
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict) and msg.get("content") is not None:
                return str(msg["content"]).strip()
    raise ValueError("no message content in openai response")


class AnthropicChat:
    """Read-only operator chat via the Anthropic Messages API (Claude). CLOUD provider — OPT-IN.

    The dashboard snapshot is sent to Anthropic (data leaves the machine). The API key is held as a
    `core.secrets.Secret` (masked in logs/repr) and revealed only for the `x-api-key` header, never
    logged. Off the trading path (Inv 1/2); read-only (Inv 1)."""

    def __init__(self, *, api_key, model: str = "claude-opus-4-8",
                 host: str = "https://api.anthropic.com", max_tokens: int = 1024,
                 transport: ChatTransport | None = None):
        from src.core.secrets import Secret
        self._key = api_key if isinstance(api_key, Secret) else Secret(str(api_key))
        self.model = model
        self.url = f"{host.rstrip('/')}/v1/messages"
        self.max_tokens = max_tokens
        self._transport = transport or self._post

    def _post(self, url: str, payload: dict) -> str:
        return _http_post_headers(url, payload, {
            "x-api-key": self._key.reveal(), "anthropic-version": "2023-06-01"})

    def answer(self, question: str, snapshot: dict, history: object = None, *,
               context_builder=build_context_summary) -> str:
        system, turns = _split_system(
            build_messages(question, snapshot, history, context_builder=context_builder))
        payload = {"model": self.model, "max_tokens": self.max_tokens,
                   "system": system, "messages": turns}
        return parse_anthropic_response(self._transport(self.url, payload))


class OpenAIChat:
    """Read-only operator chat via an OpenAI-compatible chat/completions endpoint. CLOUD — OPT-IN.

    Works with OpenAI and any compatible API (set ``base_url``). Sends the dashboard snapshot to the
    provider (data leaves the machine). Key held as a masked `Secret`, used only for the
    Authorization header. Read-only, off the trading path (Inv 1/2)."""

    def __init__(self, *, api_key, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1", max_tokens: int = 1024,
                 temperature: float = 0.2, transport: ChatTransport | None = None):
        from src.core.secrets import Secret
        self._key = api_key if isinstance(api_key, Secret) else Secret(str(api_key))
        self.model = model
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._transport = transport or self._post

    def _post(self, url: str, payload: dict) -> str:
        return _http_post_headers(url, payload, {"Authorization": f"Bearer {self._key.reveal()}"})

    def answer(self, question: str, snapshot: dict, history: object = None, *,
               context_builder=build_context_summary) -> str:
        payload = {
            "model": self.model,
            "messages": build_messages(question, snapshot, history, context_builder=context_builder),
            "max_tokens": self.max_tokens, "temperature": self.temperature}
        return parse_openai_response(self._transport(self.url, payload))


def parse_gemini_response(raw: str) -> str:
    """Extract assistant text from a Google Gemini generateContent reply (or raise ValueError)."""
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed gemini response: {e}") from e
    if isinstance(body, dict):
        if body.get("error"):
            raise ValueError(f"gemini error: {(body.get('error') or {}).get('message')}")
        cands = body.get("candidates")
        if isinstance(cands, list) and cands:
            parts = ((cands[0].get("content") or {}).get("parts")
                     if isinstance(cands[0], dict) else None)
            if isinstance(parts, list):
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                if any(t.strip() for t in texts):
                    return "".join(texts).strip()
    raise ValueError("no text content in gemini response")


class GeminiChat:
    """Read-only operator chat via Google's Gemini generateContent API. CLOUD provider — OPT-IN.

    Sends the dashboard snapshot to Google (data leaves the machine). Key held as a masked `Secret`,
    used only for the `x-goog-api-key` header. Read-only, off the trading path (Inv 1/2). Gemini
    uses roles user/model (assistant→model) and a separate ``system_instruction``."""

    def __init__(self, *, api_key, model: str = "gemini-1.5-flash",
                 host: str = "https://generativelanguage.googleapis.com", max_tokens: int = 1024,
                 temperature: float = 0.2, transport: ChatTransport | None = None):
        from src.core.secrets import Secret
        self._key = api_key if isinstance(api_key, Secret) else Secret(str(api_key))
        self.model = model
        self.url = f"{host.rstrip('/')}/v1beta/models/{model}:generateContent"
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._transport = transport or self._post

    def _post(self, url: str, payload: dict) -> str:
        return _http_post_headers(url, payload, {"x-goog-api-key": self._key.reveal()})

    def answer(self, question: str, snapshot: dict, history: object = None, *,
               context_builder=build_context_summary) -> str:
        system, turns = _split_system(
            build_messages(question, snapshot, history, context_builder=context_builder))
        contents = [{"role": "model" if t["role"] == "assistant" else "user",
                     "parts": [{"text": t["content"]}]} for t in turns]
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self.max_tokens, "temperature": self.temperature},
        }
        return parse_gemini_response(self._transport(self.url, payload))


def build_chat_client(provider: str = "ollama", *, model: str | None = None, api_key=None,
                      host: str | None = None, base_url: str | None = None,
                      transport: ChatTransport | None = None):
    """Build the read-only chat backend for ``provider`` (default local Ollama — data never leaves).

    Providers: ``ollama`` (local, default), ``anthropic`` (Claude Messages API), ``openai``
    (OpenAI-compatible; ``base_url`` overrides the endpoint). Cloud providers REQUIRE an API key and
    send the dashboard snapshot off-machine — the caller is responsible for warning the operator and
    passing the key from a secret store, never a log. All backends stay read-only (Inv 1)."""
    p = (provider or "ollama").lower()
    if p == "ollama":
        return OllamaChat(model=model or "llama3.1", host=host or _DEFAULT_HOST, transport=transport)
    if p == "anthropic":
        if not api_key:
            raise ValueError("anthropic provider requires an API key (set ANTHROPIC_API_KEY)")
        return AnthropicChat(api_key=api_key, model=model or "claude-opus-4-8",
                             host=host or "https://api.anthropic.com", transport=transport)
    if p in ("openai", "openai-compatible"):
        if not api_key:
            raise ValueError("openai provider requires an API key (set OPENAI_API_KEY)")
        return OpenAIChat(api_key=api_key, model=model or "gpt-4o-mini",
                          base_url=base_url or host or "https://api.openai.com/v1",
                          transport=transport)
    if p in ("gemini", "google"):
        if not api_key:
            raise ValueError("gemini provider requires an API key (set GEMINI_API_KEY)")
        return GeminiChat(api_key=api_key, model=model or "gemini-1.5-flash",
                          host=host or "https://generativelanguage.googleapis.com",
                          transport=transport)
    raise ValueError(f"unknown chat provider: {provider!r} "
                     "(use ollama, anthropic, openai, or gemini)")


# provider registry: name -> (env var for the API key, is-local). Extend this to add a provider.
CHAT_PROVIDERS = {
    "ollama": (None, True),
    "anthropic": ("ANTHROPIC_API_KEY", False),
    "openai": ("OPENAI_API_KEY", False),
    "gemini": ("GEMINI_API_KEY", False),
}


class ChatRouter:
    """Holds one read-only backend per available provider and routes a request to the chosen one.

    The API keys are baked into the pre-built clients (never re-sent by the UI); the browser only
    picks a provider *name*. Every backend is read-only (Inv 1)."""

    def __init__(self, clients: dict, default: str):
        if not clients:
            raise ValueError("ChatRouter needs at least one client")
        self._clients = clients
        self.default = default if default in clients else next(iter(clients))

    def providers(self) -> dict:
        """UI payload: the available providers, their models, and which is the default."""
        return {
            "default": self.default,
            "providers": [{"name": n, "model": getattr(c, "model", "?"),
                           "default": n == self.default} for n, c in self._clients.items()],
        }

    def client_for(self, name: str | None):
        return self._clients.get(name or self.default) or self._clients[self.default]

    def answer(self, question: str, snapshot: dict, *, provider: str | None = None,
               history: object = None, context_builder=build_context_summary) -> dict:
        return answer(question, snapshot, client=self.client_for(provider), history=history,
                      context_builder=context_builder)


def build_chat_router(*, env=None, default_provider: str = "ollama",
                      ollama_model: str = "llama3.1", ollama_host: str = _DEFAULT_HOST,
                      base_url: str | None = None) -> ChatRouter:
    """Build a ChatRouter with every provider whose API key is present in ``env`` (ollama always).

    Local Ollama is always included (data never leaves). A cloud provider is added only if its key
    env var is set; the key is loaded as a masked Secret. Adding a new provider is one entry in
    ``CHAT_PROVIDERS`` plus a ``build_chat_client`` branch."""
    import os

    from src.core.secrets import load_optional
    env = os.environ if env is None else env
    clients: dict = {"ollama": OllamaChat(model=ollama_model, host=ollama_host)}
    for name, (env_var, is_local) in CHAT_PROVIDERS.items():
        if is_local or env_var is None:
            continue
        key = load_optional(env_var, env=env)
        if key is not None:
            clients[name] = build_chat_client(name, api_key=key, base_url=base_url)
    return ChatRouter(clients, default_provider)


def answer(question: str, snapshot: dict, *, client, history: object = None,
           context_builder=build_context_summary) -> dict:
    """Fail-soft entry point for the UI: returns ``{"answer", "error"}``. Never raises.

    A blank question is refused without calling the model. ``history`` (prior {role, content}
    turns) is sanitized and bounded so follow-ups work. Any backend failure (Ollama down,
    malformed reply) yields a graceful 'unavailable' answer — the dashboard keeps working and the
    trading loop is unaffected (Inv 2)."""
    q = (question or "").strip()
    if not q:
        return {"answer": "", "error": "empty question", "citations": []}
    try:
        text = client.answer(q, snapshot, history, context_builder=context_builder)
        return {"answer": text, "error": None, "citations": extract_citations(text, snapshot)}
    except Exception as e:  # noqa: BLE001 — fail-soft: a chat error must never reach the UI/loop
        return {"answer": f"(assistant unavailable: {e})", "error": str(e), "citations": []}
