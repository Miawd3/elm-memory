# Phase 6B — Autonomous memory maintenance

Status: Phase 6B.1 and 6B.2 implemented and locally validated; hosted validation pending

Date: 2026-08-28

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

Phases 6B.1 and 6B.2 are implemented. Phase 6B.3 must not weaken their
authority, replay, source-containment, or history guarantees.

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

## 6. Phase 6B.2 source-verified compare-and-swap

Autonomous supersession is not safe merely because a candidate supplies a newer
timestamp or a different hash. Phase 6B.2 therefore extends the existing
`remember_memory` request instead of adding a ninth autonomous tool. A CAS
request supplies both `supersedes_claim_id` and `expected_claim_sha256`; omitting
both preserves proposal-v3 append behavior. Supplying only one fails before a
canonical write.

The operator configures zero or more named roots with
`--source-root ALIAS=/absolute/repository/path`. CAS remains unavailable when no
matching root is configured. A source reference has the form
`repo://ALIAS/relative/path@sha256:DIGEST`. ELM resolves only plain relative
paths under the named root, rejects traversal, URI credentials, queries,
fragments, percent-encoded paths, and symlink components, reads the current file
bytes, and compares their SHA-256 digest under the canonical writer lock. At
least one verified locator must already be present on the target claim, so a CAS
cannot switch to an unrelated evidence path while replacing memory.

Under the writer lock, all of these conditions are mandatory:

- target ID and canonical Markdown hash equal the digest-bound v4 preconditions;
- the target is the sole current lineage head for the same
  project/subject/predicate;
- its status is `accepted` and its authority is exactly `agent_curated`;
- no stronger, disputed, superseded, expired, or competing current claim is
  eligible;
- the successor begins after the target's `valid_from` and obeys the existing
  lease limits; and
- every configured `repo://` reference resolves within its root and matches the
  current file bytes.

Success performs one journaled old-claim/new-claim/event transaction. A changed
object is reported as `superseded`; the same object is reported as `renewed`.
Both create a successor claim and preserve the earlier claim as `superseded`;
neither extends or reopens history in place. A stale target or race becomes a
terminal deferred proposal, and concurrent CAS attempts against the same hash
have exactly one winner. Replay returns the recorded transition without
pretending the external source was rechecked.

Source verification proves only that the configured file had the stated bytes
at the transition check. It does not prove that an agent's subject, predicate,
or object is a semantically correct interpretation of those bytes. The
successor therefore remains `agent_curated`; this path cannot grant
`verified_repository_state`, `user_ratified`, or another stronger authority.
External repositories do not share ELM's lock, so later source drift is possible
and is represented honestly by `verified_at_transition`, not a permanent
freshness claim.

Proposal-v3 remains readable for leased append operations. CAS uses proposal-v4,
which digest-binds the effective `valid_to`, target claim ID, expected canonical
claim hash, source references, and the rest of the normalized payload. Derived
SQLite schema v5 projects the two CAS preconditions and remains fully
rebuildable from canonical files.

## 7. Phase 6B.2 acceptance contract

Acceptance requires:

- the exact 7/10/8 MCP tool surfaces remain unchanged;
- proposal-v2/v3 compatibility and proposal-v4 digest/replay verification;
- successful source-backed replacement and same-value lease renewal;
- stale target/hash, stronger authority, conflicting head, missing root,
  digest mismatch, traversal, and partial CAS input fail closed;
- source mismatch detected before submission leaves no canonical candidate;
- a post-submission verification race defers rather than mutating a stale claim;
- concurrent CAS produces one successor and no active contradiction;
- the old claim, new claim, proposal, and event survive rebuild and history;
- schema v0-v4 migrations reach v5 without losing projected rows; and
- full tests, benchmark gates, doctor, repeat sync, and SQLite integrity remain
  clean.

## 8. Deferred Phase 6B.3 gate

Compaction in Phase 6B.3 is logical rather than destructive by default. It may
reduce active retrieval state or produce a bounded derived snapshot, but it must
not erase canonical claims, proposals, events, evidence references, or the path
needed to explain and reverse a consolidation. Model-generated summaries remain
outside the deterministic baseline until a measured task-level failure justifies
them.
