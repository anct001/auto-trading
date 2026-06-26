"""ops/metrics.py — §15 system metrics + alerting (principle, not the full stack).

Exports health/perf metrics beyond the operator dashboard (§12): API / fill / strategy / LLM
latency, CPU/RAM/GPU, websocket/queue/DB health — with alerting. Prometheus/Grafana/
OpenTelemetry are the eventual implementation (§16), not mandated at the paper stage.

Scaffold only — no logic yet.
"""

# TODO(P3+): export a minimal metrics surface; wire alerting on breaker trips / gate events.
