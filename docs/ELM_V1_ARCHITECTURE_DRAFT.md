# ELM v1 — Corrected Architecture and Implementation Plan

Status: active staged architecture; Phases 1–4 implemented and verified; Phase 5A ratified for implementation

Date: 2026-08-26

Project: ELM — External Local Memory

Target: open-source, local-first, model-agnostic project memory for coding agents

Revises: `ELM_V1_MULTI_AGENT_IMPLEMENTATION_HANDOFF.md` (local research handoff)

## 1. Executive decision

ELM should continue, but the public project must be narrower and more internally consistent than the original v1 handoff.

Recommended definition:

> **ELM is a local, inspectable context compiler and governed project-memory layer for coding agents.**

It helps heterogeneous agents recover bounded, source-linked project context without sharing full chat histories or depending on a hosted memory service.

The first public release remains a clean, tested deterministic core with
governed claims and a read-only interoperability adapter. Authenticated actors,
raw evidence payloads, and controlled MCP mutation remain later designs rather
than implicit capabilities.

## 2. Authority and status

### User-canon constraints

- ELM is intended to become an open GitHub project.
- The immediate goal is to make it useful, testable, and worth sharing.
- Monetization is not a release requirement.
- Existing useful v0 behavior should be preserved.
- The system should remain local-first and usable by different coding agents.

### Verified current implementation facts

- Canonical project knowledge is stored in Markdown.
- `.elm/index.sqlite` is disposable and rebuildable.
- The current CLI provides `sync`, `rebuild`, `status`, `search`, `context`, `outline`, `read`, `related`, `history`, `stats`, `doctor`, governed lifecycle commands, and explicit `ids assign` mutation.
- Retrieval uses SQLite FTS5 and section-level reads.
- Archive, project, and namespace policy applies to search, outline, read, and related, including direct-ID reads.
- SQLite integer IDs remain compatibility references; optional document UUIDs and derived section keys provide rebuild-stable public identity.
- The disposable index has an explicit schema version and an in-place migration from the unversioned Phase 0 schema.
- The package has unit, integration, migration, concurrency, policy, and CLI-contract tests plus a sanitized benchmark.
- The repository is a sanitized private GitHub pre-release and contains no personal ELM snapshot or private bootstrap artifact.
- Phase 2 is complete: bounded deterministic context packets, privacy-minimized disposable retrieval traces, retention cleanup, and comparative evaluation are implemented.
- Phase 3 is merged and live-validated: immutable proposals, reference-only evidence metadata, canonical claim Markdown, explicit lifecycle transitions, valid/recorded-time history, contradictions, tombstones, and recoverable transactions.
- Phase 4 is implemented and host-validated: a seven-tool read-only MCP adapter delegates to the CLI JSON contract, reports exact index readiness, and returns equivalent stable source identities through Antigravity/Gemini and Codex.
- Raw evidence snapshots, controlled MCP mutation, embeddings, and model summarization remain deferred.

### Provisional design

The exact implemented Phase 1–4 contracts are repository truth and are linked
from their phase documents. Later roadmap phases remain proposed until explicit
authorization and implementation validation.

## 3. Architectural problems repaired

| Previous problem | Corrected decision |
|---|---|
| Durable claims/events proposed inside disposable SQLite | SQLite contains projections only; unique durable records live in canonical UTF-8 files. |
| Append-only raw evidence conflicted with deletion | Audit events contain metadata and hashes by default; raw evidence is opt-in, separately retained, and deletable. |
| `agent-private` was called a security boundary without trusted identity | Initial scopes are namespaces and policy filters; real privacy claims require an authenticated daemon or OS-level isolation. |
| Stable IDs were proposed for every object and every Markdown heading | Persist IDs only for durable entities; derive ordinary section locators and add explicit anchors only when needed. |
| Multiple agents could write without a concurrency contract | ELM uses one canonical writer, atomic file replacement, short SQLite transactions, busy timeout, and a cross-platform writer lock. |
| v1 combined identity, ACL, evidence, claims, temporal logic, MCP, and evaluation in one release | Work is split into independently useful, testable releases. |
| Multi-agent memory was treated as unique positioning | Public differentiation is local inspectability, deterministic retrieval, human governance, exact sources, and no mandatory model/service. |

