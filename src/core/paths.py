"""src/core/paths.py — resolve the repository root so the CLI works from any directory.

Every declarative config path used to be relative to the CWD, so the installed `autotrader`
command crashed when run outside the repo. Resolution order:

  1. ``AUTOTRADER_ROOT`` env var (explicit override — e.g. running against a second checkout),
  2. walk up from the CWD looking for the repo marker (``config/config.lock.json``),
  3. the package's own location (editable install → the repo the code lives in).
"""
from __future__ import annotations

import os
from pathlib import Path

_MARKER = Path("config") / "config.lock.json"


def repo_root(env: dict | None = None, cwd: Path | None = None) -> Path:
    e = os.environ if env is None else env
    override = e.get("AUTOTRADER_ROOT")
    if override:
        return Path(override)
    base = Path.cwd() if cwd is None else Path(cwd)
    for candidate in (base, *base.parents):
        if (candidate / _MARKER).exists():
            return candidate
    return Path(__file__).resolve().parents[2]  # src/core/paths.py → repo root
