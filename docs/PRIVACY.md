# Privacy Model — Phase 6B.3

ELM keeps durable project memory in inspectable files under the configured root.
`<root>/.elm/index.sqlite` contains copied Markdown text and governed-record
projections, so filesystem access to the index should be treated as equivalent
to access to the indexed memory.

## Defaults

- No network calls, telemetry, embeddings, model API, or raw-chat ingestion.
- Backups and `99_archive` are excluded from normal retrieval.
- Disputed, superseded, future-effective, expired, and deleted governed claims
  are excluded from normal retrieval.
- Archive, history, project, and namespace filters apply to search, context,
  outline, read, and related operations, including direct IDs and stable keys.
- Evidence records retain a source locator and SHA-256 hashes only. They use
  `retention: reference_only`; the CLI has no raw-payload import operation.
- Proposals are candidate records. The manual lifecycle requires an explicit
  command and accepted authority label. The separate autonomous profile may
  activate model-selected memory only as `agent_curated`, never as
  user-ratified or repository-verified authority.
- The MCP process default is read-only. The opt-in proposal-only profile may
  persist candidate text, source locators, and hashes for server-allowlisted
  projects, but it exposes no accepted-state transition.
- The opt-in autonomous profile may persist normal-sensitivity candidate text,
  source locators, hashes, and active `agent_curated` claims for allowlisted
  projects. It is bounded by request, reference, rate, pending-record, and
  active-memory quotas. It exposes no arbitrary deletion, dispute, or policy tool.
  Under Phase 6B.2, it supports source-verified compare-and-swap (CAS) supersession
  and active-lease renewal for the sole current `agent_curated` lineage head
  within operator-configured source roots, without granting stronger authority.
- Phase 6B.3 logical compaction is a bounded, deterministic, read-only view
  derived on demand during `history` retrieval. It derives lineage manifests under
  an explicit token budget, reports truncation explicitly, and never writes
  compaction records, modifies canonical Markdown, or deletes historical events.
- New autonomous claims use bounded, digest-bound validity leases. Expiry hides
  a claim from ordinary retrieval and frees active quota, but the canonical
  proposal, claim, event, and reference metadata remain available in history.
- Proposal-only input rejects raw-evidence fields, embedded URI credentials,
  and URI query strings. This reduces obvious leakage paths but cannot reliably
  detect every secret embedded in arbitrary candidate prose.
- Default context traces contain a task SHA-256, filters, section keys, timing,
  and token estimates, but no source body or raw task text.
- Raw task text in a trace requires explicit `--trace-query-text`; `--no-trace`
  disables trace creation.

## Governed records

Canonical proposal, evidence-reference, event, claim, and tombstone files may
contain project facts, source locators, actor labels, rationales, and hashes.
Choose `restricted` sensitivity for records whose disclosure needs extra care,
but understand that this label is metadata: it does not encrypt content or
enforce operating-system access control.

Actor values such as `human:reviewer` and `agent:researcher` are provenance
labels supplied by the caller. Phase 3 does not authenticate that identity.
The autonomous MCP adapter stamps `mcp:autonomous` server-side; this identifies
the configured write path but is not a cryptographic user identity.

Lifecycle events record metadata and hashes, not copies of source evidence.
Temporary transaction backups are deleted after commit or successful rollback.
If cleanup fails, the CLI reports retained paths rather than silently claiming
that they were removed.

## Deletion semantics

`elm delete` removes the selected governed item's active canonical file, records
a metadata-only tombstone, and removes the item from active derived retrieval on
the next projection update. A tombstone keeps the item ID, type, project,
deletion time, reason code, prior content hash, and actor label; it does not keep
the deleted claim object, rationale, proposal text, or evidence payload.

This is active-state deletion, not a secure-erasure promise. It cannot erase
copies already present in Git, filesystem snapshots, external backups, shell
history, model-provider logs, or another process. Historical reconstruction of
a physically deleted claim is limited to its tombstone metadata.

## Not promised

- Archive, project, namespace, history, and sensitivity filtering are governance
  controls, not authentication between mutually untrusted users or agents.
- A source hash proves equality with bytes supplied later; it does not prove the
  source's authorship, truth, availability, or ongoing freshness. Phase 6B.2 CAS
  verifies current local bytes inside an operator-configured root at transition check
  time only, not permanent correctness or external repository invariance.
- A task hash can support confirmation of a guessed task; metadata-only traces
  are privacy-minimized, not anonymous.
- ELM cannot control what a calling agent, terminal, backup tool, or model
  provider records after output is returned.
- ELM does not claim cryptographic or forensic erasure.

## Public fixtures

Only synthetic project names and facts may be committed. Private ELM snapshots,
raw chats, credentials, personal source locators, and bootstrap archives must
remain outside the repository.
