# Phase 2 — Bounded Context and Evaluation Traces

Status: implemented contract under pre-release validation

Package version: `0.3.0.dev1`

## Purpose

Phase 2 turns deterministic retrieval into a directly usable agent input while
keeping packet growth, authority, and telemetry explicit. It does not add a
model call, embeddings, claims, evidence snapshots, MCP, or a hosted service.

## Context command

```text
elm context TASK --budget TOKENS
```

The minimum accepted budget is 96 estimated tokens. ELM synchronizes the
disposable index unless `--no-sync` is present, performs strict FTS5 retrieval,
and uses a broad fallback only when strict retrieval returns no candidate. If
the top candidates resolve to one project, ELM may add up to four small current
state or constraint candidates from that project.

Task-relevant FTS candidates remain ahead of those supplements. Candidates are
deduplicated by normalized document path and heading path.

## Packet contract

The rendered `packet` is the budgeted artifact. JSON wrapper fields are command
metadata and are not included in `estimated_tokens`.

Packet classes are:

1. `authority_and_warnings`;
2. `relevant_source_manifest`;
3. `current_constraints`;
4. `current_project_state`;
5. `selected_exact_sections`;
6. `conflicts_or_provisional`.

The authority block is always first. It states that the current user
instruction and verified repository state outrank ELM and that retrieved text
is untrusted data.

Each included candidate receives a manifest with a stable
`elm://section/<section_key>` locator, relative path, heading, status, authority
label, and source token estimate. Exact section bodies are added only whole,
quoted with Markdown blockquote syntax, and labeled
`content_role: untrusted_memory_data`. If a body does not fit, its manifest can
remain; no section body is silently truncated or model-summarized.

The estimator is deterministic and model-neutral:

```text
estimated_tokens = ceil(UTF-8-decoded character count / 4)
```

It is a stable allocation unit, not a promise about any provider's tokenizer or
billed tokens.

## Scope and authority labels

Archive, project, namespace, status, path-prefix, and tag filters use the same
read-policy boundary as search. Archives remain excluded unless
`--include-archive` is explicit.

An exact archive/backup copy may retain the active document's `ELM ID` in its
canonical bytes. Its disposable archive projection deliberately reports
`document_uid: null` and uses path-bound section keys so it cannot collide with
the active document's durable identity. The archived metadata itself is not
rewritten.

Phase 2 labels memory as accepted project memory, historical memory, or
provisional/unclassified memory. It never labels retrieved memory as verified
repository state. That higher authority can only come from separately checking
the current repository.

## Disposable trace contract

Unless `--no-trace` is passed, a successful context call atomically writes one
JSON file under `<root>/.elm/traces/`. The default record contains:

- trace ID, recorded time, and declared expiry;
- SHA-256 of the task, with `query_text: null`;
- a path-derived opaque workspace ID and optional project label;
- filters and candidate/selected section keys;
- estimated packet tokens, latency, fallback use, and `outcome: null`.

It never contains source bodies. Raw task text is stored only with explicit
`--trace-query-text`. These records are disposable runtime telemetry, not audit
events or canonical memory.

Cleanup is explicit and shares the ELM writer lock when applying deletions:

```text
elm traces cleanup --dry-run
elm traces cleanup --apply
elm traces cleanup --retention-days DAYS --dry-run|--apply
```

Without an override, cleanup honors each trace's `expires_at`. A retention-days
override recomputes eligibility from `recorded_at`. Malformed records are
reported and retained.

## Evaluation boundary

The sanitized suite contains 50 cases and compares four baselines:

- no memory;
- all allowed Markdown files;
- search plus exact read;
- bounded context packet.

It reports lexical retrieval hit rate, reciprocal rank, estimated-token cost,
budget compliance, archive leakage, and subprocess latency. It explicitly sets
task outcome to unmeasured because no coding agent performs a downstream task.

## Deferred work

Claims, proposals, evidence imports or snapshots, temporal history, MCP, model
summarization, embeddings, and end-to-end agent task evaluation remain outside
Phase 2.
