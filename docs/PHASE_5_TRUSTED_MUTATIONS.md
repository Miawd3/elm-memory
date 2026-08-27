# Phase 5 — Trusted mutation design

Status: Phase 5A implemented and locally verified; Phase 5B deferred and inactive

Date: 2026-08-26

Ratified: 2026-08-26 by explicit user approval

## 1. Decision

Phase 5 must not expose the existing governance CLI verbs as ordinary MCP
tools. The first safe increment is **proposal-only MCP mutation**: an agent may
submit an immutable candidate and inspect the resulting review plan, but it may
not accept, reject, defer, supersede, dispute, delete, recover, synchronize, or
migrate canonical memory.

Trusted ratification is a separate deployment capability. When it is enabled,
ELM consumes a short-lived, single-use, signed approval grant that is bound to
one exact operation and one exact canonical pre-state. A caller-supplied
`actor`, MCP client name, session identifier, confirmation string, tool
annotation, or bearer token by itself is never evidence that a human reviewed
the memory transition.

This split preserves the Phase 3 invariant that agent inference cannot become
accepted project truth and the Phase 4 invariant that MCP does not contain a
second implementation of ELM governance.

Implemented foundations: [Phase 3 governed memory](PHASE_3_GOVERNED_MEMORY.md)
and [Phase 4 read-only MCP](PHASE_4_READ_ONLY_MCP.md).

## 2. Security claim

The opt-in `proposal_only` local stdio profile may safely claim only this:

> An MCP client can create bounded, immutable candidate records, but cannot
> change accepted-memory state through the MCP surface.

The process default remains the Phase 4 seven-tool read-only surface and makes
no mutation claim.

This is an accepted-state integrity claim, not a claim that proposals are
harmless or confidential. A proposal still persists candidate text and consumes
disk. Phase 5A must label proposal output as untrusted, forbid raw-evidence and
credential fields, warn that arbitrary text cannot be reliably classified as
secret-free, and enforce quotas before writing.

It must not claim authenticated human ratification. A local agent that can run
arbitrary commands under the same OS account can also invoke an unprotected
CLI, read same-user secrets, or edit same-user configuration. Moving an
`accept` operation from MCP to a CLI changes the exposed interface, not the
trust boundary.

Cryptographically trusted ratification requires a verifier outside the
agent's authority, for example:

- a human approval broker running as a separate OS principal;
- a hardware-backed signing key requiring user presence;
- a separately administered local service; or
- a remote authorization service with authenticated users and transaction
  approval.

The verifier private key and trust-policy configuration must not be writable or
readable as secret material by the agent. The ELM executor receives only public
verification keys and signed grants. If the deployment cannot establish that
boundary, it remains proposal-only.

## 3. Actors and authority

| Role | Responsibility | Trusted for |
| --- | --- | --- |
| Proposer | Supplies a candidate claim and source references | Candidate content only |
| MCP host | Discovers tools, may show confirmation UI, and transports calls | Usability and defense in depth, not ratification |
| Reviewer | Decides whether one exact transition is intended | Human/project intent |
| Verifier | Authenticates the reviewer, evaluates policy, and signs one grant | Principal binding and operation authorization |
| Executor | Revalidates the grant and canonical state, then calls Phase 3 governance | Deterministic enforcement |
| Projector | Rebuilds disposable SQLite state from canonical files | Retrieval readiness only |

`actor` remains an untrusted provenance label. Phase 5 adds a separate
`principal_id` derived from a verified grant. Audit output may record both but
must never promote the former into the latter.

Evidence verification, policy authorization, human ratification, transaction
validation, and postcondition checking are different decisions. Passing one
does not imply that the others passed.

The verification pipeline therefore keeps five explicit results:

1. **Schema verification** proves only that the request is well formed.
2. **Evidence verification**, when policy requires it, proves that each allowed
   locator still resolves to the bytes named by its hash; it does not decide
   what those bytes mean.
3. **Ratification verification** proves that an authorized principal approved
   this exact transition envelope.
4. **Transaction verification** proves under the writer lock that the reviewed
   preconditions still hold and the canonical transition committed once.
