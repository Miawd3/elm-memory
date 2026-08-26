Title: Orion Decisions
Scope: Stores accepted technical decisions for the synthetic Orion project.
Tags: orion, decisions, fixture
Related files: PROJECT_HUB.md, ACTIVE_CONTEXT.md
Last updated: 2026-08-25
Status: active
Summary: Accepted Orion choices cover storage, time, logging, and retrieval boundaries.

# ODR-001 — PostgreSQL storage

Status: accepted

Orion uses PostgreSQL 17 for durable telemetry records.

# ODR-002 — UTC time

Status: accepted

Persisted timestamps are normalized to UTC before storage.

# ODR-003 — Structured logs

Status: accepted

Application logs use newline-delimited JSON with stable event names.

# ODR-004 — Deterministic retrieval first

Status: accepted

The baseline uses SQLite FTS5 without mandatory embeddings.
