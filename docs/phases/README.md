# docs/phases/ — per-phase spec + DONE-GATE

Each phase has an **entry gate** that must be *proven* (not asserted) before advancing
(MASTER_DRIVER §6). The kill-switch halts a session; phase gates govern the lifecycle; the
demotion rule (§6) is the ladder going back down.

| Phase | Capital | LLM role | File |
|-------|---------|----------|------|
| P0 | none | none | [`P0.md`](P0.md) |
| P1 | paper | sentiment haircut + explainer | [`P1.md`](P1.md) |
| P2 | paper | offline hypothesis generation | [`P2.md`](P2.md) |
| P3 | paper | monitor / orchestrate | [`P3.md`](P3.md) |
| P4 | tiny real | research / decision support | [`P4.md`](P4.md) |
| P5 | scale slowly | unchanged | [`P5.md`](P5.md) |

Defaults: **N_days = 30** continuous dry-run before advancing; **MAX_POSITIONS = 3**.

Every phase boundary is a `verification-before-completion` / DONE-GATE event: re-prove the
**whole** system from a clean state, not just the new slice.