5. **Postcondition verification** proves that canonical state, disposable
   projection, health checks, and the returned receipt agree.

`verified_repository_state` is not a shortcut around ratification. A verifier
may issue that authority only after it independently reads a pinned repository
locator and commit/tree/blob hash through a trusted path. An agent-supplied Git
hash, terminal transcript, test claim, or source-reference string is evidence
input, not successful verification.

The initial verifier profile never dereferences arbitrary HTTP(S) evidence.
Automatic byte verification is limited to pinned `repo://` and policy-permitted
`elm://` locators. HTTP(S) references may remain human-reviewable metadata, but
they cannot establish `verified_repository_state`; URI userinfo is rejected and
query values are redacted from logs and receipts. A later network verifier must
separately specify egress allowlists, redirect limits, DNS-rebinding protection,
timeouts, response-size caps, and credential handling before it is enabled.

## 4. Threat model

Phase 5 must withstand, within its declared deployment boundary:

- prompt-injected or malicious memory content attempting to authorize itself;
- an honest but buggy agent issuing the wrong mutation or retrying it;
- a malicious client fabricating actor, authority, project, or confirmation
  fields;
- replay of a previously valid approval;
- mutation of a proposal or claim between review and commit;
- two agents racing to consume the same proposal or grant;
- a stale SQLite projection hiding canonical changes;
- a confused-deputy flow where a credential for one root, project, action, or
  service is reused for another;
- proposal spam, oversized records, path traversal, archive bypass, and disk
  exhaustion;
- leakage of proposal bodies, evidence content, credentials, or approval
  secrets into logs and receipts; and
- a crash before or after the Phase 3 transaction commit point.

Out of scope for a same-user local process is protection against an attacker
that can arbitrarily modify the ELM executable, trusted verifier configuration,
canonical root, or operating system. Such a deployment needs OS isolation,
sandboxing, or a separately administered service.

## 5. Deployment modes

| Mode | MCP mutation surface | Ratification claim |
| --- | --- | --- |
| `read_only` (process default) | Existing seven Phase 4 tools | None |
| `proposal_only` (Phase 5A opt-in) | Read tools plus propose/list/preview | No accepted-state mutation |
| `host_confirmed` | Same as proposal-only | Host prompt observed only; not authenticated ratification |
| `trusted_verifier` (Phase 5B opt-in) | Proposal tools plus approved-transition executor | Signed principal and exact-operation authorization |
| Remote HTTP | Determined by OAuth scopes and server policy | Authentication only unless an exact-operation grant is also verified |

MCP HTTP authorization authenticates transport requests. It does not prove
that a person reviewed a specific claim body. Remote deployments therefore use
both audience-bound transport credentials and the same operation-bound grant
for accepted-state transitions.

## 6. Phase 5A surface: proposal-only

With an explicit `--mutation-mode proposal-only` launch option, Phase 5A adds
exactly three MCP tools. Omitting the option preserves the Phase 4 read-only
surface:

| Tool | Behavior | Annotation intent |
| --- | --- | --- |
| `propose_memory` | Atomically creates reference-only evidence metadata and one immutable proposal | additive, non-idempotent without a submission ID, closed-world |
| `list_memory_proposals` | Lists compact policy-filtered proposal manifests | read-only, closed-world |
| `preview_memory_transition` | Builds a non-signable before/after review plan without writing | read-only, closed-world |

`propose_memory` follows these rules:

1. `project` is mandatory, must pass canonical syntax validation, and must be in
   an explicit server-side `--allow-project` set that is validated against an
   existing ELM project. Tool input cannot create or widen that set.
2. The server stamps the provenance source; it does not accept an authenticated
   actor or accepted authority from tool input.
3. `requested_authority` is fixed to `agent_proposal` in proposal-only mode.
4. Evidence remains reference-only. Raw evidence bytes are rejected.
5. The caller supplies a random `submission_id`. Under the writer lock, ELM
   checks canonical `(project, submission_id, payload_digest)` state. Reuse with
   the same payload returns the prior proposal; reuse with a different payload
   fails. Explicit proposal deletion retains only a domain-separated hash of
   `(project, submission_id)` in the metadata-only tombstone, so the retired
   identity cannot be reused without retaining proposal content or its payload
   digest.
