"""src/llm/ollama_client.py — Ollama sentiment backend for the slow loop (§3).

A thin, dependency-free adapter (stdlib ``urllib``) that implements the
``orchestrator.SentimentClassifier`` protocol against a local Ollama server. It is **off the
trading path** — only the slow loop calls it, on a minutes cadence, to produce sentiment_state.json.

Token discipline (context-token-efficiency): one bounded request per pair, `format="json"` for a
structured reply, `temperature=0` for determinism, a small `num_predict` cap, and raw documents
trimmed before they enter the prompt. The HTTP transport is injectable so prompt-building and
response-parsing are unit-testable without a server; production uses the stdlib POST.

This module never imports into the fast loop. If Ollama is down, the slow loop's per-pair call
raises and the orchestrator omits that pair — the fast loop then treats it as neutral (Inv. 2).
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable

from src.llm.sentiment import PairSentiment

_DEFAULT_HOST = "http://localhost:11434"
_MAX_DOC_CHARS = 2000  # trim each document before it enters the window
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)

Transport = Callable[[str, dict], str]


def build_sentiment_prompt(pair: str, documents: list[str]) -> str:
    """Build the classification prompt. Asks for a strict JSON object and nothing else."""
    docs = "\n\n".join(d[:_MAX_DOC_CHARS] for d in documents) or "(no documents)"
    return (
        f"You are a crypto market sentiment classifier for the pair {pair}.\n"
        "Read the documents below and output ONLY a JSON object with keys:\n"
        '  "sentiment": a number in [-1, 1] (negative = bearish, positive = bullish),\n'
        '  "confidence": a number in [0, 1],\n'
        '  "rationale": a one-sentence explanation.\n'
        "Do not output anything except the JSON object.\n\n"
        f"DOCUMENTS:\n{docs}\n"
    )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def parse_sentiment_response(text: str) -> PairSentiment:
    """Parse the model's reply into a clamped PairSentiment, or raise ValueError on garbage.

    Tolerates surrounding prose by extracting the first JSON object. ``sentiment`` and
    ``confidence`` are required; ``rationale`` is optional.
    """
    match = _JSON_OBJ.search(text or "")
    if not match:
        raise ValueError("no JSON object in sentiment response")
    try:
        obj = json.loads(match.group(0))
        sentiment = float(obj["sentiment"])
        confidence = float(obj["confidence"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"malformed sentiment response: {e}") from e
    return PairSentiment(
        sentiment=_clamp(sentiment, -1.0, 1.0),
        confidence=_clamp(confidence, 0.0, 1.0),
        rationale=str(obj.get("rationale", "")),
    )


def _http_post(url: str, payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (local, trusted host)
        return resp.read().decode("utf-8")


class OllamaSentimentClassifier:
    """Classify per-pair sentiment via a local Ollama model (implements SentimentClassifier)."""

    def __init__(
        self,
        *,
        model: str = "llama3.1",
        host: str = _DEFAULT_HOST,
        temperature: float = 0.0,
        num_predict: int = 200,
        transport: Transport | None = None,
    ):
        self.model = model
        self.url = f"{host.rstrip('/')}/api/generate"
        self.temperature = temperature
        self.num_predict = num_predict
        self._transport = transport or _http_post

    def classify(self, pair: str, documents: list[str]) -> PairSentiment:
        payload = {
            "model": self.model,
            "prompt": build_sentiment_prompt(pair, documents),
            "format": "json",
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.num_predict},
        }
        raw = self._transport(self.url, payload)
        # Ollama's /api/generate wraps the model text in {"response": "..."}.
        try:
            body = json.loads(raw)
            text = body.get("response", raw) if isinstance(body, dict) else raw
        except json.JSONDecodeError:
            text = raw
        return parse_sentiment_response(text)
