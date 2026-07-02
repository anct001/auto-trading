"""Tests for src/core/profile.py + the `autotrader init` wizard (beginner on-ramp).

The profile is CLI-defaults convenience only: whitelisted keys, flags override, risk configs
untouched. The wizard is tested with injected IO (no interactive terminal needed).
"""
from __future__ import annotations

import pytest

from src.core.profile import load_profile, write_profile


def test_profile_roundtrip_whitelist_and_types(tmp_path):
    p = tmp_path / "app.toml"
    write_profile({"data_exchange": "kucoin", "pair": "BTC/USDT", "equity": 5000.0,
                   "serve_ui": True, "replay_days": 30,
                   "evil_key": "ignored", "leverage": 99}, path=p)   # non-whitelisted dropped
    prof = load_profile(p)
    assert prof == {"data_exchange": "kucoin", "pair": "BTC/USDT", "equity": 5000.0,
                    "serve_ui": True, "replay_days": 30}
    assert "leverage" not in prof                       # profile can never carry risk knobs


def test_profile_missing_file_is_empty_and_malformed_raises(tmp_path):
    assert load_profile(tmp_path / "absent.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("[dry_run\npair=", encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(bad)


def test_profile_feeds_argparse_defaults_but_flags_override(tmp_path, monkeypatch):
    # simulate main()'s wiring: set_defaults(**profile), then explicit flags win
    import argparse
    prof_path = tmp_path / "app.toml"
    write_profile({"pair": "ETH/USDT", "equity": 5000.0}, path=prof_path)
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="BTC/USDT")
    p.add_argument("--equity", type=float, default=10000.0)
    p.set_defaults(**load_profile(prof_path))
    args = p.parse_args([])                             # no flags -> profile wins
    assert args.pair == "ETH/USDT" and args.equity == 5000.0
    args = p.parse_args(["--pair", "SOL/USDT"])         # flag -> flag wins
    assert args.pair == "SOL/USDT" and args.equity == 5000.0


def _mark_as_repo_root(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.lock.json").write_text("{}", encoding="utf-8")


def test_init_wizard_writes_profile_and_prints_next_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                         # wizard anchors to the repo root
    _mark_as_repo_root(tmp_path)
    from src.dry_run import _init_wizard
    answers = iter(["kucoin", "btc/usdt", "7000", "3", "BTC/USDT,ETH/USDT", "yes"])
    printed: list[str] = []
    _init_wizard(input_fn=lambda prompt: next(answers), print_fn=printed.append)
    prof = load_profile(tmp_path / "config" / "app.toml")
    assert prof["data_exchange"] == "kucoin" and prof["pair"] == "BTC/USDT"  # upper-cased
    assert prof["equity"] == 7000.0
    assert prof["pairs"] == "BTC/USDT,ETH/USDT" and prof["replay_days"] == 30  # mode 3
    assert prof["serve_ui"] is True
    joined = "\n".join(printed)
    assert "autotrader" in joined and "/help" in joined  # tells the beginner what to do next


def test_init_wizard_defaults_and_bad_equity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mark_as_repo_root(tmp_path)
    from src.dry_run import _init_wizard
    answers = iter(["", "", "abc", "", ""])             # all defaults; equity invalid -> 10000
    _init_wizard(input_fn=lambda prompt: next(answers), print_fn=lambda s: None)
    prof = load_profile(tmp_path / "config" / "app.toml")
    assert prof["equity"] == 10000.0 and prof["replay_days"] == 30  # mode default = replay
    assert prof["data_exchange"] == "kucoin"