6. Existing Phase 3 field limits are retained and explicit limits are added for
   reference counts, request bytes, pending proposals and bytes per project,
   root-wide pending records and bytes, and per-process request rate. Durable
   root-wide limits remain effective after a server restart or project-name
   churn.
7. Archive, backup, transaction, event, tombstone, policy, and arbitrary target
   paths are never accepted as tool arguments.
8. A proposal cannot affect ordinary current-memory retrieval before a later
   ratified transition.
9. Proposal/list/preview output labels candidate bodies as untrusted data; text
   that resembles instructions cannot alter server policy or authorize a tool.
10. Idempotency validation, quota validation, reference-only evidence creation,
    and proposal creation occur under one writer lock in one recoverable Phase 3
    canonical transaction. A crash cannot leave orphan evidence or reserve a
    submission ID without its proposal.
11. Proposal list and preview perform their final projection-freshness check and
    governed read inside one writer-lock interval. A concurrent canonical commit
    therefore linearizes before the check or after the returned snapshot; it
    cannot make stale SQLite state appear current between check and use.

The `payload_digest` is
`SHA-256(ASCII("ELM-PROPOSAL-SUBMISSION-V1") || 0x00 || JCS(payload))`.
`payload` is a
closed object containing normalized `project`, `subject`, `predicate`, `object`,
`valid_from`, `sensitivity`, `rationale`, the constant
`requested_authority: agent_proposal`, sorted unique source references, and
sorted unique reference-only evidence descriptors (`kind`,
`source_uri`, content/excerpt hashes, sensitivity). It excludes
`submission_id`, proposal/evidence IDs, server timestamps, provenance stamps,
and every transport/session field.

Normalization uses the Phase 3 validators: safe single-line values are trimmed,
timestamps are UTC ISO-8601 with microseconds, hashes are lowercase, rationale
line endings become LF, and JSON strings otherwise preserve Unicode code points
without normalization. Descriptor arrays sort by their RFC 8785 canonical byte
representation. Duplicate object keys, duplicate normalized descriptors, and
unknown fields are rejected before hashing. Cross-platform digest vectors are a
Phase 5A acceptance artifact.

`preview_memory_transition` in Phase 5A returns a `review_plan` marked
`signable: false`. It may show the candidate, action, root/project, evidence
locators, current target hashes, and before/after effect, but it contains no
`executor_id`, policy version, or policy digest and cannot be passed to an
executor. The exact signable envelope below is introduced only by Phase 5B.

Phase 5A does not expose `accept`, `reject`, `defer`, `supersede`, `dispute`,
`delete`, `recover`, `sync`, `rebuild`, or `ids assign` through MCP.

## 7. Canonical records and compatibility

Phase 5 freezes these new canonical records before implementation:

```text
<root>/00_registry/ELM_ROOT_ID.json
<root>/01_inbox/elm_proposals/<project>/proposal_<uuid4>.json
<root>/30_agent_logs/elm_grant_consumptions/YYYY/MM/DD/grant_<id>.json
```

`ELM_ROOT_ID.json` is an immutable `root_identity` record with
`format_version: 1`, `root_id`, `created_at`, and a non-authenticating creator
label. It is created exclusively by a backup-backed CLI bootstrap command and
is never generated by indexing or MCP.

New MCP proposals use `record_type: proposal`, `format_version: 2`. Version 2
retains every Phase 3 proposal field and adds required `submission_id`,
`payload_digest`, and `source_channel`. Phase 3 version-1 proposals remain
readable, indexable, and eligible for review, but only version-2 proposals
participate in submission-id deduplication. Older clients must reject mutation
of a version-2 proposal rather than silently ignore its security fields.

A `grant_consumption` record is immutable and contains `format_version: 1`,
`grant_id`, `grant_sha256`, operation and policy digests, executor/root/project,
verified principal and key IDs, action, consumed time, lifecycle transaction
and event IDs, before/after hashes, and an immutable canonical transaction
receipt. That receipt describes only the committed canonical transition; it
does not contain later projection health. The record stores no proposal body,
evidence payload, bearer token, private key, or reusable secret. It is created
in the same Phase 3 transaction as the authorized lifecycle transition.

