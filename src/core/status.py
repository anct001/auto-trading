"""src/core/status.py — one "is everything OK?" snapshot for the /status page (§12).

Answers the first question a new operator asks: *is the software set up and healthy?* It gathers,
READ-ONLY and fail-soft (every section independently guarded — a broken file shows as a red light,
never a crashed page):

  - the current phase (parsed from PROGRESS.md),
  - the trading configuration (venue / pairs / timeframe / fees / leverage / sentiment floor),
  - the §14 pre-flight auto-checks (config hash-lock, leverage off, kill-switch, secrets hygiene),
  - which AI chat providers are available (env key NAMES only — values are never read here),
  - whether a local Ollama server is reachable (probe injectable for tests).

It places no orders, reveals no secret values, and never blocks the trading loop (UI-thread only).
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def read_current_phase(progress_path: str | Path = "PROGRESS.md") -> str:
    """The bold phase line under '## Current phase' in PROGRESS.md, or 'unknown'."""
    try:
        text = Path(progress_path).read_text(encoding="utf-8")
        m = re.search(r"## Current phase\s+\*\*(.+?)\*\*", text)
        return m.group(1).strip() if m else "unknown"
    except Exception:
        return "unknown"


def probe_ollama(host: str = "http://localhost:11434", timeout: float = 1.0) -> bool:
    """True if a local Ollama server answers /api/tags quickly. Local-only, fail-soft."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


def _trading_config(root: Path) -> dict:
    strat = json.loads((root / "config" / "strategy" / "ema_cross.json").read_text(encoding="utf-8"))
    costs = json.loads((root / "config" / "backtest" / "costs.json").read_text(encoding="utf-8"))
    risk = json.loads((root / "config" / "risk" / "default.json").read_text(encoding="utf-8"))
    return {
        "venue": strat.get("trading_venue"),
        "pairs": strat.get("pairs"),
        "timeframe": strat.get("timeframe"),
        "strategy": strat.get("name"),
        "maker_fee": costs.get("maker_fee"),
        "taker_fee": costs.get("taker_fee"),
        "leverage": risk.get("leverage"),
        "sentiment_floor": risk.get("sentiment_floor"),
    }


def _chat_providers(env) -> list[dict]:
    from src.llm.chat import CHAT_PROVIDERS
    out = []
    for name, (env_var, is_local) in CHAT_PROVIDERS.items():
        available = True if is_local else bool(env.get(env_var))
        out.append({"name": name, "local": is_local, "available": available,
                    "env_var": env_var})
    return out


def gather_status(*, root: str | Path = ".", env=None, ollama_probe=None) -> dict:
    """Assemble the full status payload. Every section fail-soft; see module docstring."""
    import os

    from src import __version__
    root = Path(root)
    env = os.environ if env is None else env
    probe = ollama_probe if ollama_probe is not None else probe_ollama

    out: dict = {"version": __version__, "paper_mode": True,
                 "phase": read_current_phase(root / "PROGRESS.md")}

    try:
        out["config"] = _trading_config(root)
        out["config_error"] = None
    except Exception as e:
        out["config"] = {}
        out["config_error"] = f"{type(e).__name__}: {e}"

    try:
        from src.core.preflight import MANUAL, PASS, is_ready, run_preflight
        checks = run_preflight(root)
        out["preflight"] = [{"item": c.item, "status": c.status, "detail": c.detail}
                            for c in checks]
        auto = [c for c in checks if c.status != MANUAL]
        out["auto_checks_pass"] = sum(1 for c in auto if c.status == PASS)
        out["auto_checks_total"] = len(auto)
        out["manual_items_pending"] = sum(1 for c in checks if c.status == MANUAL)
        out["ready_for_real_capital"] = is_ready(checks)
    except Exception as e:
        out["preflight"] = []
        out["auto_checks_pass"] = 0
        out["auto_checks_total"] = 0
        out["manual_items_pending"] = 0
        out["ready_for_real_capital"] = False
        out["preflight_error"] = f"{type(e).__name__}: {e}"

    try:
        out["chat_providers"] = _chat_providers(env)
    except Exception:
        out["chat_providers"] = []

    try:
        out["ollama_reachable"] = bool(probe())
    except Exception:
        out["ollama_reachable"] = False

    return out
