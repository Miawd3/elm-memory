# Phase 3 Readiness Evidence

Status: implementation merged, cross-platform CI verified, and live installation accepted

Date: 2026-08-26

Merge commit: `4e3b1a4e8870c5f74141384082e007c1bae36d91` (PR 8)

## Scope

This record covers the Phase 3 governed-memory implementation only: canonical
proposals, evidence references, claims, lifecycle events, temporal history,
contradictions, tombstones, transaction recovery, SQLite projections, the
operator skill, and sanitized documentation/demo assets.

It does not claim readiness for raw evidence storage, authenticated multi-user
access, MCP, embeddings, model summarization, a public package release, or a
public repository launch.

## Evidence summary

| Check | Result | Evidence |
|---|---:|---|
| Python compilation | pass | `python -m compileall -q src tests benchmarks` |
| Regression and Phase 3 tests | pass | 76 tests on Windows/Python 3.14 after final docs/code changes |
| Governed lifecycle demo | pass | 12/12 synthetic lifecycle, rebuild, privacy, doctor, and SQLite checks |
| Existing 50-case retrieval benchmark | pass | 50/50; 100% context hit rate; zero archive leaks; budget compliant |
| Operator skill validation | pass | Official `quick_validate.py`: `Skill is valid!` |
| Wheel build and clean install | pass | `elm_memory-0.4.0.dev0-py3-none-any.whl`; imported from isolated venv |
| Private-corpus compatibility copy | pass | 652 Markdown files, zero changed/added, zero rebuild/doctor errors |
| GitHub Windows/Linux Python matrix | pass | Two PR contexts and post-merge run 33010899876; Python 3.11–3.14 |
| Repository hygiene and workflow policy | pass | SHA-pinned actions, read-only token, unprivileged triggers, no secrets |
| Live local installation | pass | Package 0.4.0.dev0, matching skill, schema v2, doctor 0, idempotent sync, SQLite `ok` |

## Tested invariants

The automated suite covers:

- evidence reference → proposal → explicit accept → canonical claim;
- stable claim identity and exact section identity after rebuild;
- direct claim-ID outline/read/related behavior;
- valid-time and recorded-time history reconstruction;
- future, disputed, superseded, and deleted claims excluded from ordinary reads;
- explicit historical access without direct-ID policy bypass;
- contradiction reporting in history, doctor, and bounded context;
- exactly one terminal transition under concurrent acceptance;
- terminal reject/defer behavior;
- cross-project supersession refusal without partial mutation;
- claim, proposal, and evidence deletion with metadata-only tombstones;
- explicit recovery of an interrupted transaction;
- recovery refusal when a human edit changes an expected target hash;
- rollback and backup cleanup after injected transaction failure;
- committed-journal recovery finishes cleanup without rolling back accepted state;
- clean refusal of a newer canonical format;
- transactional v1-to-v2 derived-schema migration and rollback;
- immutable-record create-if-absent semantics on Windows;
- preservation of claim IDs during document-ID assignment.

## Sanitized lifecycle demo

`python benchmarks/run_governance_demo.py --assert-pass` uses a temporary copy of
the synthetic fixture and verifies all of the following:

1. a candidate remains pending until explicit acceptance;
2. an accepted claim becomes current;
3. supersession removes the old claim from ordinary retrieval;
4. the old claim remains available through explicit history;
5. the replacement claim becomes current;
6. both claims remain in canonical history;
7. deleting the disposable index and rebuilding preserves the replacement claim;
8. evidence metadata is reference-only and contains no payload field;
9. `doctor` returns zero issues;
10. SQLite `PRAGMA quick_check` returns `ok`.

The script deletes its temporary memory root after completion and does not read
or copy the user's private ELM.

## Private-corpus compatibility copy

A temporary local copy of the user's ELM was opened with the Phase 3 source and
then deleted after validation. The live ELM was not mutated. Results:

- 652 Markdown files before and after; zero byte changes and zero additions;
- schema v1 projection opened/migrated and a full rebuild indexed all 652 files;
- zero sync errors, zero rebuild errors, and zero doctor issues before/after;
- strict `local skill creation` search returned
  `10_shared/general_notes/CODEX_SKILL_INSTALLATIONS.md`;
- outline, exact stable-key read, and related resolved that same document;
- a broad ordinary search returned zero `backups`/`99_archive` paths;
- final sync reported `changed: 0`, `removed: 0`, `unchanged: 652`;
- SQLite `PRAGMA quick_check` returned `ok`;
- the pre-Phase-3 corpus correctly projected zero claims and proposals.

## Release gate

Every Phase 3 release gate passed. PR 8 was merged only after the complete
Windows/Linux matrix succeeded; the post-merge `main` matrix then passed again.
The live installation preserved restorable package, skill, index, and milestone
backups and finished with clean `sync`, zero-issue `doctor`, and SQLite integrity.

## Known boundaries

- `actor` and `sensitivity` are provenance/governance labels, not authentication.
- Evidence records contain source locators and hashes, not retained source bytes.
- A hash does not establish truth, authorship, or source availability.
- Physical deletion cannot erase Git history, filesystem snapshots, or external
  backups; a deleted claim is reconstructable only as tombstone metadata.
- Deterministic lexical retrieval still cannot recover absent synonyms.
