"""src/core/profile.py — optional operator profile (config/app.toml) feeding CLI defaults.

Beginner convenience: instead of remembering a long flag string, the operator keeps their usual
settings in ``config/app.toml`` (written by ``autotrader init``, or by hand) and just runs
``autotrader``. Explicit CLI flags always override the profile.

SCOPE — convenience only, never authority: the profile can only set the same knobs the CLI flags
expose (data exchange, pair, timeframe, paper equity, UI options…). It is NOT a risk/trading
config — those live in the hash-locked ``config/{risk,strategy,backtest}`` files (§15) and cannot
be loosened from here. The file is git-ignored (per-operator, like ``.env``); a committed
``config/app.example.toml`` documents the keys.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_PATH = "config/app.toml"

# profile keys allowed to feed dry_run CLI defaults (whitelist — anything else is ignored,
# so a typo or a smuggled key can never reach argparse)
ALLOWED_KEYS = {
    "data_exchange", "pair", "timeframe", "equity", "iterations", "poll_seconds", "events",
    "replay_days", "pairs", "strategies", "perp", "serve_ui", "ui_port", "chat_model",
    "chat_provider", "chat_base_url", "alert_webhook",
}


def load_profile(path: str | Path = DEFAULT_PATH) -> dict:
    """Read the ``[dry_run]`` table from ``config/app.toml`` → whitelisted CLI-default overrides.

    Missing file → {} (the profile is optional). A malformed file raises ValueError with a clear
    message (silent fallback would hide the operator's typo)."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"malformed profile {p}: {e}") from e
    section = data.get("dry_run", {})
    if not isinstance(section, dict):
        return {}
    return {k: v for k, v in section.items() if k in ALLOWED_KEYS}


def write_profile(values: dict, path: str | Path = DEFAULT_PATH) -> Path:
    """Write a ``[dry_run]`` profile (whitelisted keys only). Returns the path written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# AutoTrader operator profile — CLI defaults for `autotrader` (flags still override).",
             "# Convenience only: risk/trading limits live in the hash-locked config/ files (§15).",
             "", "[dry_run]"]
    for k, v in values.items():
        if k not in ALLOWED_KEYS:
            continue
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k} = {v}")
        else:
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
