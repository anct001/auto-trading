# ADR 0001 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-06-26

## Context

This is a phase-gated, capital-surviving trading system where individual design choices
(risk-cap defaults, the LLM's bounded role, the single execution path, exchange entity, fee
modeling) have real consequences and will be challenged by a second LLM reviewer and the
operator. We need a durable, greppable record of *why* each significant decision was made, so a
future session (or reviewer) does not silently re-litigate a settled trade-off — or worse,
reverse one of the §1 invariants without realizing it.

## Decision

We keep lightweight Architecture Decision Records in `docs/adr/`, numbered sequentially
(`NNNN-title.md`), in the style of Michael Nygard's ADRs. Each record states **Context →
Decision → Consequences**. The MASTER_DRIVER spec (§) remains authoritative for *what* the
system does; ADRs capture *why* a particular implementation path was chosen among alternatives.

An ADR is warranted when a choice: (a) touches a §1 invariant or the risk engine, (b) deviates
from the §2 reference stack, (c) sets or changes a §4 default, or (d) would be expensive to
reverse. Use `superpowers:brainstorming` to explore the option space first, then record the
outcome here.

## Consequences

- Decisions are traceable and reviewable; the "why" survives context resets.
- A small ongoing cost: significant changes carry an ADR. This is intentional friction around
  decisions that affect money or invariants.
- ADRs are append-only in spirit: supersede with a new ADR rather than rewriting history.
