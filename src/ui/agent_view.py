"""src/ui/agent_view.py — §12 agent-view overlay ("the valuable part").

For one coin, surface the system's *own* state over the market data so the operator sees **why**
the agent is or isn't acting on that asset: the strategy signal, regime enablement, the sentiment
size-haircut, the current position, and the kill-switch. Read-only and deterministic — it reuses
the exact predicates the fast loop uses (`is_strategy_enabled`, `size_haircut`), so the overlay
can't disagree with what the loop would actually do.

It computes no orders and places nothing; `acting` is a derived explanation (would the agent open
here this tick?), with `blocked_by` listing the concrete reasons it would not.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.llm.sentiment import SentimentState, size_haircut
from src.risk import engine
from src.risk.config import RiskConfig
from src.risk.types import PortfolioState
from src.strategy.base import INTENT_ENTER_LONG
from src.strategy.regime import RegimeState, is_strategy_enabled

_HELD_EPS = 1e-12


def build_agent_view(
    *,
    pair: str,
    df: pd.DataFrame,
    strategy,
    state: PortfolioState,
    cfg: RiskConfig,
    ctx: engine.RiskContext,
    now: datetime,
    sentiment_state: SentimentState | None = None,
    regime_state: RegimeState | None = None,
) -> dict:
    """Build the read-only agent-view overlay for ``pair`` (see module docstring)."""
    intent = str(strategy.generate_signals(df).iloc[-1])
    price = float(df["close"].iloc[-1]) if len(df) else float("nan")
    pos = state.positions.get(pair)
    held = pos.qty if pos else 0.0

    target_regime = getattr(strategy, "target_regime", "any")
    regime_enabled = is_strategy_enabled(regime_state, pair, target_regime, now=now)
    haircut = size_haircut(sentiment_state, pair, now=now, floor=cfg.sentiment_floor)
    ks = ctx.killswitch
    trading_allowed = ks.allows_trading() if ks is not None else True

    blocked: list[str] = []
    if intent != INTENT_ENTER_LONG:
        blocked.append(f"intent:{intent}")
    if held > _HELD_EPS:
        blocked.append("already_long")
    if not regime_enabled:
        blocked.append("regime_disabled")
    if not trading_allowed:
        blocked.append("killswitch")
    acting = not blocked  # would open a new long here this tick

    return {
        "pair": pair,
        "price": price,
        "signal": intent,
        "acting": acting,
        "blocked_by": blocked,
        "regime_enabled": regime_enabled,
        "target_regime": target_regime,
        "sentiment_haircut": haircut,
        "sentiment_fresh": (sentiment_state.is_fresh(now) if sentiment_state is not None else None),
        "killswitch_engaged": not trading_allowed,
        "position": {
            "qty": held,
            "entry_price": pos.entry_price if pos else None,
            "unrealized_pnl": (price - pos.entry_price) * held if pos else 0.0,
        },
    }
