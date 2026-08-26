# Phase 2 Readiness

Status: implementation-complete and hosted CI verified

Evaluated: 2026-08-26

Package version: `0.3.0.dev1`

Base implementation commit: `64f85394b656d7b4143c49a19bc9aeaf22a6d3be`

Archive-identity hotfix commit: `0b004ace166f301407575549042a5a03f5eeec88`

## Implemented scope

- `elm context TASK --budget TOKENS` with a hard deterministic packet budget.
- Stable section locators, status and authority labels, source manifests, and
  whole exact sections quoted as untrusted memory data.
- Strict FTS5 retrieval with broad fallback only after an empty strict result.
- Bounded current-state/constraint supplements that preserve all active read
  filters.
- Path-bound projections for exact archive copies that retain an active
  document UID, preventing derived-index identity collisions without rewriting
  backup bytes.
- Atomic metadata-only retrieval traces with raw task text disabled by default.
- Explicit raw-query opt-in, trace disablement, declared expiry, and
  preview/apply retention cleanup.
- A 50-case sanitized comparison of no-memory, full-file, search/read, and
  context-pack estimated-token baselines.

## Local acceptance

- 57 unit, integration, CLI-contract, identity, migration, concurrency,
  read-policy, context, trace, CI-policy, and hygiene tests pass on Windows with
  Python 3.14.3.
- Compilation passes for `src`, `tests`, and `benchmarks`.
- The Agent Skill passes the bundled `quick_validate.py` validator.
- The sanitized benchmark passes 50/50 lexical retrieval cases and 48/48
  positive context-source cases, with 100% packet-budget compliance and zero
  archive leaks.
- Retrieval quality and estimated-token cost are reported separately from task
  outcome; task outcome remains explicitly unmeasured.

## Package acceptance

- A pure-Python wheel builds as
  `elm_memory-0.3.0.dev1-py3-none-any.whl`.
- Its validation-build SHA-256 was
  `A44BA7931752E449986FAA30F9ABF4793DA0023CE8110E1CCD50D25C25CA1C3F`.
- An isolated Python 3.11 target installation exposes version `0.3.0.dev1` and
  the `context` and `traces` commands.

The validation wheel was temporary. Reproducible release artifacts will be
built again from the eventual tagged commit, so their hashes may differ because
wheel ZIP metadata is not normalized by this pre-release workflow.

## Private-copy acceptance

A temporary copy of the complete private ELM was tested without modifying the
live corpus:

- 651 Markdown documents and 4,990 indexed sections;
- rebuild completed with zero indexing errors;
- `doctor` reported zero active issues;
- strict `local skill creation` context retrieval found
  `10_shared/general_notes/CODEX_SKILL_INSTALLATIONS.md`;
- the packet used 372 of a 700-token estimate budget;
- no backup or `99_archive` source entered the packet;
- the default trace had `query_text: null` and no packet/source-body fields;
- the final incremental sync reported zero changes;
- SQLite `PRAGMA quick_check` returned `ok`;
- SHA-256 comparison found zero Markdown changes in both the temporary copy and
  live corpus.

A second 652-file private-copy probe added an exact Markdown backup containing
the active document's UID. Rebuild and final sync completed with zero errors;
the active projection retained its UID, the backup projection used a null UID
and path-bound section key, `doctor` and `quick_check` passed, and neither the
backup nor live canonical bytes changed.

The temporary copy and its derived trace/index state were removed after the
probe.

## Hosted acceptance

Implementation commit `64f85394b656d7b4143c49a19bc9aeaf22a6d3be`
passed both GitHub Actions contexts for private PR 6:

- push run `32959376281`: nine of nine jobs passed;
- pull-request run `32959380309`: nine of nine jobs passed.

Each run covered the 50-case benchmark plus Python 3.11–3.14 on Ubuntu and
Windows. The workflow retained read-only token permissions, unprivileged
triggers, full-SHA action pins, and disabled checkout credential persistence.

Archive hotfix commit `0b004ace166f301407575549042a5a03f5eeec88` then passed
the same two nine-job contexts for private PR 7:

- push run `32960345662`;
- pull-request run `32960348574`.

## Evidence limits

- The deterministic `ceil(characters / 4)` accounting unit is not a vendor
  tokenizer and does not measure billed tokens.
- The fixture suite is sanitized and lexical. It does not establish semantic
  recall on arbitrary corpora or downstream coding-task success.
- Project and namespace filters are governance controls, not authenticated
  security boundaries between mutually untrusted OS users.
- A task hash can confirm a correctly guessed task; metadata-only traces are
  privacy-minimized, not anonymous.
- Context labels cannot prove current repository truth. Agents must inspect the
  repository separately for consequential implementation facts.

## Deferred

Claims, proposals, evidence snapshots, temporal history, MCP, embeddings, model
summarization, and end-to-end agent task evaluation remain outside Phase 2.