The parser and indexer support record-specific format versions instead of one
global version assumption. SQLite adds rebuildable projections for proposal-v2
submission identity and grant consumption. Delete-and-rebuild must reproduce
idempotent lookup and consumed-grant state entirely from these files.

Trusted verifier keys, `executor_id`, and authorization policy remain deployment
configuration outside the portable root; they are not canonical ELM records.

New durable identities use their record-type prefix plus UUIDv4:
`root_<uuid4>`, `submission_<uuid4>`, `executor_<uuid4>`, and `grant_<uuid4>`.
Approval nonces are separate, cryptographically random values with at least 128
bits of entropy.

## 8. Transition envelope

In Phase 5B, the trusted verifier flow converts a current review plan into this
deterministic envelope. The verifier signs its digest rather than agent-authored
prose. Phase 5A does not emit this signable object.

```json
{
  "format_version": 1,
  "action": "accept",
  "root_id": "root_<uuid>",
  "executor_id": "executor_<uuid>",
  "project": "orion",
  "target": {
    "proposal_id": "proposal_<uuid>",
    "claim_id": null
  },
  "parameters": {
    "authority": "user_ratified",
    "reason_code": null
  },
  "expected_state": {
    "proposal_status": "pending",
    "proposal_sha256": "<digest>",
    "claim_sha256": null,
    "lifecycle_sha256": "<digest>"
  },
  "policy_version": 1,
  "policy_digest": "sha256:<digest>"
}
```

The actual action-specific schema is closed: unknown, missing, action-forbidden,
or incorrectly typed fields are rejected. JSON parsing rejects duplicate keys,
floats, non-finite numbers, type coercion, invalid Unicode, and values outside
declared byte/count ranges before canonicalization. The envelope is serialized
with RFC 8785 JCS and hashed with SHA-256.
Timestamps, display text, client names, and mutable absolute paths are excluded
from the operation identity. A stable `root_id` identifies portable canonical
memory, while an `executor_id` identifies one trusted deployment. The
deployment configuration pins both identities and the verifier keys, so copying
a root does not make an approval grant valid in another executor.

The lifecycle digest covers the minimum canonical records that determine the
transition. Unrelated project activity does not invalidate a grant, but a
change to the target proposal, claim, tombstone, or terminal-event state does.

## 9. Approval grant

The approval broker displays the canonical proposal, evidence locators,
before/after transition, authority, project, and destructive effect directly
from the envelope. It must not sign a digest displayed only by the requesting
agent.

A Phase 5B grant contains:

```json
{
  "format_version": 1,
  "grant_id": "grant_<uuid4>",
  "issuer": "verifier.example",
  "principal_id": "human:<stable-pseudonymous-id>",
  "verifier_key_id": "key_<id>",
  "signature_algorithm": "Ed25519",
  "audience": "elm://executor/executor_<uuid>",
  "root_id": "root_<uuid>",
  "operation_digest": "sha256:<digest>",
  "action": "accept",
  "project": "orion",
  "issued_at": "<UTC timestamp>",
  "not_before": "<UTC timestamp>",
  "expires_at": "<UTC timestamp>",
  "nonce": "<128-bit-random>",
  "max_uses": 1,
  "policy_version": 1,
  "policy_digest": "sha256:<digest>",
  "signature": "<base64url-no-padding>"
}
```

The signature covers `ASCII("ELM-APPROVAL-GRANT-V1") || 0x00` followed by the
RFC 8785 canonical bytes of every field except `signature`. Format version 1
fixes `signature_algorithm` to
Ed25519, a 32-byte allowed public key, and an exactly 64-byte signature encoded
as unpadded base64url. An unknown algorithm, encoding, field, policy digest, or
format fails closed. Cross-platform canonicalization/signature test vectors are
published before execution is implemented.

The grant never contains a private key, bearer token, raw evidence payload, or
reusable authorization secret. Its lifetime is short and `max_uses` is always
one for canonical mutation.

## 10. Phase 5B execution

