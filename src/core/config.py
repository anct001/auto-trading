"""src/core/config.py — §15 declarative config hash-lock (integrity check).

Hashes version-controlled, **declarative** config (strategy params, risk limits, pairlists, the
cost model) and refuses to run on an unexpected change. The hash is over *canonical* content
(parsed JSON, sorted keys, no whitespace), so reformatting or key reordering is ignored — only a
semantic change trips it.

Critically (§15 FIX): this applies **only to declarative config files**. It is never applied to
runtime-computed state (rolling correlation, ATR-based sizing) or to legitimate runtime
*tightening* of limits (Invariant 3) — that separation is structural here: this module only ever
takes config-file paths. A legitimate config change is re-locked with human sign-off (regenerate
the lockfile), it is not silently accepted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ConfigIntegrityError(RuntimeError):
    """Raised when a locked config file is missing from the manifest or has changed."""


def _canonical_bytes(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def hash_config_file(path: str | Path) -> str:
    """SHA-256 of a config file's canonical content (formatting/key-order independent)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return hashlib.sha256(_canonical_bytes(data)).hexdigest()


def build_manifest(paths: list[str | Path]) -> dict[str, str]:
    """Build a {path: hash} manifest for the given config files (the lock baseline)."""
    return {str(p): hash_config_file(p) for p in paths}


def verify_configs(paths: list[str | Path], manifest: dict[str, str]) -> None:
    """Verify each path's current hash matches the manifest. Raise on any drift."""
    problems = []
    for p in paths:
        key = str(p)
        if key not in manifest:
            problems.append(f"{key}: not in manifest (config not locked)")
            continue
        if hash_config_file(p) != manifest[key]:
            problems.append(f"{key}: hash mismatch (config changed without re-locking)")
    if problems:
        raise ConfigIntegrityError("; ".join(problems))


def write_manifest(manifest: dict[str, str], path: str | Path) -> None:
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(path: str | Path) -> dict[str, str]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
