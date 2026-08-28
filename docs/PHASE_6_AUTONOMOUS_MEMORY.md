# Phase 6A — Autonomous agent memory

Status: implemented, hosted-validated, and merged

Date: 2026-08-27

## 1. Product decision

Routine human approval is not part of the normal ELM memory loop. A person may
inspect canonical Markdown or history on demand, but an AI agent should be able
to preserve useful continuity without interrupting the user for every write.

Phase 5B signed per-operation human ratification is therefore removed from the
active roadmap. Its design remains historical security research, not a
prerequisite for autonomous memory.

Autonomy does not make an agent's inference verified truth. Phase 6A separates
write permission from epistemic authority: the operator enables one bounded
profile once, and every resulting memory is permanently labeled
`agent_curated`.

## 2. Deployment boundary

The process default remains the exact seven-tool read-only MCP surface. The
existing Phase 5A `proposal-only` profile remains the exact ten-tool candidate
surface. A separate explicit `autonomous` profile exposes the seven read tools
plus exactly one write tool:

```text
remember_memory
```

Starting this profile requires:

- an existing immutable ELM root identity;
- one or more existing, non-symlink project allowlist entries;
- fixed proposal/request limits;
- fixed active-memory project and root quotas; and
- a process-local request-rate limit.

Enabling the profile is the operator's standing authorization for bounded
agent memory in those projects. It is not authentication, multi-user access
control, or evidence that a person reviewed an individual memory.

## 3. Authority model

`remember_memory` writes an active canonical claim with authority
`agent_curated`. Retrieval exposes that authority as `agent_curated_memory` and
quotes its content as untrusted data.

The effective precedence remains:

1. current explicit user instruction;
2. verified current repository state;
3. user-ratified or repository-verified durable memory;
4. agent-curated active memory;
5. pending, disputed, historical, or otherwise provisional memory;
6. current model inference.

Search orders agent-curated claim results after otherwise comparable
non-agent sources. This is a provenance rule, not a guarantee that higher
authority material is always factually correct.

## 4. Write lifecycle

The tool accepts the same closed, bounded submission identity and reference-only
evidence shape as Phase 5A. Sensitivity is fixed to `normal`; raw evidence bytes,
credentials, private keys, and arbitrary paths are not accepted.

The lifecycle is deterministic:

1. Normalize the closed request and compute its submission digest.
2. Atomically create or replay the immutable proposal/evidence bundle.
3. Under the canonical writer lock, compare active claims with the same project,
   subject, predicate, and overlapping validity.
4. Apply exactly one outcome:
   - no current value: create an `agent_curated` claim and acceptance event;
   - same current value: reject the candidate as a duplicate and reuse the
     existing stronger/current claim;
   - different current value: defer the candidate as contradicted and preserve
     the active claim unchanged;
   - quota exhausted: defer the candidate with `quota_exceeded`;
   - future-dated candidate: defer it as outside the current-memory scope.
5. Refresh the disposable projection and return canonical commit state
   separately from projection health. The receipt includes the checked
   canonical/projected governance digests and explicitly describes them as a
   snapshot, not a linearizable postcondition.

The proposal and activation are individually atomic canonical transactions.
If the process stops between them, replaying the same submission resumes the
pending proposal rather than duplicating it. Concurrent exact submissions
activate one claim; concurrent conflicting submissions leave one active claim
and one deferred candidate.

A terminal replay reports active success only when the original terminal event
and current claim are both stamped `mcp:autonomous` / `agent_curated`. Disputed,
superseded, expired, future, manually accepted, rejected, or deferred state is
returned as an explicit non-active outcome and is never described as a fresh
autonomous activation.

## 5. Safety and quality invariants

- Markdown and canonical governance records remain durable truth; SQLite remains
  disposable.
- Neither indexing nor read calls create autonomous memory.
- `agent_curated` is never emitted by the proposal-only profile or accepted as a
  CLI ratification authority.
- Agent memory cannot silently replace, supersede, dispute, or delete an active
  claim.
- Stronger/current exact duplicates are reused rather than shadowed.
- Conflicts and quota exhaustion create no contradictory active claim.
- Ordinary reads continue to exclude backups, archives, disputed, superseded,
  future, expired, and deleted claims.
- Exact `read`, `outline`, and `related` results preserve governed claim ID,
  raw claim authority, a normalized authority label, and the untrusted-data
  content role.
- In mutation-capable MCP profiles, indexed search, context, read, related, and
  stats acquire the canonical writer lock and verify root identity plus
  canonical/projection freshness in the same CLI operation as the query. They
  fail closed while those states differ; canonical `history` and diagnostic
  `status` remain available for repair.
- This serialized path intentionally trades mutation-profile parallel read
  throughput for a simple Phase 6A correctness proof. A future immutable epoch
  protocol may optimize it only if it preserves the same freshness boundary.
- The autonomous MCP surface contains no delete, recovery, sync, rebuild,
  identity, policy, arbitrary-file, or accepted-authority selector.
- Every active agent-memory write is idempotent, bounded, project-scoped,
  rate-limited, source-linked when references exist, and reconstructable after
  deleting SQLite.

## 6. Explicit non-goals

Phase 6A does not implement:

- automatic conflict resolution or supersession;
- autonomous deletion, forgetting, consolidation, or summaries;
- model confidence scores presented as calibrated probabilities;
- secret classification or encryption;
- raw chat ingestion or raw evidence storage;
- cross-user authentication;
- embeddings, graph databases, hosted services, or a custom memory model; or
- the signed Phase 5B approval-grant design.

## 7. Acceptance contract

The local acceptance suite proves:

- default read-only remains seven tools;
- proposal-only remains ten tools;
- autonomous mode is exactly eight tools;
- policy, root identity, and project scope are mandatory;
- exact replay produces one proposal, claim, and event;
- replay after dispute, supersession, expiry, future dating, manual acceptance,
  rejection, or deferral never reports a false active autonomous claim;
- a stronger exact claim is reused;
- conflict and quota paths do not create active contradictions;
- project/root quotas remain bounded under concurrent and cross-project writes;
- autonomous request rate limiting fails before an excess canonical write;
- interrupted two-stage writes resume safely;
- same-submission and conflicting concurrent writes remain deterministic;
- restricted autonomous submissions fail before canonical mutation;
- rebuild preserves claim identity and `agent_curated` authority;
- search-to-read preserves distinct agent-curated and stronger governed
  provenance;
- mutation-capable indexed reads fail closed after a canonical/projection split
  until operator sync or rebuild repairs the projection, including a
  deterministic writer-at-the-old-preflight-boundary race for both profiles and
  all five indexed-read handlers;
- bounded context labels the source `agent_curated_memory`; and
- `doctor` and SQLite integrity remain clean.

## 8. Next slice

Phase 6B adds autonomous maintenance, not human ratification. Phase 6B.1 begins
with digest-bound validity leases and non-destructive expiry while preserving the
exact eight-tool autonomous surface. Source-verified compare-and-swap
supersession and logical compaction remain separately gated in
[PHASE_6B_AUTONOMOUS_MAINTENANCE.md](PHASE_6B_AUTONOMOUS_MAINTENANCE.md).

Real-world task validation remains a parallel evidence track. It should measure
whether autonomous writes improve later task correctness and token use without
increasing stale-memory or contradiction rates.
