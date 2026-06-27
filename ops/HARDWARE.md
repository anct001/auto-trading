# ops/HARDWARE.md — hardware requirements

The design has **two decoupled loops** (§3, Invariant 2), so hardware splits into two independent
parts that need **not** be the same machine: the **trading box** (CPU, always-on, static IP) and
the **LLM box** (GPU, optional, async). The trading box needs **no GPU**.

| Purpose | Minimum spec | GPU? |
|---|---|---|
| **Dev / backtest / dry-run (current state)** | Any modern laptop, or a 1 vCPU / 1–2 GB VPS. ~few GB disk for Parquet. | No |
| **Live trading (P4, real capital)** | **Static-IP VPS** (required to IP-whitelist the API key, §10), 2 vCPU / 2 GB, high uptime, **NTP-synced clock** (§9), restart-safe (§9). A ~$5–10/mo VPS suffices. | No |
| **Local LLM slow loop (P1+, OPTIONAL)** | Ollama: 7–8B ≈ 6–8 GB VRAM, 13–14B ≈ 10–16 GB, 32B ≈ 20–24 GB. Or Apple Silicon (unified memory). Or **skip it**. | Yes (or Apple Silicon) |
| **Operator UI (P3)** | Runs alongside the trading box; negligible extra. | No |

## Key points

- **The money-making part (deterministic engine) is hardware-trivial.** The real hardware cost is
  the **LLM (GPU)** — which is **optional, decoupled, and never on the trading path**. If the LLM
  is down/slow, the fast loop ignores that feature (Inv. 2); default `FLOOR = 1.0` means the LLM
  only logs until forward-validated (§6 P1). **You can reach P4 (tiny real capital) with just a
  cheap static-IP VPS — no GPU at all.**
- The LLM box does **not** need high uptime and can be a separate machine (home PC with a GPU, a
  Mac, or rented GPU-by-the-hour). It writes JSON state files the trading loop reads optionally.
- Storage: Parquet (OHLCV) + SQLite/Postgres (trades/state/audit) + append-only event log — tens
  of GB of SSD is ample for a solo operator. Multi-region failover is deferred (§16).
- No co-location / HFT hardware: the strategy cadence is per closed candle (e.g. 1h).
