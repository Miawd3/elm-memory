Title: Zephyr Project Hub
Scope: Defines the synthetic Zephyr snapshot service for holdout evaluation.
Tags: zephyr, snapshots, networking, fixture
Related files: NETWORK.md, ZZZ_TAIL_CANARY.md
Last updated: 2026-08-27
Status: active
Summary: Zephyr exposes loopback health probes and writes immutable snapshots.

# Current State

Zephyr monitors its local writer and emits immutable snapshot files.

# Constraint

Network and naming values must be recovered from accepted Zephyr documents.
