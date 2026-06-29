"""src/ops/metrics.py — Prometheus text exposition of the operator state (§15/§16).

Renders the dashboard read-model as Prometheus metrics so a standard scraper (Prometheus →
Grafana/Alertmanager) can chart and alert on the paper/live system — the ops observability a pro
desk expects. Pure function over the dashboard payload; the `/metrics` endpoint just serves its
output as `text/plain`. Read-only; exports state, never trades.

(The full Prometheus/Grafana/OpenTelemetry stack is the §16 eventual implementation; this is the
exposition surface it scrapes.)
"""
from __future__ import annotations

# (metric_name, dashboard_key, help) for plain gauges read straight off the payload
_GAUGES = [
    ("trading_equity", "equity", "Account equity"),
    ("trading_day_return_pct", "day_return_pct", "Today's return vs day-start (%)"),
    ("trading_drawdown_pct", "drawdown_pct", "Peak-to-current drawdown (%)"),
    ("trading_gross_exposure_pct", "gross_exposure_pct", "Gross exposure (% of equity)"),
]


def _line(name: str, value: float, help_: str) -> str:
    return f"# HELP {name} {help_}\n# TYPE {name} gauge\n{name} {value}\n"


def render_metrics(d: dict) -> str:
    """Render a dashboard payload as Prometheus exposition text."""
    parts = [_line(n, float(d.get(k, 0.0) or 0.0), h) for n, k, h in _GAUGES]
    parts.append(_line("trading_open_positions", float(len(d.get("positions") or [])),
                       "Open positions"))
    parts.append(_line("trading_killswitch_engaged", 1.0 if d.get("killswitch_engaged") else 0.0,
                       "Kill-switch engaged (1=halted)"))
    parts.append(_line("trading_tick_count", float((d.get("health") or {}).get("tick_count", 0)),
                       "Fast-loop ticks since start"))
    pf = d.get("performance") or {}
    parts.append(_line("trading_trade_count", float(pf.get("trade_count", 0)), "Closed trades"))
    parts.append(_line("trading_win_rate", float(pf.get("win_rate", 0.0)), "Win rate (fraction)"))
    parts.append(_line("trading_realized_pnl", float(pf.get("total_pnl", 0.0)), "Realized P&L"))
    if pf.get("profit_factor") is not None:  # None = ∞ (no losses) — not representable, omit
        parts.append(_line("trading_profit_factor", float(pf["profit_factor"]), "Profit factor"))
    return "".join(parts)
