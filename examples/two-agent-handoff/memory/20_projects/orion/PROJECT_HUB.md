Title: Orion Example Project Hub
Scope: Defines the synthetic Orion telemetry gateway used in the cross-agent demo.
Tags: example, orion, gateway
Related files: ACTIVE_CONTEXT.md, DECISIONS.md
Last updated: 2026-08-25
Status: active
Summary: Orion writes Aurora telemetry to PostgreSQL and keeps all timestamps in UTC.

# Current State

The Aurora gateway writes verified telemetry batches to PostgreSQL 17.

# Constraint

Persisted timestamps use UTC and application logs use structured JSON.

# Handoff

The next agent should add parser regression tests before changing the storage design.
