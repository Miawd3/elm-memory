# ELM Memory

ELM is a local, inspectable project-memory and deterministic retrieval layer for coding agents. Canonical knowledge stays in Markdown; SQLite FTS5 is a disposable index that can be deleted and rebuilt.

This repository is in **Phase 1 / pre-alpha**. It provides the tested deterministic retrieval engine plus rebuild-stable public locators, versioned disposable-index migrations, explicit document-ID assignment, consistent read-policy filtering, and cross-platform single-writer coordination. Bounded context packets, governed claims, and MCP are planned but are not implemented yet.

## Why ELM

- local Markdown remains readable without ELM;
- no hosted service, model API, embeddings, or database daemon is required;
- search returns compact section manifests before exact section reads;
- backups and `99_archive` are excluded from ordinary retrieval;
- the same CLI can be called by Codex, Claude, Gemini, Cursor, or any terminal-capable agent;
- `doctor` exposes metadata, link, and duplicate-title problems instead of silently hiding them.

## Requirements

- Python 3.11 or newer;
- a Python build with SQLite FTS5 enabled;
- Windows or Linux for the CI targets.

Python 3.11 remains the provisional minimum until the first public release tag.

## Install for development

```bash
python -m venv .venv
```

Activate the environment, then install the local package:

```bash
python -m pip install --no-deps -e .
```

No runtime Python dependencies are required.

## Five-minute demo

Build an index over the sanitized example:

```bash
elm rebuild --root examples/two-agent-handoff/memory --json
```

Search for a task-relevant section:

```bash
elm search "Aurora PostgreSQL" --root examples/two-agent-handoff/memory --json
```

Read the returned stable `section_key` (legacy numeric section IDs remain accepted):

```bash
elm read SECTION_KEY --root examples/two-agent-handoff/memory --json
```

Check health:

```bash
elm doctor --root examples/two-agent-handoff/memory --json
```

You may also run the package without installing a console script:

```bash
python -m elm_memory --help
```

## Root discovery

ELM resolves its Markdown root in this order:

1. `--root PATH`;
2. the `ELM_ROOT` environment variable;
3. a path stored in `~/.elm-system/root`;
4. the current directory when it contains `00_registry`.

There is no machine-specific default path in the public engine.

## Retrieval flow

```text
task
  -> elm search
  -> compact candidate manifests
  -> elm read for one exact section
  -> elm outline or elm related only when expansion is needed
```

Available commands:

```text
sync  rebuild  search  outline  read  related  stats  doctor  ids assign
```

The index lives at `<root>/.elm/index.sqlite`. It is derived state and must never be the only location of durable knowledge.

## Stable identity and explicit mutation

Search, outline, read, and related results expose legacy integer IDs together with:

- optional `document_uid` values stored as `ELM ID: doc_<uuid4>` in Markdown;
- derived `section_key` values that survive index deletion and rebuild;
- `section_namespace`, which reports whether the key is document-UID-bound or path-bound.

Indexing never inserts IDs into Markdown. Preview the explicit migration first:

```bash
elm ids assign --root /path/to/memory --dry-run --json
```

Apply it only after reviewing the plan:

```bash
elm ids assign --root /path/to/memory --apply --json
```

The apply operation holds the ELM writer lock, creates targeted originals under
`<root>/backups/elm-ids-<timestamp>/`, uses atomic file replacement, validates
every inserted ID, and rolls back already-written documents if the batch fails.
Backups and archives are skipped unless `--include-archive` is explicit.

## Read policy and concurrency

`search`, `outline`, `read`, and `related` apply the same archive, project, and
namespace filters. A numeric ID or stable key cannot bypass those filters.
`--include-archive` is an explicit opt-in; `--project` and `--namespace` narrow
all relevant read paths.

Multiple no-sync readers may run concurrently. Index writes and canonical
mutations use `<root>/.elm/writer.lock`; a competing writer waits up to
`--lock-timeout` or exits cleanly. Recovery of a dead writer's lock requires the
explicit `--recover-stale-lock` flag and is recorded in derived runtime state.

## Agent Skill

A host-neutral Agent Skill is available at `skills/elm-memory-operator`. It teaches compatible coding agents when to retrieve ELM context and how to preserve authority boundaries. The CLI remains usable without skill support.

Validate the skill with the official Codex skill validator when it is available:

```bash
python /path/to/skill-creator/scripts/quick_validate.py skills/elm-memory-operator
```

## Development checks

```bash
python -m compileall -q src tests benchmarks
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
```

The sanitized benchmark contains 20 deterministic retrieval, project-filter, and archive-isolation cases. It is a regression suite, not a claim of general semantic-memory quality or billed-token savings.

## Architecture boundary

The corrected v1 architecture and staged roadmap are documented in
[docs/ELM_V1_ARCHITECTURE_DRAFT.md](docs/ELM_V1_ARCHITECTURE_DRAFT.md). The exact
implemented identity, migration, policy, and locking contracts are summarized in
[docs/PHASE_1_FOUNDATIONS.md](docs/PHASE_1_FOUNDATIONS.md).

Phase order:

1. public baseline and regression protection;
2. stable identity, migrations, and concurrency;
3. bounded context packets and privacy-safe traces;
4. governed proposals, evidence references, claims, and temporal history;
5. read-only MCP, followed later by controlled mutation;
6. optional semantic retrieval only after measured deterministic failures.

## License, privacy, and publication status

This repository contains only synthetic fixtures. Do not add personal memory snapshots, raw chats, credentials, private keys, terminal dumps, or private bootstrap archives.

ELM is licensed under the [Apache License 2.0](LICENSE). It permits use,
modification, redistribution, and commercial use under its notice and license
conditions, and includes an explicit contributor patent grant.

The GitHub repository remains a private pre-release while Phase 1 is validated
and the external-facing release documentation is completed.

## Current limitations

- document UIDs are optional; without one, a section key is path-bound and changes when its document moves;
- namespace and archive filters are governance controls, not authentication between mutually untrusted OS users;
- canonical mutations are intentionally serialized through one writer rather than supporting simultaneous writers;
- there is no `elm context`, claims lifecycle, evidence store, or MCP server yet.
