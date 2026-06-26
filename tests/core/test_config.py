"""Tests for src/core/config.py — declarative config hash-lock (§15 integrity check).

The hash-lock applies ONLY to declarative config files (strategy params, risk limits, pairlists,
cost model). It hashes canonical content (so reformatting/key-order is ignored) and refuses to
run on an unexpected change. It is never applied to runtime-computed state or runtime tightening
(that separation is structural: this module only takes config file paths).
"""
from __future__ import annotations

import json

import pytest

from src.core import config as cfgmod


def _write(path, data):
    path.write_text(json.dumps(data))
    return path


def test_hash_is_stable_across_formatting(tmp_path):
    a = _write(tmp_path / "a.json", {"x": 1, "y": 2})
    b = tmp_path / "b.json"
    b.write_text('{\n  "y": 2,\n   "x": 1\n}\n')  # same content, different whitespace + key order
    assert cfgmod.hash_config_file(a) == cfgmod.hash_config_file(b)


def test_hash_changes_on_value_change(tmp_path):
    a = _write(tmp_path / "a.json", {"per_trade_risk_pct": 0.5})
    b = _write(tmp_path / "b.json", {"per_trade_risk_pct": 1.0})
    assert cfgmod.hash_config_file(a) != cfgmod.hash_config_file(b)


def test_build_and_verify_roundtrip(tmp_path):
    p = _write(tmp_path / "risk.json", {"per_trade_risk_pct": 0.5})
    manifest = cfgmod.build_manifest([p])
    cfgmod.verify_configs([p], manifest)  # must not raise


def test_verify_detects_modification(tmp_path):
    p = _write(tmp_path / "risk.json", {"per_trade_risk_pct": 0.5})
    manifest = cfgmod.build_manifest([p])
    _write(p, {"per_trade_risk_pct": 1.0})  # tampered after locking
    with pytest.raises(cfgmod.ConfigIntegrityError) as exc:
        cfgmod.verify_configs([p], manifest)
    assert str(p) in str(exc.value)


def test_verify_detects_missing_from_manifest(tmp_path):
    p = _write(tmp_path / "risk.json", {"per_trade_risk_pct": 0.5})
    with pytest.raises(cfgmod.ConfigIntegrityError):
        cfgmod.verify_configs([p], manifest={})  # not locked at all


def test_manifest_persists_and_reloads(tmp_path):
    p = _write(tmp_path / "risk.json", {"per_trade_risk_pct": 0.5})
    lock = tmp_path / "config.lock.json"
    cfgmod.write_manifest(cfgmod.build_manifest([p]), lock)
    manifest = cfgmod.load_manifest(lock)
    cfgmod.verify_configs([p], manifest)  # must not raise


def test_project_config_files_are_locked_and_unmodified():
    """The committed lockfile must match the committed config files (catches drift in CI/startup)."""
    from pathlib import Path

    root = Path(__file__).parents[2]
    lock = cfgmod.load_manifest(root / "config" / "config.lock.json")
    paths = [root / rel for rel in lock]
    cfgmod.verify_configs(paths, {str(root / rel): h for rel, h in lock.items()})
