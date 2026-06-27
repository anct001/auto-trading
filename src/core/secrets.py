"""src/core/secrets.py — §10 env/vault secret loader; never logged, never committed.

Loads trade-only, withdrawal-disabled, IP-whitelisted keys from a git-ignored .env / vault
(injected via env vars at runtime). The Agent never handles the raw secret.

The `Secret` wrapper is the safety mechanism: its repr/str/format are masked, so a secret that is
accidentally logged, interpolated into a string, or written to the append-only event log (§15)
comes out as "***", never the value. The real value is reachable only via an explicit
`.reveal()`, which should be called as late as possible (right at the ccxt boundary).
"""
from __future__ import annotations

import os
from typing import Mapping

MASK = "***"


class MissingSecretError(RuntimeError):
    """Raised when a required secret is not present in the environment/vault."""


class Secret:
    """A string value that refuses to reveal itself except via .reveal()."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret({MASK})"

    def __str__(self) -> str:
        return MASK

    def __format__(self, spec: str) -> str:
        return MASK

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))


def mask(_value: object) -> str:
    """Always returns the mask — a convenience for redaction call-sites."""
    return MASK


def load_secret(name: str, *, env: Mapping[str, str] | None = None) -> Secret:
    """Load a required secret from the environment. Raises MissingSecretError if absent/empty."""
    source = os.environ if env is None else env
    value = source.get(name)
    if not value:
        raise MissingSecretError(f"required secret {name!r} is not set")
    return Secret(value)


def load_optional(name: str, *, env: Mapping[str, str] | None = None) -> Secret | None:
    """Load a secret if present, else None (e.g. the public viewing endpoint needs no key)."""
    source = os.environ if env is None else env
    value = source.get(name)
    return Secret(value) if value else None
