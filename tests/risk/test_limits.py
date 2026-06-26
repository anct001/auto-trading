"""Tests for src/risk/limits.py — hard portfolio limits (PLAN_RISK R2, §4).

Money code. Each limit is a pure predicate returning (ok, reason). Boundaries matter: just-under
passes, at/over fails. The correlation-cluster check is the crypto-specific one — several
"diversified" longs that are really one BTC-beta bet must be summed as a single cluster.

(Per-trade *risk* is enforced upstream in sizing R1; daily soft blocks new entries only, hard
and drawdown halt everything — the engine R6 applies side/halt semantics.)
"""
from __future__ import annotations

from src.risk import limits
from src.risk.config import RiskConfig
from src.risk.types import Order, Position, PortfolioState


def _cfg(**over):
    d = {
        "per_trade_risk_pct": 0.5, "position_sizing": "inverse_atr", "max_fractional_kelly": 0.5,
        "max_concurrent_positions": 3, "gross_exposure_pct": 100, "per_asset_cap_pct": 25,
        "correlation": {"cluster_corr_threshold": 0.7, "cluster_exposure_cap_pct": 40},
        "daily_loss_limits": {"soft_pct": -2.0, "hard_pct": -4.0},
        "max_drawdown_killswitch_pct": -12.0,
        "depeg_guard": {"quote_assets": ["USDT"], "threshold_pct": 1.0},
        "capital_on_exchange_cap": None, "human_heartbeat_days": 7, "leverage": 0,
        "exchange_assertions": {},
    }
    d.update(over)
    return RiskConfig.from_dict(d)


def _pos(pair, value):
    return Position(pair=pair, qty=value, entry_price=1.0)  # value at price 1.0 == qty


def _state(equity, positions=None, peak=None, day_start=None):
    return PortfolioState(
        equity=equity, peak_equity=peak or equity,
        day_start_equity=day_start or equity, positions=positions or {},
    )


def _order(pair, notional):
    return Order(pair=pair, side="buy", qty=notional, price=1.0, source="strategy")


def _prices(*pairs):
    return {p: 1.0 for p in pairs}


# ---- gross exposure (≤ 100%) -----------------------------------------------------------------

def test_gross_exposure_boundary():
    state = _state(1000.0, {"ETH/USDT": _pos("ETH/USDT", 800.0)})
    prices = _prices("ETH/USDT", "BTC/USDT")
    assert limits.check_gross_exposure(_order("BTC/USDT", 200.0), state, _cfg(), prices).ok
    bad = limits.check_gross_exposure(_order("BTC/USDT", 201.0), state, _cfg(), prices)
    assert not bad.ok and bad.reason == "gross_exposure"


# ---- per-asset cap (≤ 25%) -------------------------------------------------------------------

def test_per_asset_cap_boundary():
    state = _state(1000.0)
    prices = _prices("BTC/USDT")
    assert limits.check_per_asset(_order("BTC/USDT", 250.0), state, _cfg(), prices).ok
    assert not limits.check_per_asset(_order("BTC/USDT", 251.0), state, _cfg(), prices).ok


def test_per_asset_cap_counts_existing_same_pair():
    state = _state(1000.0, {"BTC/USDT": _pos("BTC/USDT", 100.0)})
    prices = _prices("BTC/USDT")
    # 100 existing + 200 new = 300 > 250 cap
    assert not limits.check_per_asset(_order("BTC/USDT", 200.0), state, _cfg(), prices).ok


# ---- concurrency (≤ 3 positions) -------------------------------------------------------------

def test_concurrency_blocks_fourth_new_pair():
    positions = {p: _pos(p, 10.0) for p in ("A/USDT", "B/USDT", "C/USDT")}
    state = _state(1000.0, positions)
    res = limits.check_concurrency(_order("D/USDT", 10.0), state, _cfg())
    assert not res.ok and res.reason == "max_concurrent_positions"


def test_concurrency_allows_adding_to_existing_pair():
    positions = {p: _pos(p, 10.0) for p in ("A/USDT", "B/USDT", "C/USDT")}
    state = _state(1000.0, positions)
    assert limits.check_concurrency(_order("A/USDT", 10.0), state, _cfg()).ok  # no new slot


# ---- daily soft / hard / drawdown (state-level) ----------------------------------------------

def test_daily_soft_blocks_entries_at_threshold():
    assert limits.check_daily_soft(_state(985.0, day_start=1000.0), _cfg()).ok  # -1.5%, not reached
    blocked = limits.check_daily_soft(_state(980.0, day_start=1000.0), _cfg())  # -2.0%
    assert not blocked.ok and blocked.reason == "daily_soft_limit"


def test_daily_hard_halts_at_threshold():
    assert limits.check_daily_hard(_state(970.0, day_start=1000.0), _cfg()).ok  # -3% < hard
    halted = limits.check_daily_hard(_state(960.0, day_start=1000.0), _cfg())  # -4%
    assert not halted.ok and halted.reason == "daily_hard_limit"


def test_drawdown_killswitch_at_threshold():
    assert limits.check_drawdown(_state(890.0, peak=1000.0), _cfg()).ok  # -11% < 12
    killed = limits.check_drawdown(_state(880.0, peak=1000.0), _cfg())  # -12%
    assert not killed.ok and killed.reason == "drawdown_killswitch"


# ---- correlation cluster (treat >0.7-corr positions as one, cap 40%) -------------------------

def test_correlated_cluster_summed_breaches_cap_even_when_each_under_per_asset():
    # ETH 25% + new SOL 25%, corr 0.8 → cluster 50% > 40% cap
    state = _state(1000.0, {"ETH/USDT": _pos("ETH/USDT", 250.0)})
    prices = _prices("ETH/USDT", "SOL/USDT")
    corr = {("ETH/USDT", "SOL/USDT"): 0.8}
    res = limits.check_correlation_cluster(_order("SOL/USDT", 250.0), state, _cfg(), prices, corr)
    assert not res.ok and res.reason == "correlation_cluster_cap"


def test_uncorrelated_positions_not_clustered():
    state = _state(1000.0, {"ETH/USDT": _pos("ETH/USDT", 250.0)})
    prices = _prices("ETH/USDT", "SOL/USDT")
    corr = {("ETH/USDT", "SOL/USDT"): 0.5}  # below 0.7 threshold
    # cluster is just the new 25% order → under the 40% cap
    assert limits.check_correlation_cluster(_order("SOL/USDT", 250.0), state, _cfg(), prices, corr).ok


# ---- portfolio beta (mechanism; cap passed explicitly until a config default exists) ---------

def test_portfolio_beta_cap():
    state = _state(1000.0, {"ETH/USDT": _pos("ETH/USDT", 500.0)})
    prices = _prices("ETH/USDT", "SOL/USDT")
    betas = {"ETH/USDT": 1.2, "SOL/USDT": 1.5}
    # (500*1.2 + 500*1.5)/1000 = 1.35 beta-weighted exposure
    assert limits.check_portfolio_beta(_order("SOL/USDT", 500.0), state, prices, betas, max_beta=1.5).ok
    assert not limits.check_portfolio_beta(
        _order("SOL/USDT", 500.0), state, prices, betas, max_beta=1.3
    ).ok
