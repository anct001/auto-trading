"""src/llm/hypothesis_gen.py — offline LLM strategy-hypothesis proposer (§5, P2).

The slow loop proposes candidate strategy specs OFFLINE. It only suggests — **a human reviews and
approves each before it ever enters a backtest** (Inv. 1/8), and a candidate only becomes a trade
after it clears `backtest/hypothesis.validate_hypothesis` (walk-forward + deflated Sharpe). This
module never touches the trading path.

§5 discipline enforced here: a **strict parameter budget**. Proposals with more than ``max_params``
tunable parameters are dropped, not trimmed — more knobs is more overfitting surface. Parsing is
defensive (malformed entries skipped); if nothing valid survives it raises so the caller knows the
cycle produced no usable candidates.

Dependency-free Ollama backend (stdlib urllib), transport injected for offline testing — same
pattern as `ollama_client.py`. Temperature is non-zero here: ideation wants some diversity, unlike
the deterministic sentiment classifier.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

_DEFAULT_HOST = "http://localhost:11434"
_JSON_SPAN = re.compile(r"[\[{].*[\]}]", re.DOTALL)

Transport = Callable[[str, dict], str]


@dataclass(frozen=True)
class HypothesisProposal:
    name: str
    params: dict[str, Any]
    target_regime: str = ""
    rationale: str = ""


def build_hypothesis_prompt(context: str, *, n: int, max_params: int) -> str:
    """Prompt the model for ``n`` strategy hypotheses, each within the parameter budget."""
    return (
        "You are a quantitative strategy researcher. Propose simple, testable trading-strategy "
        f"hypotheses for the following context.\n\nCONTEXT:\n{context}\n\n"
        f"Propose exactly {n} hypotheses. Each MUST use at most {max_params} tunable parameters "
        "(strict budget — fewer is better; avoid overfitting). Output ONLY a JSON array; each "
        'element: {"name": str, "params": {<=N numeric params}, "target_regime": str, '
        '"rationale": one sentence}. No text outside the JSON array.\n'
    )


def _coerce(items: list, max_params: int) -> list[HypothesisProposal]:
    out: list[HypothesisProposal] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        params = it.get("params", {})
        if not name or not isinstance(params, dict):
            continue
        if len(params) > max_params:  # §5 strict budget — drop, never trim
            continue
        out.append(HypothesisProposal(
            name=str(name), params=dict(params),
            target_regime=str(it.get("target_regime", "")),
            rationale=str(it.get("rationale", "")),
        ))
    return out


def parse_hypotheses_response(text: str, *, max_params: int) -> list[HypothesisProposal]:
    """Parse the model reply into valid proposals within budget, or raise ValueError if none."""
    match = _JSON_SPAN.search(text or "")
    if not match:
        raise ValueError("no JSON in hypotheses response")
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed hypotheses response: {e}") from e
    items = obj.get("hypotheses", []) if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        raise ValueError("hypotheses payload is not a list")
    proposals = _coerce(items, max_params)
    if not proposals:
        raise ValueError("no valid hypotheses within the parameter budget")
    return proposals


def _http_post(url: str, payload: dict) -> str:
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (local, trusted host)
        return resp.read().decode("utf-8")


class OllamaHypothesisProposer:
    """Propose strategy hypotheses via a local Ollama model (offline ideation, human-gated)."""

    def __init__(
        self,
        *,
        model: str = "llama3.1",
        host: str = _DEFAULT_HOST,
        temperature: float = 0.7,
        num_predict: int = 600,
        transport: Transport | None = None,
    ):
        self.model = model
        self.url = f"{host.rstrip('/')}/api/generate"
        self.temperature = temperature
        self.num_predict = num_predict
        self._transport = transport or _http_post

    def propose(self, context: str, *, n: int = 3, max_params: int = 2) -> list[HypothesisProposal]:
        payload = {
            "model": self.model,
            "prompt": build_hypothesis_prompt(context, n=n, max_params=max_params),
            "format": "json",
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.num_predict},
        }
        raw = self._transport(self.url, payload)
        try:
            body = json.loads(raw)
            text = body.get("response", raw) if isinstance(body, dict) else raw
        except json.JSONDecodeError:
            text = raw
        return parse_hypotheses_response(text, max_params=max_params)
