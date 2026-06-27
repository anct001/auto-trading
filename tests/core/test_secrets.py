"""Tests for src/core/secrets.py — env/vault secret loader, never logged (§10, Inv. 6/7).

The Secret wrapper makes accidental leakage hard: its repr/str/format are masked, so logging a
Secret (or interpolating it into a string, or writing it to the event log) yields "***", never
the value. The real value is reachable only via an explicit .reveal().
"""
from __future__ import annotations

import pytest

from src.core import secrets as secmod
from src.core.secrets import Secret


def test_repr_and_str_are_masked():
    s = Secret("AKIA-LIVE-XYZ")
    assert "AKIA-LIVE-XYZ" not in repr(s)
    assert "AKIA-LIVE-XYZ" not in str(s)


def test_format_string_is_masked():
    s = Secret("topsecret")
    assert "topsecret" not in f"{s}"
    assert "topsecret" not in "{}".format(s)


def test_reveal_returns_the_value():
    assert Secret("topsecret").reveal() == "topsecret"


def test_equality_by_value_without_leaking():
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")


def test_load_secret_from_env():
    env = {"EXCHANGE_API_KEY": "k123"}
    s = secmod.load_secret("EXCHANGE_API_KEY", env=env)
    assert isinstance(s, Secret) and s.reveal() == "k123"


def test_load_secret_missing_raises():
    with pytest.raises(secmod.MissingSecretError):
        secmod.load_secret("NOPE", env={})


def test_load_optional_returns_none_when_absent():
    assert secmod.load_optional("NOPE", env={}) is None
    assert secmod.load_optional("K", env={"K": "v"}).reveal() == "v"


def test_mask_helper():
    assert secmod.mask("anything") == "***"