Trusted-verifier policy is enforced in the governance core, not only in the MCP
adapter. When a root is opened by a trusted-verifier deployment, every CLI,
Python, or MCP path that initiates `accept`, `reject`, `defer`, `supersede`, or
`dispute` must supply the same valid grant; `--actor` and `--authority` alone
are refused. A deployment that leaves an unprotected direct CLI path available
to the agent cannot claim trusted ratification. Delete and recovery require
separate CLI-only administrator authorization and cannot be used as a bypass.

Phase 5B adds one opt-in MCP tool, `execute_approved_transition`, rather than a
set of independently privileged lifecycle tools. It accepts the exact envelope
and signed grant. The server verifies in this order:

1. Parse both closed schemas and enforce byte/count limits.
2. Require trusted-verifier mode and a healthy, pinned deployment policy.
3. Resolve `verifier_key_id` from the trusted key allowlist.
4. Verify the grant signature without consulting agent-provided keys.
5. Verify issuer, audience/executor, root, project, action, immutable policy
   version and digest, validity interval, nonce shape, and `max_uses == 1`.
6. Recompute the canonical operation digest and compare it in constant time.
7. Treat any pre-lock consumed-grant lookup as an advisory optimization only.
8. Invoke one governance-core transaction coordinator, which acquires the
   existing ELM writer lock exactly once.
9. Under that lock, reload the trusted policy and atomically recheck revocation,
   policy digest, and grant consumption. Return the recorded receipt only when
   the canonical signed grant and operation digest are identical; otherwise
   reject replay.
10. Recompute target hashes and lifecycle state under the lock; reject stale
    review state rather than silently rebuilding the plan.
11. Re-evaluate server-side authorization policy for the verified principal.
12. Call a refactored internal Phase 3 transition primitive that requires the
    coordinator's already-held lock; public lifecycle entry points continue to
    acquire their own single lock. Nested lock acquisition is forbidden.
13. In the same recoverable transaction, commit the lifecycle changes and the
    immutable `grant_consumption` record containing the bounded receipt.
14. Refresh disposable projection state and return that receipt.

If a retry presents the same grant and exact operation after a successful
commit, the server returns the original canonical transaction receipt plus a
fresh, separately labeled projection status without applying another
transition. The same grant with different bytes fails as a replay conflict.

Initial Phase 5B actions are `accept`, `reject`, `defer`, `supersede`, and
`dispute`, each separately allowlisted by policy. `delete`, `recover`, `sync`,
`rebuild`, identity migration, arbitrary Markdown writes, and trust-policy/key
changes remain outside MCP.

## 11. Canonical replay and audit state

Replay protection cannot live only in SQLite because SQLite is disposable.
The canonical `grant_consumption` record and lifecycle changes share one
transaction and commit point. Rebuilding the index recreates the
consumed-grant projection and idempotent result lookup.

The immutable canonical transaction receipt contains identifiers and hashes,
not proposal bodies or evidence payloads:

- `grant_id`, operation digest, action, project, and verified principal ID;
- proposal, claim, event, and transaction IDs where applicable;
- canonical before/after hashes; and
- `committed: true` for the exact canonical transition.

The operational MCP response wraps that receipt and adds a separately labeled,
current `projection_status` after refresh. Projection health is never written
into the earlier immutable receipt. A refused operation creates no
`grant_consumption` record; it returns a bounded error response and may emit
privacy-minimized operational telemetry under the existing trace policy.

If canonical commit succeeds but projection refresh fails, ELM reports
`committed: true` and `projection_healthy: false`. It does not roll back accepted
truth to make SQLite look healthy. Every read tool in a mutation-capable MCP
process performs a readiness check and fails closed while `healthy` is false or
`sync_required` is true; this is a new Phase 5 requirement, not a claim about
the existing Phase 4 server. The process resumes reads only after successful
sync/rebuild and health verification.

A trusted-verifier deployment also owns the root and index read boundary: every
supported CLI/MCP reader sharing that root must enforce the same readiness
contract, and legacy Phase 4/no-sync reader processes are forbidden there. The
deployment must enforce this through service/OS isolation or exclusive root
access; without it, trusted-verifier security is not claimed. Proposal-only
deployments remain compatible with the ordinary Phase 4 boundary because they
cannot change accepted-memory state.

