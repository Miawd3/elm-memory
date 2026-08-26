# Phase 3 — Governed Memory Contract

Status: implemented canonical format v1 (`elm-memory` 0.4.0.dev0)

Date: 2026-08-26

## 1. Boundary

Phase 3 adds durable proposals, reference-only evidence metadata, ratified
claims, lifecycle events, temporal history, contradictions, deletion tombstones,
and explicit crash recovery. It does not add raw evidence storage, automatic
fact extraction, authentication, MCP, embeddings, or model summarization.

Markdown and canonical JSON remain the source of truth. Every SQLite table added
in this phase is a rebuildable projection.

## 2. Canonical locations

```text
<root>/
  01_inbox/elm_proposals/<project>/proposal_<uuid4>.json
  01_inbox/elm_transactions/transaction_<uuid4>.json
  20_projects/<project>/CLAIMS/claim_<uuid4>.md
  30_agent_logs/elm_events/YYYY/MM/DD/event_<uuid4>.json
  30_agent_logs/elm_tombstones/<item-id>.json
  40_sources/elm_evidence/metadata/evidence_<uuid4>.json
  backups/elm-governance/<transaction-id>/...       # temporary recovery only
  .elm/index.sqlite                                 # disposable projection
```

All canonical JSON records contain `format_version: 1` and an exact
`record_type`. A client that encounters a newer or malformed format refuses the
operation instead of guessing or rewriting it.

## 3. Record contract

### Evidence reference

An evidence record contains a stable ID, project, kind, source URI, capture
time, content SHA-256, optional excerpt SHA-256, sensitivity, actor label, and
`retention: reference_only`. Allowed source schemes are `repo://`, `elm://`,
`http://`, `https://`, and `urn:`. No command imports source bytes.

### Proposal

A proposal is immutable and contains:

- project and stable proposal ID;
- subject, predicate, and object;
- proposed and valid-from timestamps;
- actor and requested authority;
- sensitivity;
- zero or more same-project evidence IDs;
- zero or more source references ending in `@sha256:<digest>`;
- optional rationale.

Proposal status is derived from immutable events. It is not edited into the
proposal file.

### Claim

An accepted claim is canonical UTF-8 Markdown with standard ELM metadata plus:

- stable claim and originating proposal IDs;
- project, subject, predicate, and object;
- accepted authority;
- valid-from and optional valid-to timestamps;
- recorded-at and transitioned-at timestamps;
- accepted, disputed, or superseded status;
- supersedes/superseded-by links;
- evidence IDs, source references, sensitivity, actor, and rationale.

The allowed accepted authorities are `user_ratified`,
`ratified_project_decision`, and `verified_repository_state`. Candidate
authorities such as `agent_proposal` cannot be used to accept a claim.

### Event and tombstone

An event records a lifecycle transition, actor label, time, transaction ID,
affected IDs, reason or authority where applicable, and before/after hashes. It
does not copy the source evidence payload.

A tombstone records only the deleted item ID/type/project, deletion time, actor,
reason code, and prior SHA-256. It intentionally cannot reconstruct deleted
claim content.

## 4. Lifecycle

```text
proposal: pending ──accept──────────────> accepted claim
                  ├─reject──────────────> rejected (terminal)
                  └─defer───────────────> deferred (terminal)

claim:    accepted ──dispute────────────> disputed
         accepted + pending proposal
                  ──supersede───────────> old superseded + new accepted

proposal | claim | evidence ──delete────> absent active record + tombstone
```

Acceptance and supersession are explicit single-writer operations. No search,
sync, rebuild, context, or inference path promotes a proposal. Supersession is
allowed only when the old claim and new proposal have the same project, subject,
and predicate.

`actor` is an audit label supplied by the caller, not authenticated identity.
The `sensitivity` field is a governance label, not encryption or access control.

## 5. Transaction and recovery semantics

Every multi-file lifecycle mutation runs under the ELM writer lock:

