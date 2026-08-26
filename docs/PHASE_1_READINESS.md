# Phase 1 Readiness

Status: implementation-complete and hosted CI verified

Evaluated: 2026-08-26

Package version: `0.2.0.dev0`

## Implemented scope

- Apache-2.0 license and SPDX package metadata.
- Index schema version `1` with transactional migration from the unversioned
  Phase 0 projection and refusal of unsupported newer schemas.
- Optional `ELM ID: doc_<uuid4>` parsing.
- Explicit `elm ids assign --dry-run|--apply` with targeted backups, source-hash
  revalidation, atomic replacement, post-write validation, and batch rollback.
- Rebuild-stable section keys bound to a document UID or normalized relative
  path, while legacy integer references remain accepted.
- Archive, project, and namespace policy across search, outline, read, and
  related, including guessed numeric IDs and stable keys.
- SQLite busy timeout, short per-document index transactions, a cross-platform
  single-writer lock, explicit stale recovery, and metadata-only recovery logs.

## Local verification

- 45 unit, integration, CLI-contract, migration, identity, read-policy,
  concurrency, CI-policy, and repository-hygiene tests pass on Windows with
  Python 3.14.3.
- The killed-writer Windows regression passes five consecutive focused runs.
- The sanitized retrieval benchmark passes 20/20 with MRR 1.0.
- Python compilation passes for `src`, `tests`, and `benchmarks`.
- The updated `elm-memory-operator` Agent Skill passes `quick_validate.py`.
- A wheel builds successfully as `elm_memory-0.2.0.dev0-py3-none-any.whl` and
  contains `License-Expression: Apache-2.0`, `LICENSE`, and `NOTICE`. Its local
  SHA-256 is `8484A266940CBD34D9FED0CD93EAE532B84F1C8AE8BA380B6A924EE255FD88EB`.
- Installing that wheel into an isolated target exposes version `0.2.0.dev0`
  and the expected `search` and `ids` CLI commands.
- A temporary copy of the complete private ELM rebuilt 512 Markdown files and
  4,033 sections with schema version 1, zero indexing errors, zero `doctor`
  issues, and zero Markdown SHA-256 changes.
- `elm ids assign --dry-run` on that full copy planned 139 active document IDs,
  changed zero files, and created no backup batch.
- The temporary private-corpus probe was removed after verification; the live
  ELM was not migrated or modified.

## Evidence limits

- The full-corpus ID operation was dry-run only. Apply/rollback behavior is
  covered by synthetic fixtures, including injected mid-batch failure.
- The concurrency lock coordinates cooperating processes with the same
  filesystem permissions; it is not authenticated authorization.
- The benchmark remains synthetic and lexical and does not measure billed model
  tokens or end-to-end agent task success.
- The hosted matrix proves supported interpreter and OS compatibility for the
  sanitized suite; it does not reproduce the private full-corpus probe.

## Hosted acceptance result

Phase 1 commit `488ab2970e37332e1538fef18117f1a62a949ee5` passed both
GitHub Actions contexts for draft PR 4:

- push run `32948155121`: nine of nine jobs passed;
- pull-request run `32948172792`: nine of nine jobs passed.

Each run covered the sanitized benchmark plus Python 3.11-3.14 on Ubuntu and
Windows. Claims, evidence snapshots, context packing, and MCP remain outside
this phase.

## Remaining public-release work

1. Resolve the final repository/distribution-name and minimum-Python decisions.
2. Review and merge the Node.js 24-native Dependabot action updates without
   weakening immutable pins, read-only permissions, or unprivileged triggers.
3. Enable GitHub private vulnerability reporting and replace the provisional
   reporting text in `SECURITY.md`.
4. Review the documentation for an external audience before changing repository
   visibility or creating a public tag.