## 12. Policy and key management

Trusted-verifier mode requires startup configuration that pins:

- the expected stable `root_id`;
- the deployment-specific `executor_id` used as the grant audience;
- accepted grant format and signature algorithms;
- verifier public keys and key IDs;
- allowed actions, projects, authorities, and principal mappings;
- maximum grant lifetime and clock-skew tolerance; and
- an immutable policy version, canonical policy digest, and revocation state.

Private signing keys never live in the ELM root, SQLite index, MCP environment,
repository, logs, or agent-readable configuration. Changing the trusted key set
is an administrator/bootstrap operation, not an MCP tool.

The portable `root_id` is assigned only by an explicit, backup-backed CLI
bootstrap/migration; indexing and MCP never create it. An intentional root copy
keeps that identity. The deployment-specific `executor_id` and verifier policy
live outside the portable root, so a new deployment receives a new grant
audience.

One `executor_id` is provisioned for exactly one configured root instance. The
trusted configuration pins the resolved root location and `root_id`; startup
refuses a mismatch or reuse against another clone. A cloned root receives a new
executor ID unless an administrator explicitly declares both copies to be one
authorization domain. Replacement of a root at the same trusted location by an
attacker with filesystem-control authority remains outside the local threat
model.

ELM cannot prove that a same-user configuration file is outside an unrestricted
agent's control. `status` must therefore report the configured mutation mode and
trust assumptions without upgrading an operational setup into a security claim.
An absent, malformed, unexpected, expired, or unverifiable policy disables the
approved-transition tool and leaves proposal-only operation available.

## 13. Host controls are defense in depth

MCP annotations describe tool risk but do not authorize a mutation. Phase 5
sets accurate annotations and strongly recommends host allowlists and per-tool
prompts. The host exposes only the read tools by default. When an operator
selects the proposal-only profile, the host may add the three proposal tools
while keeping the approved executor disabled until separately configured.

These controls reduce accidents and improve user visibility, but ELM still
performs every authorization check server-side. Tool descriptions, server
instructions, approval-mode settings, namespaces, and sensitivity labels are
not accepted as proof of identity or intent.

## 14. Acceptance matrix

### Surface and authority

- Default MCP lists only the existing seven Phase 4 read tools. The explicit
  proposal-only profile additionally lists propose/list/preview and no
  accepted-state mutation tool.
- MCP input cannot select an accepted authority or verified principal.
- A caller-supplied `actor`, client name, confirmation string, or annotation
  change never satisfies ratification.
- Direct arbitrary file, policy, key, migration, recovery, and deletion tools
  remain absent.

### Grant verification

- A valid signed grant for the exact root/project/action/state succeeds once.
- Tampered signature, payload, operation digest, audience, root, project,
  action, authority, policy version, or key ID fails closed.
- Expired, not-yet-valid, revoked, overlong, unknown-format, and unknown-key
  grants fail closed.
- Reusing a grant returns the prior receipt only for the exact completed
  operation; otherwise it fails as replay.
- Cross-platform canonicalization and Ed25519 test vectors match byte-for-byte.
- Duplicate keys, floats, type coercion, invalid Unicode, wrong signature
  encoding, policy-digest mismatch, and missing/forbidden action fields fail
  before mutation.

### State and concurrency

- A canonical target change after approval yields `stale_approval` and no write.
- Two concurrent consumers of one grant produce one commit and one idempotent
  receipt or replay refusal.
- Two different grants for the same terminal proposal cannot create two claims.
- Crash injection at every Phase 3 journal boundary preserves explicit,
  hash-guarded recovery.
- Rebuild preserves grant consumption, receipts, lifecycle identity, and current
  versus historical retrieval.
- Pre-lock replay lookup cannot authorize or reject execution; consumption,
  policy, state, transition, and receipt are rechecked/committed under one lock.

### Abuse and privacy

- Submission IDs deduplicate retries and reject payload conflicts.
- Size, count, pending-queue, and rate limits reject proposal flooding before a
  canonical write.
