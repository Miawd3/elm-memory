# Phase 5A readiness record

Status: local and hosted implementation acceptance passed

Date: 2026-08-26

Version: `0.6.0.dev0`

## Scope delivered

Phase 5A adds bounded candidate submission without granting an agent authority
to change accepted memory. The process default remains the exact Phase 4
seven-tool read-only MCP surface. An operator must explicitly select
`--mutation-mode proposal-only` and provide one or more existing
`--allow-project` values before the three additional tools exist:

- `propose_memory`;
- `list_memory_proposals`;
- `preview_memory_transition`.

There is no MCP tool for accept, reject, defer, supersede, dispute, delete,
recover, sync, rebuild, document-ID migration, root-ID bootstrap, or policy/key
management. Phase 5B accepted-state execution remains unimplemented and is no
longer an active roadmap item.

## Canonical changes

- `elm root-id init --dry-run/--apply` explicitly creates the immutable
  `00_registry/ELM_ROOT_ID.json` record and a recoverable copy under
  `backups/elm-root-identity/`. Indexing and MCP never create it.
- MCP submissions create proposal format v2. Version 2 retains Phase 3 fields
  and adds `submission_id`, `payload_digest`, and `source_channel`.
- Version-1 proposals remain readable, indexable, and eligible for explicit CLI
  lifecycle review.
- Evidence remains reference-only. One writer lock and one canonical recovery
  transaction cover every new evidence record and its proposal, preventing an
  orphan or a reserved submission without a proposal.
- Index schema v3 projects proposal format/submission identity and migrates v2
  state in place. Delete-and-rebuild reproduces the same idempotency lookup.

## Submission identity and limits

The payload digest is:

```text
SHA-256("ELM-PROPOSAL-SUBMISSION-V1" || 0x00 || RFC8785-JCS(payload))
```

The parser rejects duplicate object keys, floats, non-finite numbers, invalid
Unicode, type mismatches, unknown fields, duplicate normalized references, and
raw-evidence fields. Timestamps become UTC with microseconds; hashes become
lowercase; rationale line endings become LF. A fixed test vector freezes the
canonical bytes and digest across platforms.

Default server limits are:

| Limit | Default |
| --- | ---: |
| UTF-8 request bytes | 65,536 |
| source plus evidence references | 16 |
| pending proposals per project | 256 |
| pending bytes per project | 4 MiB |
| pending proposal/evidence records across the root | 2,048 |
| pending bytes across the root | 32 MiB |
| proposal calls per server process per minute | 30 |

Durable project/root quotas are recomputed from canonical proposal, evidence,
event, and tombstone state under the writer lock. A restart or a second allowed
project cannot reset the root-wide limits. Process-local rate limiting is an
additional abuse brake, not the durable quota boundary.

If a proposal-v2 record is explicitly deleted, its metadata-only tombstone
retains a domain-separated hash of `(project, submission_id)`. The proposal
body and payload digest remain deleted, while the retired identity cannot be
used to create a second canonical proposal.

## Readiness and failure behavior

`status` now compares a canonical governance-record digest with the digest of
the last successful SQLite projection. A proposal committed before projection
failure therefore makes the server unhealthy instead of letting a stale list
look current. Proposal tools call `status` first and fail closed until an
operator repairs the index through the CLI. List and preview also repeat the
freshness decision inside the same writer-lock interval as their governed read,
closing the check/use race with a concurrent proposal commit.

If canonical commit succeeds but any ordinary projection refresh operation
raises or reports an error, the response keeps `canonical_committed: true` and
reports sanitized projection health separately. It does not roll back durable
truth to make SQLite appear healthy.

Every proposal-v2 digest is recomputed from the canonical proposal and linked
evidence descriptors before projection, preview, or idempotent replay. Pending
proposals prevent deletion of their evidence. Evidence may be tombstoned only
after a terminal proposal transition; the historical proposal remains
readable, but replay is refused because its complete digest can no longer be
reconstructed.

