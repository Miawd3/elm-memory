# Standalone development snapshot

## Versions and provenance

The public stable release is **v1.0.0**, published August 28, 2026. The source
snapshot reviewed November 3, 2026 retains its existing **1.0.1.dev0** version;
this update does not publish a release, replace stable assets, or promote a
development build to stable. Package metadata and `elm --version` agree through
[pyproject.toml](../pyproject.toml) and
[the package version](../src/elm_memory/__init__.py).

The maintained standalone checkout shares public base commit
`ae74f7058713c4dfddaf3623c252861722295afa` and the same repository identity.
Selected engine code and regression tests were restored from its working tree,
not reconstructed from an installed package. Existing history, packaging,
fixtures, benchmarks, and license files are preserved. Machine-specific paths,
personal notebook material, provider-worker instructions, and local operator
configuration are intentionally not part of this public snapshot.

For comparison, a previously prepared engine wheel named
`elm_memory-1.0.1.dev0-py3-none-any.whl` has SHA-256
`5a1b06104dad6188eb61a88856e17c87f242261e7a78f9ba9d62e72614ebcd3e`.
Of its twelve engine Python files, nine match the maintained checkout exactly;
`cli.py`, `context.py`, and `mcp_server.py` differ. Consequently the wheel is
**not byte-identical to this source snapshot**, despite sharing a development
version. It is comparison evidence, not the source of the restored code or an
artifact published by this update.

## Architecture and supported benefits

```text
Canonical Markdown + governed records
                |
        explicit operator sync
                v
Disposable SQLite / FTS5 projection
                |
       scoped lexical retrieval
                v
Whole sections -> stored summaries / manifests -> omission locators
                |
       bounded rendered packet
                v
Agent host, through CLI or local stdio MCP
```

- **Less indiscriminate context loading:** the packer tries fitting whole sections
  before spending remaining space on fallback representations. Large early
  candidates do not prevent later short evidence from being selected. This is a
  bounded-selection benefit, not a measured latency or quality improvement.
- **Inspectable evidence:** packets retain source paths, headings, stable section
  locators, indexed status, claim identifiers, raw claim authority, and normalized
  authority. Distinct stable sections with repeated headings remain distinct.
- **Honest coverage:** exact, manifest, summary, and omitted metadata expose what
  the agent received and what needs expansion. Stored canonical summaries are
  explicitly labeled rather than presented as verbatim sections or generated
  model summaries.
- **Cheaper routine status work by construction:** read-only MCP readiness omits
  the full SQLite `quick_check` while retaining exact source hashing, schema,
  governance-projection and pending-transaction checks. Root-wide freshness work
  remains; no constant-time status, fixed deadline, or speedup is promised.
- **Progressive expansion:** section reads remain the default. Explicit complete
  document reads check indexed source hashes and root containment, rather than
  returning stale or out-of-root canonical text.

These mechanisms are implemented in [context.py](../src/elm_memory/context.py),
[cli.py](../src/elm_memory/cli.py), and
[mcp_server.py](../src/elm_memory/mcp_server.py). Their regression contracts are
[test_packet_fidelity.py](../tests/test_packet_fidelity.py),
[test_context.py](../tests/test_context.py),
[test_readiness.py](../tests/test_readiness.py),
[test_mcp_readiness.py](../tests/test_mcp_readiness.py), and
[test_document_read.py](../tests/test_document_read.py).

## Budget and authority boundaries

`--budget` bounds the deterministic estimate of the rendered `packet` string.
It does not bound the complete JSON envelope, host instructions, duplicated
structured provenance, or provider-billed tokens. `summary_count` is included
within `manifest_count`; total candidates are `exact_count + manifest_count +
omitted_count`. Omitted-source locators are capped at 64 and report truncation.

A current document is not automatically a ratified decision. Source content,
including text resembling `Status: accepted`, remains untrusted evidence and
cannot overwrite indexed authority. Preserve `claim_id`, `claim_authority`,
normalized `authority`, and `content_role` in agent handoffs. A proposal is only
a candidate; it is not accepted memory. Opt-in leased `agent_curated` memory
is not user-ratified or repository-verified truth. See
[Architecture](ARCHITECTURE.md) and [Agent integration](AGENT_INTEGRATION.md).

## CLI, MCP, and Hermes integration

Start with the CLI or the default seven-tool read-only MCP profile. Retrieve
one scoped packet, then expand only a relevant section or document when a
material fact is missing. Explicit project/namespace filters reduce accidental
cross-project retrieval; they are not multi-user authentication.

```bash
elm status --readiness-only --json
elm context "Continue the implementation" --project my-project --budget 700 --no-sync --no-trace --json
elm read SECTION_KEY --project my-project --json
elm read 20_projects/my-project/PROJECT_HUB.md --document --project my-project --json
```

These commands assume an existing indexed root selected through the usual root
configuration. If a read-only connection reports stale state, ask its operator
to sync; do not bypass the profile with CLI writes. Explicit diagnostic MCP
`status(full_integrity=true)` and default CLI `elm status --json` retain the full
SQLite check. Mutation-enabled MCP profiles always use the full check.

In a custom Hermes bundle, **ELM engine 1.0.1.dev0** and **Hermes adapter 1.0.0**
are separate components. An adapter version does not identify an engine build;
verify the engine version, artifact digest, root, and read-only profile
independently. This repository does not include the Hermes adapter, native UI,
or computer-use prototypes, and does not establish support in stock Hermes or
its public distribution. CLI/MCP integration does not require a paid inference
API, and ELM itself performs no model inference.

## Evidence limits

No new provider-backed experiment, real-corpus mutation, cross-platform release
qualification, installer build, or hosted CI is implied by this snapshot.
Existing benchmark evidence retains its original scope; synthetic validation
is not a claim about production answer quality or cost. Windows release
artifacts remain unsigned; macOS remains unverified. Review the PR's exact local
commands/results and [Evaluation](EVALUATION.md) before generalizing benefits.