- Project and root-wide quotas plus server-side project allowlists prevent
  project-name churn from bypassing durable limits.
- Concurrent reuse of one submission ID creates one proposal; crash injection
  never leaves orphan evidence or a reserved submission without its proposal.
- Path traversal, symlink escape, backup/archive targeting, and project-scope
  bypass are rejected.
- Prompt injection inside candidate text or source metadata cannot alter policy
  or operation fields.
- Logs, errors, events, and receipts contain no raw evidence, private key,
  bearer token, or reusable grant secret.
- The initial verifier never performs HTTP(S) fetches; disallowed URI credentials
  are rejected and query values are redacted from operational output.

### Failure behavior

- Verifier/policy unavailability disables accepted-state execution.
- Pending canonical transactions block new mutation and governed reads.
- Projection failure after canonical commit is reported distinctly and never
  rewinds committed truth.
- Every supported reader in a trusted-verifier deployment fails closed until
  projection health is restored; legacy/concurrent Phase 4 readers are refused
  or excluded by the deployment boundary.
- Executor startup refuses a root/executor binding mismatch or accidental reuse
  of one executor identity against a second clone.
- A failed MCP request does not poison later direct CLI recovery or reads.

## 15. Implementation sequence and gates

1. **5.0 — Ratify this contract.** Completed 2026-08-26. The approval covers
   Phase 5A only; it does not authorize Phase 5B accepted-state execution.
2. **5A.1 — Canonical identities and limits.** Completed locally. Added root identity, proposal-v2
   submission metadata, explicit project allowlists, root/project quotas,
   compound transactions, migrations, and concurrency/crash tests in the core.
3. **5A.2 — Proposal-only MCP tools.** Completed locally. Delegates to the CLI JSON contract and
   verify that accepted state cannot change through the exposed surface.
4. **5A.3 — Non-signable review plan.** Completed locally. Added deterministic submission
   canonicalization, preview, and cross-platform digest vectors without an
   executor identity, policy, or signing dependency.
5. **5A release gate.** Run the full Phase 1–4 suite, proposal abuse tests,
   Windows/Linux CI, wheel isolation, and a heterogeneous-host demonstration.
6. **5B.1 — Verifier interface and signable envelope.** Implement executor
   identity, immutable policy loading, exact envelopes, public-key grant
   verification, expiry/revocation, and canonical replay records behind an
   opt-in flag.
7. **5B.2 — Approved executor.** Refactor one lock-owning core transaction
   coordinator, connect the verified transition entry point, separate canonical
   receipts from projection status, and add crash/concurrency/adversarial tests.
8. **5B release gate.** Validate with a real separate-boundary approval broker.
   A same-user mock signer is test infrastructure and does not satisfy this
   security gate.

Phase 5A may ship independently and is the recommended next implementation
slice. Phase 5B remains inactive until a concrete cross-platform verifier
deployment is selected and independently reviewed.

## 16. Standards and implementation references

- MCP tools and human-in-the-loop guidance:
  <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>
- MCP authorization and audience-bound HTTP tokens:
  <https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- MCP security best practices, local-server risks, replay/session concerns, and
  scope minimization:
  <https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices>
- Codex MCP transports, tool allowlists, and per-tool approval controls:
  <https://learn.chatgpt.com/docs/extend/mcp?surface=cli>
- JSON Canonicalization Scheme: <https://www.rfc-editor.org/rfc/rfc8785>
- Ed25519: <https://www.rfc-editor.org/rfc/rfc8032>

## 17. Ratified decisions

The user explicitly ratified these Phase 5A boundaries on 2026-08-26:

1. Ratify Phase 5A as an opt-in proposal-only MCP profile with no
   accepted-state tool; preserve read-only as the process default.
2. Keep deletion, recovery, synchronization, migration, and trust-policy changes
   permanently outside the Phase 5 MCP surface.
3. Treat host approval prompts as defense in depth, not trusted verification.
4. Defer Phase 5B implementation until the first real verifier target is chosen:
   hardware-backed local approval or a separately administered approval service.

This ratification authorizes implementation and validation of items 5A.1–5A.3.
It does not authorize any MCP operation that changes accepted memory state.