Proposal output is labeled untrusted candidate data. The server stamps
`actor: mcp:unverified` and `requested_authority: agent_proposal`; neither can
be selected by tool input. HTTP/URI credentials and query strings are rejected
from Phase 5A evidence locators. The preview is marked `signable: false` and
contains no executor ID, policy version, policy digest, signing key, or grant.

## Local acceptance evidence

- 109 unit/integration tests passed on Python 3.14.3 for the complete Phase 1–5A
  suite, including existing lifecycle recovery and read-only MCP tests.
- The Phase 5A test group covers strict canonical JSON, a fixed digest vector,
  explicit/immutable root bootstrap, v1/v2 compatibility, idempotent retry,
  conflicting retry, concurrent retry, durable project/root quotas, project
  allowlists, credential rejection, crash rollback, and stale-projection
  detection, including duplicate normalized references, prohibited JSON scalar
  coercion, boolean version rejection, tampered proposal/evidence digests,
  tombstoned-evidence replay refusal, configured request/reference boundaries,
  symlink escape refusal, separate reporting for arbitrary ordinary
  post-commit projection failures, and fail-closed detection if a root identity
  changes after proposal-server startup.
- `benchmarks/run_phase5a_demo.py --assert-pass` passed all 15 synthetic checks:
  exact seven/ten tool surfaces, annotations, root bootstrap isolation,
  idempotency, conflict refusal, no orphan records, non-signable preview,
  unchanged accepted state, healthy status, and SQLite `quick_check`.
- `python -m compileall -q src tests benchmarks` and repository diff checks are
  clean.
- A pure-Python `0.6.0.dev0` wheel installed with the MCP extra into a fresh
  Windows virtual environment. The installed package imported at the expected
  version, initialized a synthetic root identity, rebuilt schema v3, reported a
  healthy current governance projection, and returned SQLite `quick_check: ok`.
  The tested wheel SHA-256 is
  `462831A9D34906005AA181873EAEB35E90B04B47364FD5EC724D5EA8EEE1BE94`.

The existing 50-case sanitized retrieval benchmark, 12-check governance demo,
and 14-check read-only MCP demo also pass locally. GitHub Actions run
`33026006634` passed the sanitized demo job and the full Python 3.11–3.14 matrix
on both Ubuntu and Windows for implementation commit `6029d55`.

## Post-merge soak pilot

The post-merge Phase 5A pilot adds
`benchmarks/run_phase5a_soak.py` and the operating contract in
`docs/PHASE_5A_SOAK_PILOT.md`. The default profile repeats seven synthetic
scenarios twice with six logical MCP agents: exact replay, conflicting replay,
unique writer contention, durable project quota, process rate limit, stale
projection repair, and the exact seven/ten-tool surface boundary.

The first repeated Windows run reproduced transient `WinError 32` lock-release
and `WinError 5` atomic-replace failures. Writer-lock release now performs
bounded retry with ownership-token revalidation, and atomic replacement performs
bounded retry only for `PermissionError`. Injected regression tests verify both
repairs and confirm that a foreign lock token is never deleted.

After those repairs, 113 local tests passed while the two-repetition soak ran in
parallel with the regression suite. The selected acceptance run completed 70
logical operations in 81 tool-call attempts with 11 bounded retries, 54
successful calls, and the expected conflict/quota/rate/stale-projection
refusals. It reported 9,442 request-side and 39,923 response-side
model-neutral serialized token estimates. Provider-billed tokens remain
explicitly unavailable and `null`; latency and estimated-token totals are
measurements, not fixed CI thresholds.

## Operational start

```bash
elm root-id init --root /path/to/memory --dry-run --creator operator:local --json
elm root-id init --root /path/to/memory --apply --creator operator:local --json
elm rebuild --root /path/to/memory --json

elm-mcp --root /path/to/memory \
  --mutation-mode proposal-only \
  --allow-project PROJECT
```

The root identity and project allowlist are operator configuration. They do not
authenticate a human reviewer. Explicit CLI ratification remains a separate
governance action, and the agent that authored a proposal must not self-accept
it merely because the proposal exists.