## 4. Fixed invariants

1. **Accepted project truth remains human-readable Markdown.**
2. **SQLite is disposable.** Deleting `.elm/index.sqlite` may reduce performance, but must not delete unique project knowledge, accepted claims, proposals, evidence references, or deletion state.
3. **Indexing is read-only with respect to canonical files.** `sync`, `search`, and `rebuild` never add IDs or rewrite Markdown.
4. **Canonical mutation is explicit.** Commands that change durable state use clear mutation verbs, support dry-run where useful, and create recoverable backups for migrations.
5. **Evidence is data, not instruction.** Retrieved text never gains policy authority because it resembles a prompt.
6. **Evidence is not truth.** A source may support a candidate claim but cannot silently become accepted memory.
7. **Repository state outranks memory for current implementation facts.** Current explicit user instruction outranks both.
8. **Normal context is bounded.** Growth of historical ELM data must not linearly grow the packet sent to an agent.
9. **One canonical writer.** Multiple agents may retrieve and propose concurrently; accepted-memory finalization is serialized.
10. **Compatibility is intentional.** Existing v0 CLI behavior remains available until a documented deprecation and migration path exists.

## 5. Corrected storage architecture

```text
Agent adapters: Codex / Claude / Gemini / Cursor / generic
                              |
                              v
                       CLI / MCP adapter
                              |
                              v
                         ELM core
        retrieval | context packing | proposals | validation
                  |           |             |
        +---------+-----------+-------------+---------+
        |                                               |
        v                                               v
Canonical UTF-8 files                           Disposable runtime state
Markdown accepted state                        .elm/index.sqlite
Markdown claim records                         .elm/runtime.sqlite
proposal/evidence metadata                     bounded retrieval traces
audit metadata events                          locks and caches
```

### 5.1 Canonical accepted state

Existing files remain authoritative:

```text
PROJECT_HUB.md
ACTIVE_CONTEXT.md
DECISIONS.md
CONSTRAINTS.md
OPEN_QUESTIONS.md
REFERENCES.md
PROGRESS_LOG.md
```

Structured claims are deferred until their lifecycle is implemented. When introduced, accepted claims use human-readable Markdown records rather than SQLite-only rows:

```text
20_projects/<project>/CLAIMS/
  claim_<uuid>.md
```

Each claim record uses a small line-based metadata contract compatible with deterministic parsing:

```markdown
Title: Backend database claim
ELM ID: claim_550e8400-e29b-41d4-a716-446655440000
Subject: backend
Predicate: uses_database
Object: PostgreSQL
Status: accepted
Authority: ratified_project_decision
Valid from: 2026-08-25T00:00:00Z
Valid to:
Recorded at: 2026-08-25T20:00:00Z
Source refs: doc_<uuid>#architecture@sha256:<hash>

# Rationale

Accepted by the project owner after repository verification.
```

The exact claim format will be frozen only after parser tests and a round-trip fixture exist.

### 5.2 Proposals

Agents do not append concurrently to one shared file. Each proposal is an independent immutable file created through a temporary file plus atomic rename:

```text
01_inbox/elm_proposals/<project>/<proposal_id>.json
```

Proposal records may contain a short candidate statement and source references, but not arbitrary raw chats or terminal dumps.

Ratification is a single-writer operation:

```text
proposal -> validate -> accept/reject/defer
                         |
                         +-> canonical Markdown update
                         +-> metadata-only audit event
```

### 5.3 Evidence

Default evidence mode is **reference-only**:

```text
40_sources/elm_evidence/metadata/<evidence_id>.json
40_sources/elm_evidence/snapshots/<evidence_id>/...  # explicit opt-in only
```

