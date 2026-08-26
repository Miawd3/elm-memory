# Phase 2 Readiness

Status: implementation-complete and hosted CI verified

Evaluated: 2026-08-26

Package version: `0.3.0.dev0`

Implementation commit: `64f85394b656d7b4143c49a19bc9aeaf22a6d3be`

## Implemented scope

- `elm context TASK --budget TOKENS` with a hard deterministic packet budget.
- Stable section locators, status and authority labels, source manifests, and
  whole exact sections quoted as untrusted memory data.
- Strict FTS5 retrieval with broad fallback only after an empty strict result.
- Bounded current-state/constraint supplements that preserve all active read
  filters.
- Atomic metadata-only retrieval traces with raw task text disabled by default.
- Explicit raw-query opt-in, trace disablement, declared expiry, and
  preview/apply retention cleanup.
- A 50-case sanitized comparison of no-memory, full-file, search/read, and
  context-pack estimated-token baselines.

## Local acceptance

- 56 unit, integration, CLI-contract, identity, migration, concurrency,
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
  `elm_memory-0.3.0.dev0-py3-none-any.whl`.
- Its validation-build SHA-256 was
  `E4CD7F3583F74666BF58047696F19BEE611D0E780B90DEC50B2FB56F0550359C`.
- An isolated Python 3.11 target installation exposes version `0.3.0.dev0` and
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
