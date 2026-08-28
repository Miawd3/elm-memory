# Privacy model

ELM keeps durable memory in inspectable files under a root chosen by the user. `<root>/.elm/index.sqlite` contains copied Markdown text and governed-record projections, so filesystem access to the index should be treated as equivalent to access to the indexed memory.

## Defaults

- No network calls, telemetry, embeddings, model API, or raw-chat ingestion.
- Backups and `99_archive` are excluded from normal retrieval.
- Disputed, superseded, future-effective, expired, and deleted governed claims are excluded from normal retrieval.
- Archive, history, project, and namespace filters apply to search, context, outline, read, and related operations, including direct stable keys.
- Evidence records retain a source locator and SHA-256 hashes only. The CLI has no raw-evidence import operation.
- The default MCP process is read-only.
- Proposal-only MCP may persist untrusted candidate text, locators, and hashes for allowlisted projects but exposes no accepted-state transition.
- Autonomous MCP may persist bounded normal-sensitivity candidate text and active `agent_curated` claims for allowlisted projects. It cannot claim human or repository authority.
- Autonomous claims use bounded validity leases. Expiry hides a claim from ordinary reads and frees active quota without deleting canonical history.
- Default context traces contain a task hash, filters, section keys, timing, and token estimates, but no source body or raw task text.
- `--no-trace` disables traces. Raw task text requires the explicit `--trace-query-text` option.

## Canonical records

Proposal, evidence-reference, event, claim, and tombstone files may contain project facts, locators, actor labels, rationales, and hashes. A `restricted` sensitivity label is metadata; it does not encrypt content or enforce operating-system access control.

Actor values such as `human:reviewer` and `agent:researcher` are provenance labels supplied by the caller. They are not authenticated identities. The autonomous adapter stamps its configured write channel server-side, but that is not a cryptographic user identity.

Lifecycle events record metadata and hashes, not copies of source evidence. Temporary transaction backups are deleted after commit or successful rollback. If cleanup fails, ELM reports retained paths.

## Deletion

`elm delete` removes the active canonical item, records a metadata-only tombstone, and removes the item from ordinary derived retrieval. The tombstone keeps identity, type, project, deletion time, reason, prior content hash, and actor label; it does not retain the deleted body.

This is active-state deletion, not secure erasure. ELM cannot erase copies in Git, filesystem snapshots, external backups, shell history, model-provider logs, or another process.

Uninstalling ELM removes only the application runtime and command links. It never removes a configured Markdown root.

## Not promised

- Governance filters are not authentication between mutually untrusted users or agents.
- A source hash proves byte equality at a particular check; it does not prove authorship, truth, availability, or future freshness.
- A task hash can confirm a guessed task. Metadata-only traces are privacy-minimized, not anonymous.
- ELM cannot control what a calling agent, terminal, backup tool, or model provider records after output is returned.
- ELM does not claim cryptographic or forensic erasure.

## Public fixtures

Only synthetic project names and facts belong in this repository. Private memory roots, raw chats, credentials, personal source locators, and bootstrap archives must remain outside it.