1. Validate all inputs and calculate the complete change set.
2. Back up only existing target files under the transaction backup directory.
3. Atomically create a `prepared` canonical transaction journal containing
   target paths and before/after hashes.
4. Apply atomic writes/deletes.
5. Atomically replace the journal with `state: committed`. This state change is
   the commit point.
6. Remove temporary backups and then the journal; keep the committed journal
   and report retained paths when cleanup cannot finish.

An existing immutable record is created with exclusive filesystem semantics and
is never silently replaced. If a process stops before the commit point,
`doctor` reports `incomplete_transaction`. Neither `sync` nor `rebuild` performs
implicit recovery.

While any transaction journal remains, governed read commands fail closed
instead of using a partially refreshed claim projection. A new lifecycle
mutation is also refused until recovery is explicitly completed.

`elm recover --dry-run` distinguishes `rollback` from
`finish_commit_cleanup`. For a `prepared` journal, `--apply` restores the
pre-transaction state only when current target hashes still match the recorded
interrupted write. For a `committed` journal, it verifies every final target
hash and completes backup cleanup without reverting the committed state. An
unexpected manual edit blocks recovery rather than being overwritten.

## 6. Temporal and retrieval semantics

`valid_from`/`valid_to` describe when a claim is true in the project domain.
`recorded_at` and lifecycle event times describe when ELM knew or changed it.

`elm history --valid-at T` selects the domain-time slice. `--recorded-at T`
reconstructs the state known at that recorded time, including the pre-transition
status of a later superseded claim. `--include-deleted` adds tombstone metadata.

Ordinary search, context, outline, read, and related return only claims that are:

- accepted;
- effective at the current time;
- not expired, disputed, superseded, or tombstoned;
- permitted by the normal archive/project/namespace policy.

`--include-history` is an explicit opt-in for historical claim documents. Direct
claim IDs and links cannot bypass the same policy.

Two overlapping accepted claims with the same project, subject, and predicate
but different objects are reported as a contradiction. `doctor` reports it and
`context` labels the affected source `accepted_conflicting_memory`; ELM does not
silently choose a winner.

## 7. Derived schema

Index schema version 2 adds disposable projections for proposals, evidence
references, claims, events, and tombstones. `sync` and `rebuild` repopulate these
tables from canonical files. A v1-to-v2 index migration is transactional; a
failed migration rolls back without publishing a partial schema.

Deleting `.elm/index.sqlite` and rebuilding must preserve every canonical ID,
claim status, lifecycle event, tombstone, and current/history retrieval result.

## 8. Operator workflow

```bash
elm evidence add ...
elm propose ...
elm proposals list --project PROJECT --status pending

# only after explicit ratification
elm accept PROPOSAL_ID --actor human:reviewer --authority user_ratified

elm history --project PROJECT
elm doctor --no-sync --json
```

Changing an accepted fact requires a new proposal followed by:

```bash
elm supersede OLD_CLAIM_ID NEW_PROPOSAL_ID \
  --actor human:reviewer --authority ratified_project_decision
```

Do not edit claim lifecycle metadata by hand. Use `reject`, `defer`, `dispute`,
`supersede`, or `delete` so event and recovery state remain coherent.

## 9. Acceptance invariants

- No accepted state exists only in SQLite.
- Delete-and-rebuild preserves stable claim identity and lifecycle state.
- Agent inference cannot auto-accept a proposal.
- Ordinary retrieval never returns stale or future-effective claim state.
- Direct IDs do not bypass current/history policy.
- Concurrent acceptance produces one terminal transition.
- Contradictions remain visible and are never auto-resolved.
- Deletion removes active canonical and derived content and retains only a
  metadata tombstone.
- Reference-only evidence records never contain raw source payloads.
- Interrupted writes require explicit, hash-guarded recovery.

The executable evidence for these invariants lives in
`tests/test_governance.py`, `tests/test_migrations.py`,
`tests/test_concurrency.py`, and `benchmarks/run_governance_demo.py`.
