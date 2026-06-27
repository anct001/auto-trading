"""src/ui/dashboard/model.py — operator control dashboard read model (§12).

Pure, read-only derivation of the operator's at-a-glance status from the portfolio state, the
risk config, current prices, and (optionally) the kill-switch, slow-loop sentiment state, and the
event log. It computes nothing that trades — it only *reads* and presents (Invariant: UI is
read-only by default; the only writes live elsewhere — kill-switch + the risk-gated manual order).

Surfaces the §12 dashboard fields: equity; today's P&L vs the soft/hard daily limits; drawdown vs
the kill-switch; gross + per-asset exposure vs caps; open positions with unrealized P&L; kill-switch
status; slow-loop (sentiment) freshness; and a decision log with the "why" per recent event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.events.log import Event
from src.llm.sentiment import SentimentState
from src.risk.config import RiskConfig
from src.risk.killswitch import KillSwitch
from src.risk.types import PortfolioState

# event types worth showing in the operator decision log, with how to render their "why"
_LOG_TYPES = ("RiskRejected", "RiskPassed", "OrderSubmitted", "SignalGenerated", "SentimentHaircut")


@dataclass(frozen=True)
class PositionView:
    pair: str
    qty: float
    entry_price: float
    price: float
    value: float
    unrealized_pnl: float
    exposure_pct: float


@dataclass(frozen=True)
class DecisionLogEntry:
    timestamp: str
    type: str
    pair: str
    detail: str


@dataclass(frozen=True)
class DashboardState:
    equity: float
    day_return_pct: float
    daily_soft_pct: float
    daily_hard_pct: float
    daily_soft_breached: bool
    daily_hard_breached: bool
    drawdown_pct: float
    killswitch_pct: float
    killswitch_breach: bool
    gross_exposure_pct: float
    gross_cap_pct: float
    positions: list[PositionView]
    killswitch_engaged: bool
    killswitch_reason: str | None
    sentiment_fresh: bool | None
    decision_log: list[DecisionLogEntry] = field(default_factory=list)


def _detail(ev: Event) -> str:
    p = ev.payload
    if ev.type in ("RiskRejected", "RiskPassed"):
        reasons = p.get("reasons") or []
        return f"{p.get('side', '')} {p.get('pair', '')} {'; '.join(reasons) or 'ok'}".strip()
    if ev.type == "SignalGenerated":
        return f"intent={p.get('intent', '')}"
    if ev.type == "SentimentHaircut":
        return f"multiplier={p.get('multiplier')} {p.get('rationale', '')}".strip()
    if ev.type == "OrderSubmitted":
        return f"{p.get('side', '')} qty={p.get('qty', '')}"
    return ""


def _decision_log(events: list[Event] | None, limit: int) -> list[DecisionLogEntry]:
    if not events:
        return []
    relevant = [e for e in events if e.type in _LOG_TYPES]
    entries = [
        DecisionLogEntry(timestamp=e.timestamp, type=e.type,
                         pair=str(e.payload.get("pair", "")), detail=_detail(e))
        for e in relevant
    ]
    entries.reverse()  # newest first
    return entries[:limit]


def build_dashboard(
    *,
    state: PortfolioState,
    cfg: RiskConfig,
    prices: dict[str, float],
    killswitch: KillSwitch | None = None,
    events: list[Event] | None = None,
    sentiment_state: SentimentState | None = None,
    now: datetime | None = None,
    decision_log_limit: int = 20,
) -> DashboardState:
    """Derive the read-only operator dashboard state. Pure; never trades."""
    eq = state.equity
    day_return = state.day_return()
    drawdown = state.drawdown()

    positions: list[PositionView] = []
    gross_value = 0.0
    for pair, pos in state.positions.items():
        price = prices.get(pair, pos.entry_price)
        value = pos.value(price)
        gross_value += value
        positions.append(PositionView(
            pair=pair, qty=pos.qty, entry_price=pos.entry_price, price=price, value=value,
            unrealized_pnl=(price - pos.entry_price) * pos.qty, exposure_pct=100.0 * value / eq,
        ))

    when = now or datetime.now(timezone.utc)
    sentiment_fresh = None if sentiment_state is None else sentiment_state.is_fresh(when)

    return DashboardState(
        equity=eq,
        day_return_pct=100.0 * day_return,
        daily_soft_pct=float(cfg.daily_soft_pct),
        daily_hard_pct=float(cfg.daily_hard_pct),
        daily_soft_breached=100.0 * day_return <= cfg.daily_soft_pct,
        daily_hard_breached=100.0 * day_return <= cfg.daily_hard_pct,
        drawdown_pct=100.0 * drawdown,
        killswitch_pct=float(cfg.max_drawdown_killswitch_pct),
        killswitch_breach=100.0 * drawdown <= cfg.max_drawdown_killswitch_pct,
        gross_exposure_pct=100.0 * gross_value / eq,
        gross_cap_pct=float(cfg.gross_exposure_pct),
        positions=positions,
        killswitch_engaged=bool(killswitch and killswitch.is_halted),
        killswitch_reason=(killswitch.halt_reason or None) if killswitch and killswitch.is_halted else None,
        sentiment_fresh=sentiment_fresh,
        decision_log=_decision_log(events, decision_log_limit),
    )
