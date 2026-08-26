Title: Orion Project Hub
Scope: Defines the synthetic Orion gateway project and its current architecture.
Tags: orion, gateway, fixture
Related files: ACTIVE_CONTEXT.md, DECISIONS.md
Last updated: 2026-08-25
Status: active
Summary: Orion is a deterministic telemetry gateway used only for public ELM tests.

# Current State

The Aurora gateway accepts telemetry batches and writes durable records to PostgreSQL 17.
All persisted timestamps use UTC and all application logs use structured JSON.

# Constraints

- The fixture must not require network access.
- The implementation baseline uses Python 3.11 or newer.
- Semantic embeddings are outside the Orion baseline.

# Delivery

The next milestone is a parser regression suite followed by a bounded-context experiment.
