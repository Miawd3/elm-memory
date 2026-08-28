# Phase 6B — Autonomous memory maintenance

Status: Phase 6B.1 implemented and locally validated; hosted validation pending

Date: 2026-08-27

## 1. Product decision

Phase 6B keeps routine memory maintenance inside the AI workflow. It does not
restore per-item human approval and does not grant an agent stronger epistemic
authority. Every autonomously maintained claim remains `agent_curated`, active
but unverified, and subordinate to current user instructions, verified
repository state, and stronger governed memory.

The phase is split because expiry, source-backed replacement, and compaction
have different proofs and failure modes:

1. **6B.1 — bounded validity leases:** deterministic expiry and renewal without
   a new MCP tool;
2. **6B.2 — source-verified replacement:** compare-and-swap supersession of an
   active `agent_curated` claim only after a configured source verifier proves
   the replacement's current digest;
3. **6B.3 — logical compaction:** bounded active-state consolidation while
   preserving canonical history and provenance.

Only 6B.1 is in the current implementation gate. Later slices must not weaken
its authority, replay, or history guarantees.

## 2. Phase 6B.1 surface

The MCP surfaces remain unchanged:

- read-only: exactly seven tools;
- proposal-only: exactly ten tools;
- autonomous: exactly eight tools.

`remember_memory` gains an optional `valid_to` timestamp. The autonomous server
has a standing default and maximum TTL policy. When the caller omits `valid_to`,
ELM deterministically derives it from `valid_from` plus the configured default
TTL. The effective timestamp is bound into the immutable submission digest, so
replay cannot silently change the lease after a restart or policy change.

The canonical proposal-v2 format remains readable and is still used by the
proposal-only profile. Autonomous lifecycle submissions use proposal-v3, which
adds the effective `valid_to` value without changing the authority model.

## 3. Lifecycle rules

Under the writer lock, a 6B.1 activation must satisfy all existing Phase 6A
checks plus these temporal rules:

- `valid_to` is strictly later than `valid_from`;
- the interval does not exceed the operator-configured maximum TTL;
- a candidate whose effective interval is already expired is deferred rather
  than briefly activated;
- a future-effective candidate remains deferred;
- active-memory quotas count claims that are actually current at the canonical
  check time, including legacy non-expiring claims and leased claims whose
  `valid_to` is still in the future;
- expired claims no longer block a current renewal or consume active quota;
- expiry is a temporal read-policy result, not a destructive background write.

Renewal is a new `remember_memory` submission after the previous lease expires.
The old claim, proposal, evidence references, and event remain available through
history. No timer service, scheduler, or database daemon is required.

## 4. Safety boundary

Phase 6B.1 does not allow an agent to:

- supersede, dispute, delete, shorten, or extend an existing claim in place;
- replace a stronger or conflicting current value;
- select `user_ratified`, `verified_repository_state`, or another stronger
  authority;
- claim that an unverified URI/hash pair proves repository freshness;
- erase expired history or compact canonical records;
- mutate policy, root identity, synchronization state, or arbitrary Markdown.

The index remains disposable. Rebuild must recover every proposal-v2,
proposal-v3, claim, lease boundary, and authority label from canonical files.

## 5. Phase 6B.1 acceptance contract

Acceptance requires:

- proposal-only v2 compatibility and autonomous v3 digest verification;
- deterministic default TTL and bounded explicit `valid_to`;
- rejection before canonical mutation for malformed or over-limit intervals;
- stable replay of active, expired, and deferred lifecycle outcomes;
- ordinary retrieval hides expired memory while explicit history preserves it;
- an expired claim frees active quota and does not block renewal;
- the exact 7/10/8 tool surfaces remain unchanged;
- concurrent writes cannot bypass lifecycle or quota checks;
- rebuild, doctor, repeat sync, and SQLite integrity remain clean; and
- the full deterministic benchmark and static evaluation suite still passes.

## 6. Deferred Phase 6B.2 and 6B.3 gates

Autonomous supersession is not safe merely because a candidate supplies a newer
timestamp or a different hash. Phase 6B.2 requires an operator-configured source
root/verifier, a current digest check performed by ELM, an expected-current
claim ID and content hash, same-project/subject/predicate enforcement, a target
whose authority is exactly `agent_curated`, and one atomic old/new/event
transaction. Any stale precondition, stronger claim, unresolved conflict, or
unverifiable source must defer.

Compaction in Phase 6B.3 is logical rather than destructive by default. It may
reduce active retrieval state or produce a bounded derived snapshot, but it must
not erase canonical claims, proposals, events, evidence references, or the path
needed to explain and reverse a consolidation. Model-generated summaries remain
outside the deterministic baseline until a measured task-level failure justifies
them.