```json
{
  "evidence_id": "evidence_<uuid>",
  "kind": "repository_file",
  "source_uri": "repo://src/config.py",
  "captured_at": "2026-08-25T20:00:00Z",
  "content_sha256": "...",
  "excerpt_sha256": "...",
  "sensitivity": "normal"
}
```

Rules:

- Raw chat exports are not stored by default.
- Full terminal output is not stored by default.
- Secrets and instruction-shaped payloads are blocked from durable evidence.
- An optional snapshot requires an explicit import action and a configured retention class.
- Large or sensitive snapshots live outside ordinary retrieval and never enter a context packet without an explicit source read.

### 5.4 Audit events and traces are different

Audit events record durable workflow facts such as proposal accepted, claim superseded, or item deleted. They contain IDs, timestamps, actor labels, action, and hashes—not the sensitive payload itself.

```text
30_agent_logs/elm_events/<year>/<month>/<day>/<event_id>.json
```

Retrieval traces are operational telemetry. They are disposable by default, have a retention window, and may be explicitly exported into a reviewed evaluation dataset.

This prevents ordinary debugging telemetry from silently becoming permanent user memory.

## 6. Identity model

### 6.1 Durable IDs

Persist IDs only for entities whose identity must survive rebuild or movement:

- workspace;
- project;
- document when referenced durably;
- claim;
- proposal;
- evidence reference;
- audit event.

Use prefixed UUIDv4 strings for the first public implementation. UUIDv4 is available across supported Python versions and avoids adding a ULID/UUIDv7 dependency.

Examples:

```text
workspace_550e8400-e29b-41d4-a716-446655440000
project_9ad1...
doc_42a7...
claim_d603...
```

### 6.2 Document IDs

Document IDs are assigned only by an explicit command:

```text
elm ids assign --root <path> --dry-run
elm ids assign --root <path> --apply
```

The command adds an `ELM ID:` metadata field, makes targeted backups, and never runs implicitly during `sync` or `rebuild`.

Existing integer database IDs remain internal row keys. CLI JSON returns both during the compatibility period:

```json
{
  "document_id": 512,
  "document_uid": "doc_42a7..."
}
```

### 6.3 Section locators

Do not insert comments into every heading.

Ordinary section identity is a rebuild-stable derived locator:

```text
section_namespace = document_uid if present else "path:" + normalized_relative_path
section_key = UUIDv5(section_namespace, normalized_heading_path + occurrence)
```

The locator is stable across SQLite rebuilds as long as its namespace and heading path remain stable. A path-bound locator is reported as such and changes when its document moves. Any section used by durable evidence must first receive a document UID. A durable evidence reference additionally stores the content hash. A renamed or moved heading is treated as a changed source unless an explicit anchor is assigned during a deliberate migration.

This gives rebuild stability without mass-editing the corpus.

### 6.4 Runtime identities

Agent, session, and retrieval-trace IDs are created at runtime and do not need Markdown annotations. They are not database-generated identity that must be reconstructed after rebuild.

## 7. Scopes and security semantics

### 7.1 Initial public semantics

The first implementation supports deterministic namespaces:

```text
workspace
project
shared
```

`agent` and `session` labels may be recorded for provenance, but `agent-private` and `session-private` must not be advertised as secure isolation in a shared local process.

### 7.2 Filter coverage

Scope and archive policy must apply to every read path:

- search;
- context;
- read;
- outline;
- related;
- history;
- MCP resources and tools.

A hidden result must not be recoverable merely by guessing its section or document ID.

### 7.3 Future secure mode

Real private scopes require at least one trusted boundary:

- separate OS users and filesystem permissions; or
- a local daemon that authenticates clients and binds permissions to a trusted principal; or
- a remote authenticated server, which is outside the initial local-only product.

Until then, scope filtering is a governance and retrieval rule, not an access-control guarantee.

## 8. Deletion and retention

Deletion has three explicit levels:

1. **Remove from active retrieval:** update or remove the canonical record and resync derived indexes.
2. **Logical historical deletion:** retain a metadata-only tombstone with item ID, timestamp, and non-sensitive hash.
3. **Best-effort physical erasure:** remove configured snapshots and derived copies, then verify absence from indexes and caches.

