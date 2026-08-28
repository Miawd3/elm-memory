# Heterogeneous agent pilot

Status: executable local pilot; Phase 5B remains inactive

Date: 2026-08-27

## Purpose

This pilot measures whether different coding-agent hosts can recover the same
fact from the same disposable ELM root, and records the usage counters each host
reports. It compares three evidence conditions within each host:

- `elm`: the host retrieves through the seven read-only ELM MCP tools;
- `full_corpus`: the same active synthetic Markdown corpus is placed directly
  in the initial prompt;
- `no_memory`: no project evidence is supplied, so the correct answer is
  `INSUFFICIENT_EVIDENCE`.

The pilot is an evaluation harness, not a new trust capability. It does not
enable proposal tools, write accepted memory, or implement Phase 5B.

## Supported routes

The executable supports Codex, Gemini through Antigravity, Claude through
Antigravity, and an optional direct Claude Code route. Every provider run uses
a separate empty working directory. Only the ELM MCP subprocess receives the
temporary synthetic memory-root path, so the fixture is not available through
an ordinary relative read from the host workspace.

The harness copies only the ELM Python package into a separate temporary runtime
and removes repository `PYTHONPATH` from provider processes. Codex and
Antigravity run in streamed-event mode: `elm` must show only calls to the named
read-only MCP server and must include `status` plus `context`; the other two
conditions must show zero tool calls. Missing provenance is a non-pass result,
even when the answer is correct.

The default matrix is one case across Codex, Gemini-Antigravity, and
Claude-Antigravity in all three conditions. Direct Claude Code is opt-in because
it has a separate authentication state and supports its own per-run USD cap.

## Evaluation integrity

The expected answer, expected source path, and expected heading live only in
`benchmarks/heterogeneous_cases.json`, which is evaluator-side. The response
schema contains only field types and limits; it contains no expected values or
`const` oracle. ELM and no-memory prompts do not contain the expected answer or
locator. The full-corpus prompt necessarily contains the evidence being tested.

The evaluator checks the supporting sentence by normalized exact match, plus
the source path, stable section key, and evidence status after the host returns.
For a passing quality result, the report retains
only the closed four-field synthetic response; failed or unexpected response
payloads are discarded. Source locators must be relative Markdown paths without
traversal. The remaining report contains allowlisted usage counters, versions,
durations, and boolean checks. Raw provider output, credentials, conversation
IDs, and session IDs are not retained. Canonical Markdown hashes must be
unchanged before and after the matrix.

## Usage accounting

Provider-native counters are preserved under their original names and marked
`cross_provider_comparable: false`. The harness does not pretend that OpenAI,
Google, and Anthropic token or cache counters have identical semantics. It
computes paired ELM-versus-full-corpus differences only within one route and one
case. The model-neutral initial-prompt estimate is reported separately.

These are end-to-end host-run measurements. ELM therefore includes the model's
tool-selection turns and any repeated host scaffold, while full-corpus can
finish in a single turn. That is the real current integration cost, not noise
that the harness removes.

## Safe operation

Static CI-safe validation calls no model:

```bash
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
```

Real calls require the explicit `--execute` flag. The default is bounded to at
most 12 provider runs and 300 seconds per run:

```bash
python benchmarks/run_heterogeneous_pilot.py \
  --execute \
  --routes codex gemini-antigravity claude-antigravity \
  --conditions elm full_corpus no_memory \
  --case-ids orion_storage \
  --max-runs 9 \
  --assert-pass
```

Antigravity headless ELM runs need temporary permission for exactly the seven
read tools on the dedicated `elm_benchmark` server. Back up the settings file,
add only these rules for the run, remove them immediately afterward, and verify
that the restored file matches the backup byte-for-byte:

```json
{
  "permissions": {
    "allow": [
      "mcp(elm_benchmark/status)",
      "mcp(elm_benchmark/search)",
      "mcp(elm_benchmark/context)",
      "mcp(elm_benchmark/read)",
      "mcp(elm_benchmark/related)",
      "mcp(elm_benchmark/history)",
      "mcp(elm_benchmark/stats)"
    ]
  }
}
```

Do not leave these permissions in place, use a wildcard, or use a dangerous
bypass. The permission is unnecessary for
the full-corpus and no-memory conditions. The harness does not edit host
settings or perform login; those remain operator-controlled actions.

## First observation

The strict paired Codex storage-case run passed quality, complete telemetry,
streamed tool provenance, oracle separation, and immutability gates. Codex
reported 85,282 input-plus-output tokens for ELM and 19,618 for the full-corpus
condition, an ELM/full-corpus ratio of 4.3471. ELM showed only `status` and
`context` calls; both controls showed zero tools, and no-memory correctly
returned `INSUFFICIENT_EVIDENCE`.

Gemini through Antigravity also returned correct responses for all three
conditions with complete provider counters. In the strict streamed matrix its
totals were 44,608 tokens for ELM and 32,489 for full corpus, a ratio of 1.3730.
However, Antigravity also emitted built-in `list_dir`/`view_file` activity in the
ELM condition beyond its expected MCP broker reads. The strict provenance gate
therefore rejected the route, and this ratio is only a non-attested quality
observation. The Claude-Antigravity route reached its provider quota before it
could return a schema-valid benchmark response, so no Claude efficiency ratio
is claimed. Direct Claude Code was installed but not authenticated and was not
used as a substitute.

This is a useful negative result, not evidence that ELM is generally less
efficient. The synthetic fixture has only six active Markdown files and fits
comfortably in one prompt; the ELM condition pays multiple host/tool turns plus
the coding-agent scaffold. ELM's expected advantage is bounded retrieval as
history grows. That scaling claim remains unproven until a corpus-size curve
uses repeated, counterbalanced runs at sizes where full-corpus injection becomes
materially expensive or impossible.

## Next evidence step

The deterministic corpus-size curve is implemented in
[CORPUS_SIZE_CURVE.md](CORPUS_SIZE_CURVE.md). It generates nested synthetic
corpora at several token targets, counterbalances condition and size order,
repeats exact within-route pairs, and requires conservative statistical and
provenance gates before reporting a bounded crossover. The original no-memory
control remains in this pilot; the curve focuses on the two conditions whose
cost can cross as corpus size grows. Phase 5B remains separately blocked on an
independent trusted verifier.

This workspace separation is an evaluation-integrity control, not a security
boundary against a hostile local host. A client that can inspect its process
configuration or arbitrary readable OS paths may still discover the temporary
root. The pilot therefore tests cooperative coding-agent behavior under an
explicit tool-use contract; it does not claim adversarial data isolation.
