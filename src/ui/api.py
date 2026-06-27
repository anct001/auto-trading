"""src/ui/api.py — §12 operator API: transport-agnostic JSON service layer.

All UI is read-only by default; the only writes are the human kill-switch and a manual order that
takes the SAME risk-engine path (Invariant 9) — no screen bypasses §4 limits. This module turns
the tested read model (`dashboard/model.build_dashboard`) and risk preview (`preview`) into
JSON-safe dicts a transport (FastAPI + SSE) can return verbatim. Keeping serialization here — not
in a web handler — lets it be unit-tested without a web dependency; the HTTP/SSE layer is a thin
wrapper added when the operator UI is wired.
"""
from __future__ import annotations

from dataclasses import asdict

from src.ui.dashboard.model import build_dashboard
from src.ui.preview import preview_manual_order


def dashboard_payload(**kwargs) -> dict:
    """Build the §12 dashboard read model and return it as a JSON-serializable dict."""
    return asdict(build_dashboard(**kwargs))


def preview_payload(**kwargs) -> dict:
    """Run a manual-order risk preview (Inv. 9) and return it as a JSON-serializable dict."""
    prev = preview_manual_order(**kwargs)
    return {
        "allowed": prev.allowed,
        "reasons": list(prev.reasons),
        "metrics": dict(prev.metrics),
        "order": asdict(prev.order) if prev.order is not None else None,
    }