ELM must not promise cryptographic erasure from Git history, filesystem snapshots, backups, or third-party exports. The documentation must distinguish active deletion from complete historical erasure.

Raw evidence snapshots receive a retention class:

```text
ephemeral   -> deleted automatically after the configured window
project     -> retained until project cleanup
explicit    -> retained until an explicit deletion action
```

Metadata-only audit events can remain after payload deletion because they do not contain the deleted content.

## 9. Multi-agent concurrency

SQLite WAL alone is not a complete multi-agent write policy.

Required behavior:

- `PRAGMA busy_timeout` is configured.
- Canonical mutations acquire one cross-platform ELM writer lock.
- Lock files include PID, host, started time, and operation name.
- Stale-lock recovery is explicit and logged.
- Canonical file writes use temporary sibling files, flush, and atomic `os.replace`.
- SQLite transactions remain short.
- Read commands never hold the writer lock while a model is reasoning.
- Proposal creation uses one-file-per-proposal atomic creation and does not require editing shared Markdown.
- Ratification and migrations are single-writer operations.

The first concurrency tests use two processes and verify:

- simultaneous reads succeed;
- one sync waits or exits cleanly while another sync owns the lock;
- two proposal creations do not overwrite each other;
- ratification cannot produce two accepted versions of the same proposal;
- a killed writer leaves recoverable canonical files and a diagnosable stale lock.

## 10. Context packing

`elm context` is implemented before automatic claims extraction because it creates immediate public value using the current corpus.

Initial deterministic flow:

```text
task
  -> resolve root/workspace/project namespace
  -> load small current-state and constraint candidates
  -> FTS5 section search
  -> exclude archive and disallowed scopes
  -> deduplicate by document and heading path
  -> allocate token budget by packet class
  -> return source-linked packet
```

Initial packet classes:

```text
authority and warnings
current constraints
current project state
relevant source manifests
selected exact sections
conflicts or provisional items
```

Token allocations are configuration defaults, not universal constants. The hard invariant is that the final packet never exceeds the requested budget.

The first implementation performs no LLM summarization. If a source does not fit, it returns a manifest and locator rather than silently truncating a claim into a potentially misleading summary.

## 11. Retrieval traces

Trace schema starts small:

```json
{
  "trace_id": "trace_<uuid>",
  "recorded_at": "...",
  "query_sha256": "...",
  "query_text": null,
  "workspace_id": "...",
  "project_id": "...",
  "filters": {},
  "candidate_section_keys": [],
  "selected_section_keys": [],
  "estimated_tokens": 0,
  "latency_ms": 0,
  "fallback_used": false,
  "outcome": null
}
```

Privacy defaults:

- raw query text is off by default;
- source contents are not copied into traces;
- traces expire by policy;
- reviewed traces can be exported into an eval fixture with explicit redaction.

## 12. Schema and migration contract

Two formats are versioned separately:

1. **Index schema version:** disposable SQLite projection. An incompatible failure may fall back to rebuild.
2. **Canonical format version:** Markdown/proposal/evidence record contract. A change requires a migration, backup, dry-run report, and validation.

SQLite gains a metadata table:

```sql
CREATE TABLE elm_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Migration behavior:

- opening a newer unsupported schema fails clearly;
- derived-schema migration failure offers a safe rebuild;
- canonical migration never runs implicitly from search;
- every canonical migration is idempotent or records why it cannot be rerun;
- migration fixtures cover the immediately previous public format.

## 13. CLI and MCP boundary

CLI remains the canonical debug and automation interface.

### Read commands

```text
elm sync
elm rebuild
elm search
elm context
elm read
elm outline
elm related
elm history
elm stats
elm doctor
```

### Explicit mutation commands

```text
elm ids assign
elm propose
elm accept
elm reject
elm dispute
elm delete
elm migrate
```

The MCP adapter initially exposes read-only operations. Mutation MCP tools are deferred until canonical mutation, identity binding, validation, and concurrency tests exist.

MCP is an adapter; no storage or retrieval rule is implemented only inside MCP.

## 14. Public repository boundary

The public repository must be created from code and sanitized fixtures, not from either private bootstrap archive.

Recommended structure after incremental extraction:

```text
elm-memory/
  README.md
  LICENSE
  SECURITY.md
  CONTRIBUTING.md
  pyproject.toml
  src/elm_memory/
    cli.py
    storage.py
    markdown.py
    index.py
    retrieval.py
    diagnostics.py
  tests/
    fixtures/
    test_sync.py
    test_rebuild.py
    test_retrieval.py
    test_archive_policy.py
    test_links.py
    test_migrations.py
  benchmarks/
  examples/
    two-agent-handoff/
  docs/
