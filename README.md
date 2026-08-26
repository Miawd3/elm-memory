# ELM Memory

ELM is a local, inspectable project-memory and deterministic retrieval layer for coding agents. Canonical knowledge stays in Markdown; SQLite FTS5 is a disposable index that can be deleted and rebuilt.

This repository is in **Phase 0 / pre-alpha**. It currently provides the tested v0 retrieval engine and sanitized fixtures. Stable public IDs, bounded context packets, governed claims, and MCP are planned but are not implemented yet.

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
- Windows or Linux for the Phase 0 CI targets.

The minimum Python version is provisional until the first public release decision is ratified.

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

Read the returned section ID:

```bash
elm read SECTION_ID --root examples/two-agent-handoff/memory --json
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
sync  rebuild  search  outline  read  related  stats  doctor
```

The index lives at `<root>/.elm/index.sqlite`. It is derived state and must never be the only location of durable knowledge.

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

The corrected v1 architecture and staged roadmap are documented in [docs/ELM_V1_ARCHITECTURE_DRAFT.md](docs/ELM_V1_ARCHITECTURE_DRAFT.md).

Phase order:

1. public baseline and regression protection;
2. stable identity, migrations, and concurrency;
3. bounded context packets and privacy-safe traces;
4. governed proposals, evidence references, claims, and temporal history;
5. read-only MCP, followed later by controlled mutation;
6. optional semantic retrieval only after measured deterministic failures.

## Privacy and publication status

This repository contains only synthetic fixtures. Do not add personal memory snapshots, raw chats, credentials, private keys, terminal dumps, or private bootstrap archives.

No license has been selected yet. Until a `LICENSE` file is added, this repository is a private pre-release working tree and must not be presented as an open-source release.

## Current limitations

- integer document and section IDs are not stable across full rebuilds;
- `scope` metadata is searchable but is not an access-control boundary;
- simultaneous canonical writers are not yet supported;
- read-policy enforcement across guessed IDs belongs to Phase 1;
- there is no `elm context`, claims lifecycle, evidence store, or MCP server yet.
