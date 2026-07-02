"""Tests for repo-root anchoring (audit #6) + ccxt-metadata market constraints (audit #5)."""
from __future__ import annotations


from src.core.paths import repo_root
from src.data.market_meta import market_constraints_from_ccxt
from src.risk.sizing import MarketConstraints

_FALLBACK = MarketConstraints(min_notional=10.0, lot_step=1e-5, tick_size=0.1)


# ---- repo root --------------------------------------------------------------------------------

def test_repo_root_env_override(tmp_path):
    assert repo_root(env={"AUTOTRADER_ROOT": str(tmp_path)}) == tmp_path


def test_repo_root_walks_up_to_the_marker(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.lock.json").write_text("{}", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert repo_root(env={}, cwd=deep) == tmp_path


def test_repo_root_falls_back_to_package_location(tmp_path):
    # cwd is an unrelated directory (installed-command case) → the editable-install repo wins
    root = repo_root(env={}, cwd=tmp_path)
    assert (root / "config" / "config.lock.json").exists()
    assert (root / "pyproject.toml").exists()


def test_configs_load_from_any_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # simulate `autotrader` run from $HOME
    from src.risk.config import RiskConfig
    root = repo_root(env={})
    cfg = RiskConfig.load(root / "config" / "risk" / "default.json")
    assert cfg.leverage == 0


# ---- market constraints from ccxt metadata ----------------------------------------------------

class _DecimalPlacesExchange:
    id = "kucoinlike"
    precisionMode = 2  # DECIMAL_PLACES
    markets = {"BTC/USDT": {"precision": {"amount": 5, "price": 1},
                            "limits": {"cost": {"min": 1.0}}}}


class _TickSizeExchange:
    id = "bitbanklike"
    precisionMode = 4  # TICK_SIZE — values ARE the step
    markets = {"BTC/JPY": {"precision": {"amount": 0.0001, "price": 1.0},
                           "limits": {"cost": {"min": 500.0}}}}


def test_decimal_places_mode_converts_to_steps():
    mc, src = market_constraints_from_ccxt(_DecimalPlacesExchange(), "BTC/USDT", _FALLBACK)
    assert mc.lot_step == 1e-5 and mc.tick_size == 0.1 and mc.min_notional == 1.0
    assert "metadata" in src


def test_tick_size_mode_uses_values_directly():
    mc, _ = market_constraints_from_ccxt(_TickSizeExchange(), "BTC/JPY", _FALLBACK)
    assert mc.lot_step == 0.0001 and mc.tick_size == 1.0 and mc.min_notional == 500.0


def test_missing_pair_or_fields_fail_soft_to_fallback():
    mc, src = market_constraints_from_ccxt(_TickSizeExchange(), "NOPE/USD", _FALLBACK)
    assert mc == _FALLBACK and src.startswith("fallback")

    class NoLimits:
        id = "x"
        precisionMode = 2
        markets = {"BTC/USDT": {"precision": {}, "limits": {}}}
    mc, _ = market_constraints_from_ccxt(NoLimits(), "BTC/USDT", _FALLBACK)
    assert mc == _FALLBACK                              # every field individually defaulted


def test_strategy_loads_from_hash_locked_config():
    # audit #5: the live loop must consume the SAME hash-locked params the lock verifies
    import json
    from src.strategy.ema_cross import EmaCross
    root = repo_root(env={})
    cfg = json.loads((root / "config" / "strategy" / "ema_cross.json").read_text(encoding="utf-8"))
    strat = EmaCross.from_config(cfg)
    assert (strat.ema_fast, strat.ema_slow) == (cfg["params"]["ema_fast"], cfg["params"]["ema_slow"])