```

Repository rules:

- no personal ELM snapshot;
- no raw chats, terminal logs, user paths, or credentials;
- synthetic fixture contains accepted, provisional, superseded, archived, and broken-link cases;
- CI runs on Windows and Linux;
- supported Python range is explicitly tested;
- release artifacts are built from Git tags;
- checksums and provenance apply to releases, not private user data.

The user delegated the license choice for broad use and modification. Apache-2.0
is accepted because it is permissive and includes an explicit contributor patent
grant; the repository includes the official license text and a compact NOTICE.

## 15. Implementation roadmap

### Phase 0 — Public baseline and regression protection

Goal: turn the working prototype into a clean, reproducible project without changing its memory model.

Implementation slices:

1. Create the clean repository and import only sanitized code/docs.
2. Freeze current CLI JSON contracts with fixtures.
3. Add unit tests for metadata, Markdown sections, links, archive classification, and FTS query escaping.
4. Add integration tests for sync, rebuild, search, outline, read, related, stats, and doctor.
5. Add Windows/Linux CI and Python-version matrix.
6. Convert the current five-case benchmark into a reproducible sanitized smoke suite.
7. Add at least 20 adversarial retrieval cases before calling the baseline public-ready.

Acceptance:

- clean clone installs without private files;
- all current v0 commands pass on Windows and Linux;
- deleting SQLite and rebuilding reproduces the same documents and section contents;
- backups/archive remain excluded by default;
- no secrets or personal paths exist in tracked files;
- CI and sanitized benchmark pass.

### Phase 1 — Identity, migrations, and concurrency

Goal: make future provenance and multi-agent reads safe without adding claims yet.

Implementation slices:

1. Add index schema version and migration runner.
2. Add optional document UID parsing and explicit `elm ids assign` migration.
3. Add derived rebuild-stable section keys.
4. Return public UIDs alongside legacy integer IDs.
5. Add `busy_timeout`, writer-lock implementation, atomic canonical-write utility, and concurrency tests.
6. Apply namespace/archive policy consistently to every read command.

Acceptance:

- document UIDs and section keys survive index deletion/rebuild;
- indexing never modifies Markdown;
- old numeric CLI references continue to work during the compatibility period;
- disallowed sections cannot be recovered by guessing an ID;
- concurrent reads work and concurrent writers fail or wait cleanly;
- migration dry-run and rollback fixtures pass.

### Phase 2 — Bounded context and evaluation traces

Goal: deliver the most visible user benefit before expanding the memory ontology.

Status: implementation and pre-release validation complete.

Implementation slices:

1. Add `elm context <task> --budget <tokens>`.
2. Define deterministic packet classes and allocation policy.
3. Add source locators, authority/status labels, warnings, and exact token accounting estimate.
4. Add privacy-minimized disposable traces and retention cleanup.
5. Expand evaluation to 50–100 real or carefully sanitized cases.
6. Compare no-memory, full-file, search/read, and context-pack baselines.

Acceptance:

- packet never exceeds requested estimated-token budget;
- corpus history growth does not linearly grow the packet;
- current user instruction and verified repository state cannot be overridden by stale memory;
- archive and namespace leakage remain zero in controlled tests;
- retrieval quality and task outcome are reported separately;
- traces contain no source body and no raw query by default.

### Phase 3 — Proposals, evidence references, claims, and temporal history

Goal: add governed state changes after identity and evaluation exist.

Status: implementation, cross-platform CI, package/skill installation, and live validation complete.

The frozen implemented contract is documented in
[PHASE_3_GOVERNED_MEMORY.md](PHASE_3_GOVERNED_MEMORY.md).

Implementation slices:

1. Add atomic one-file-per-proposal queue.
2. Add evidence-reference records with sensitivity and retention metadata.
3. Add accepted claim Markdown format and parser.
4. Add accept/reject/dispute/supersede lifecycle with one writer.
5. Add valid-time and recorded-time semantics.
6. Add history queries and contradiction reporting.
7. Add deletion/tombstone verification.

Acceptance:

- no accepted claim exists only in SQLite;
- rebuild preserves all accepted state and claim identity;
- inference cannot become accepted without configured authority;
- supersession preserves history without returning stale claims as current;
- deletion removes active retrieval and derived copies;
- raw evidence is absent unless explicitly imported.

### Phase 4 — Read-only MCP and heterogeneous-agent demo

Goal: prove one core works across agents.

Status: implementation, parity validation, failure-isolation testing, and a
synthetic Antigravity/Gemini-to-Codex host demonstration are complete. The
frozen boundary is documented in
[PHASE_4_READ_ONLY_MCP.md](PHASE_4_READ_ONLY_MCP.md).

Implementation slices:

1. Add a thin read-only MCP adapter.
2. Expose search, context, read, related, history, stats, and status.
3. Build a sanitized Claude-researcher to Codex-developer demo.
4. Validate tool behavior with at least two heterogeneous MCP-capable hosts.
5. Document that namespace filters are not authentication.

Acceptance:

- CLI and MCP return equivalent source identities and scope behavior;
- neither adapter contains unique memory logic;
- two hosts recover the same accepted project state;
- no mutation tool is exposed accidentally;
- MCP failure does not affect direct CLI use.

### Phase 5 — Controlled MCP mutation

Goal: expose proposals safely without letting an agent self-ratify memory.

Status: Phase 5A ratified on 2026-08-26 and active for implementation; Phase 5B
remains deferred and inactive. The detailed threat model, trust boundary, grant
schema, and acceptance matrix are in
[PHASE_5_TRUSTED_MUTATIONS.md](PHASE_5_TRUSTED_MUTATIONS.md).

Prerequisites:

- Phase 3 mutation tests pass;
- concurrency and rollback tests pass;
- Phase 5A has explicit server-side project allowlists, durable root/project
  quotas, submission idempotency, and compound canonical transactions;
- Phase 5B additionally requires trusted actor binding outside the agent's
  authority and a usable exact-operation review/ratification flow.

Phase 5A opt-in proposal tools (the process default remains read-only):

```text
propose_memory
list_memory_proposals
preview_memory_transition
```

Phase 5A has no accepted-state mutation tool. Phase 5B may later add one
`execute_approved_transition` tool that consumes a short-lived, single-use,
signed grant bound to the exact operation and canonical pre-state. It remains
disabled unless a verifier outside the agent's authority is configured.

Direct arbitrary Markdown write, deletion, recovery, synchronization, identity
migration, and trust-policy/key management are not MCP tools. MCP host prompts
and caller-supplied actor labels are defense in depth and provenance, not
authenticated ratification.

### Phase 6 — Optional semantic retrieval

Embeddings, reranking models, summaries, graph infrastructure, and learned controllers remain deferred.

Add one only when an evaluation identifies a named failure class, establishes the deterministic baseline, and shows a material task-level improvement that justifies privacy, dependency, latency, and maintenance cost.

## 16. Proposed commit sequence for the first implementation cycle

The first cycle should stop after Phase 0 unless its acceptance checks pass.

```text
01 chore: create sanitized public repository skeleton
02 test: freeze v0 parser and CLI contracts
03 test: add sync/rebuild/archive/link integration fixtures
04 refactor: extract modules without changing CLI behavior
05 ci: add Windows and Linux test matrix
06 benchmark: add sanitized retrieval and leakage cases
07 docs: add architecture, security, privacy, and contribution guides
08 release: tag the tested public baseline
```

The second cycle begins stable identity only after that tag:

```text
09 feat: add index schema version and migration framework
10 feat: parse optional document UIDs
11 feat: add explicit UID assignment migration
12 feat: add derived section keys and compatibility JSON fields
13 fix: enforce read policy on every retrieval endpoint
14 feat: add writer lock and concurrency tests
```

## 17. Highest-risk tests

These tests protect the architectural invariants rather than just code coverage:

1. Delete every disposable database and rebuild; no durable knowledge or public identity is lost.
2. Reindex one changed document; unrelated document and section identities remain unchanged.
3. Guess a known archived/private section ID through every read endpoint; access policy still applies.
4. Kill a writer between temporary-file creation and atomic replace; canonical source remains valid.
5. Run two simultaneous ratifications; only one valid accepted transition is committed.
6. Delete evidence payload; active retrieval, snapshots, caches, and index references no longer expose it while the metadata-only tombstone remains non-sensitive.
7. Insert prompt-injection text into evidence; context returns it as quoted data with source status, never as system policy.
8. Grow historical fixtures by 100x; the same context budget remains enforced.
9. Rename a heading used by evidence; the old hash no longer resolves silently to unrelated content.
10. Open a newer canonical format with an older client; the client refuses mutation rather than corrupting it.

## 18. Public demo definition

The first share-worthy demonstration should be small and reproducible:

1. A researcher agent reads a synthetic repository and creates a proposal with exact source references.
2. A human accepts the proposal.
3. The first session ends.
4. A different coding agent requests a bounded ELM context packet.
5. It receives accepted project state, exact source locators, status, and token estimate.
6. It completes a small repository task.
7. A reviewer finds that repository truth changed.
8. ELM records a new proposal and supersedes the old claim after ratification.
9. A current query returns the new state; a historical query returns the old state with provenance.

The sanitized Phase 3 lifecycle smoke test exercises the governed state changes
through the CLI. The Phase 4 host harness then gives the resulting accepted
state to Antigravity/Gemini and Codex through the same read-only MCP adapter;
both must return the same stable source identity. This proves compatibility,
not authenticated actors or multi-tenant isolation.

## 19. Explicitly deferred

- graph database;
- automatic knowledge graph extraction;
- hosted service;
- multi-tenant authorization server;
- encrypted cloud synchronization;
- raw chat archive viewer;
- automatic promotion of inferred high-impact claims;
- custom memory model;
- fine-tuning, distillation, or reinforcement learning;
- mandatory embeddings;
- web UI;
- secure-erasure guarantees across Git and backups.

## 20. Remaining decisions before public release

The license is resolved as Apache-2.0. These decisions still affect the first
public tag:

1. Public repository name and final one-sentence positioning.
2. Minimum supported Python version.
3. Whether the first public package remains `elm-memory` or changes after a
   collision and discoverability check.

These do not block the completed Phase 1–3 implementation. They do block the
first public tag.

## 21. Immediate next implementation slice

Phase 4 is complete. Phase 5A proposal-only MCP mutation was ratified on
2026-08-26 and is the active implementation slice.

The active gate is **Phase 5A proposal-only MCP mutation** as defined in
[PHASE_5_TRUSTED_MUTATIONS.md](PHASE_5_TRUSTED_MUTATIONS.md). Accepted-state
MCP mutation requires a later, separately authorized Phase 5B verifier
deployment. Direct arbitrary Markdown writes, raw evidence snapshots,
embeddings, authenticated multi-user scopes, and model summarization remain
outside the Phase 5A gate.

## 22. Supersession rule

This document does not erase the original multi-agent handoff:

- the original handoff remains source material and research provenance;
- this document is the active staged architecture ratified for Phase 1 work;
- implemented and tested behavior becomes repository truth;
- later unimplemented phases remain provisional until user ratification or
  implementation evidence refines them.
