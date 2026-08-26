# Phase 4 readiness record

Date: 2026-08-26

Status: local acceptance complete; cross-platform CI is the merge gate

## Accepted boundary

Phase 4 adds a local read-only MCP adapter and a heterogeneous-host demo. It
does not add MCP mutation, automatic synchronization, remote transport,
authentication, embeddings, model summarization, or raw evidence storage.

The adapter exposes exactly:

```text
status  search  context  read  related  history  stats
```

All tools delegate to the CLI JSON contract under one fixed root. MCP reads use
an existing compatible index, never create or migrate it, never synchronize
Markdown, and never write a retrieval trace.

## Local verification

Environment:

- Windows;
- Python 3.14.3;
- SQLite 3.50.4 with FTS5;
- MCP Python SDK 2.1.1;
- Antigravity CLI 1.1.21;
- Codex CLI 0.149.0.

Verification commands:

```text
python -m compileall -q src tests benchmarks examples/two-agent-handoff
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
python benchmarks/run_governance_demo.py --assert-pass
python benchmarks/run_mcp_demo.py --assert-pass
python examples/two-agent-handoff/run_hosts.py --assert-pass
python -m build
python .../skill-creator/scripts/quick_validate.py skills/elm-memory-operator
```

Observed results:

- 86 unit and integration tests passed after the readiness document was added;
- sanitized retrieval benchmark: 50/50 cases, 100% context hit rate, zero
  archive leaks, and complete budget compliance;
- governed lifecycle demo: all 12 checks passed;
- in-process MCP demo: all 14 checks passed;
- skill validator: `Skill is valid!`;
- source compilation completed without errors;
- the sdist and universal wheel built successfully, and a clean temporary venv
  installed `elm-memory[mcp]` from the wheel, rebuilt the fixture, reported a
  healthy status, and exposed the `elm-mcp` entry point.

## Heterogeneous-host acceptance

The host harness copied the synthetic Orion fixture into a temporary workspace,
rebuilt its disposable index, and independently prompted two model hosts through
the same local MCP command. Antigravity used the first available high-effort
Gemini model (Gemini 3.7 Flash High in this run); Codex used its configured
model.

Both hosts recovered:

```text
database: PostgreSQL 17
path: 20_projects/orion/DECISIONS.md
section_key: section_aa2e2681-7dae-509a-b98c-30ea67c1bbf2
```

The harness passed `fixture_rebuild_clean`, both host recovery checks, and
`same_source_identity`. It retained only versions, booleans, the synthetic
source identity, bounded errors, and elapsed time.

Claude Code remains a configured optional host in the same harness. It was not
counted toward acceptance because the installed CLI's OAuth session expired
before any model token or MCP tool call. This is an external authentication
state, not evidence of adapter compatibility or incompatibility.

## Failure and adversarial checks

- A missing index becomes a bounded MCP tool error and does not create `.elm`.
- The same failure does not break a subsequent direct CLI rebuild/read.
- MCP reads leave canonical Markdown, the SQLite bytes, and trace directories
  unchanged in controlled tests.
- CLI and MCP return the same stable section identities and policy-filtered
  scope results.
- `status` detects changed content even when byte size and mtime are preserved,
  because freshness includes SHA-256 comparison.
- The exact seven-tool list and read-only annotations are asserted by tests.
- Existing GitHub Actions policy tests continue to enforce read-only token
  permissions, unprivileged triggers, pinned action SHAs, and disabled checkout
  credential persistence.

## Merge acceptance

Phase 4 is ready to merge when the repository's Windows/Linux GitHub Actions
matrix passes from this branch. A post-merge local install must then verify:

1. `elm status --json` reports a compatible, healthy live index;
2. a local MCP client can list exactly the seven read tools;
3. `elm sync --json` reports no unexpected canonical changes;
4. `elm doctor --json --no-sync` reports zero issues;
5. SQLite `PRAGMA quick_check` returns `ok`.

Phase 5 remains inactive until controlled mutation and trusted actor binding are
separately designed and explicitly authorized.
