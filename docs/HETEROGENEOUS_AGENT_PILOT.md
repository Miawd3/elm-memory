# Heterogeneous agent pilot

Status: executable local pilot; Phase 5B is outside the active roadmap

Date: 2026-08-28

## Purpose

This pilot measures whether different coding-agent hosts can recover the same
fact from the same disposable ELM root, and records the usage counters each host
reports. It compares three evidence conditions within each host:

- `elm`: ELM supplies a bounded, source-linked packet. Codex and Claude Code
  retrieve it through the seven read-only MCP tools; Antigravity defaults to a
  separately attested host-brokered adapter because CLI 1.1.22 cannot safely
  confine direct workspace MCP discovery;
- `full_corpus`: the same active synthetic Markdown corpus is placed directly
  in the initial prompt;
- `no_memory`: no project evidence is supplied, so the correct answer is
  `INSUFFICIENT_EVIDENCE`.

The pilot is an evaluation harness, not a new trust capability. It does not
enable proposal tools, write accepted memory, or implement Phase 5B.

## Supported routes

The executable supports Codex, Gemini through Antigravity, Claude through
Antigravity, and an optional direct Claude Code route. Every provider run uses
a separate empty working directory. Codex and Claude Code use direct read-only
MCP. In the default Antigravity mode, the trusted harness—not the model—calls
`status` and `context` against the synthetic root, validates the bounded packet,
and injects that packet into a text-only provider prompt. Antigravity receives
neither the root path nor an attached workspace.

The harness copies only the ELM Python package into a separate temporary runtime
and removes repository `PYTHONPATH` from provider processes. Codex direct-MCP
`elm` runs must show only the named read-only server and must include `status`
plus `context`. Host-brokered Antigravity, full-corpus, and no-memory runs must
show exactly zero provider tool calls. Missing provider or broker provenance is
a non-pass result even when the answer is correct.

The report calls the two paths `direct-mcp` and `host-brokered-context`.
Brokered responses use `evidence_status='provided'`: ELM retrieved the packet,
but the model received it as supplied evidence. Direct MCP continues to use
`evidence_status='retrieved'`.

The default matrix is one case across Codex, Gemini-Antigravity, and
Claude-Antigravity in all three conditions. Direct Claude Code is opt-in because
it has a separate authentication state and supports its own per-run USD cap.

## Evaluation integrity

The expected answer, expected source path, and expected heading live only in
`benchmarks/heterogeneous_cases.json`, which is evaluator-side. The response
schema contains only field types and limits; it contains no expected values or
`const` oracle. Direct-MCP ELM and no-memory prompts do not contain the expected
answer or locator. A host-brokered ELM prompt and a full-corpus prompt
necessarily contain evidence, but neither receives evaluator-only expected
values.

The evaluator checks the supporting sentence by normalized exact match, plus
the source path, stable section key, and evidence status after the host returns.
For a passing quality result, the report retains
only the closed four-field synthetic response; failed or unexpected response
payloads are discarded. Source locators must be relative Markdown paths without
traversal. The broker receipt retains only hashes and bounded metadata: exact
operations and exit codes, health/freshness, project/archive/trace policy,
packet budget and size, source counts, packet/final-prompt/case/task/project/
root/source-code bindings, and retrieval latency. It retains no packet body or
raw query. The remaining report contains allowlisted usage counters, versions,
durations, and boolean checks. Raw provider output, credentials, conversation
IDs, and session IDs are not retained. Canonical Markdown hashes must be
unchanged before and after the matrix.

## Usage accounting

Provider-native counters are preserved under their original names and marked
`cross_provider_comparable: false`. The harness does not pretend that OpenAI,
Google, and Anthropic token or cache counters have identical semantics. It
computes paired ELM-versus-full-corpus differences only within one route and one
case. The model-neutral initial-prompt estimate is reported separately.

For direct MCP, ELM includes model tool-selection turns and host scaffold. For
host-brokered context, `retrieval_elapsed_ms`, `provider_elapsed_ms`, and their
combined `elapsed_ms` are reported separately; provider token counters cover
the injected prompt, not the local ELM subprocess. Results may be paired only
within one route, model, adapter, case, size, and repetition. Direct-MCP and
host-brokered token or latency results must never be pooled.

## Safe operation

Static CI-safe validation calls no model:

```bash
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
```

Real calls require the explicit `--execute` flag. The default Antigravity
adapter is `host-brokered-context`; it needs no Antigravity MCP permission and
is bounded to at most 12 provider runs and 300 seconds per run:

```bash
python benchmarks/run_heterogeneous_pilot.py \
  --execute \
  --routes codex gemini-antigravity claude-antigravity \
  --conditions elm full_corpus no_memory \
  --case-ids orion_storage \
  --max-runs 9 \
  --assert-pass
```

The adapter acquires the ELM writer lock while it verifies `status` and calls
`context --no-sync --no-trace`, checks canonical Markdown before and after,
rejects archived or unsafe locators, binds the exact packet and final prompt by
SHA-256, and fails before provider invocation on any mismatch. The provider
stream must then attest zero tool calls.

`--antigravity-adapter direct-mcp` retains the original experimental route.
Only that mode needs temporary permission for exactly the seven read tools on
the dedicated `elm_benchmark` server. Back up and restore the settings file
byte-for-byte:

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
bypass. The permission is unnecessary for the default brokered adapter and both
controls. The harness does not edit host settings or perform login; those
remain operator-controlled actions.

Antigravity permissions are execution gates, not tool-schema pruning. The ELM
prompt names the MCP `read` tool explicitly and forbids built-in file or shell
tools as defense in depth, but prompt compliance is never treated as the
security boundary. The streamed zero-tolerance provenance audit remains the
authoritative per-run gate. See the current Antigravity documentation for
[fine-grained permissions](https://antigravity.google/docs/cli/permissions) and
[workspace MCP configuration](https://antigravity.google/docs/mcp).

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

### Antigravity 1.1.22 confinement recheck — 2026-08-28

An isolated Debian recheck preserved the exact MCP allowlist, synthetic corpus,
provider counters, canonical-Markdown hashes, and zero-tolerance audit. It also
tested host-enforced read and command denials with byte-for-byte settings backup
and restoration. The denials blocked workspace MCP discovery or local MCP
startup; with the documented allowlist and explicit trusted disposable parent,
Antigravity returned no MCP calls. Those cells therefore failed closed and are
not efficiency evidence.

The prompt now binds section expansion explicitly to the ELM `read` tool and
forbids built-in file or shell tools. That removes an ambiguous cue but does not
certify direct MCP. The direct route remains non-claim-capable until a host
version provides enforceable tool-schema confinement or a fresh streamed run
demonstrates the exact MCP-only trace without broader host permissions. The
audit rule and allowlist were not relaxed.

### Host-brokered adapter boundary — 2026-08-28

The default Antigravity adapter now moves retrieval into the trusted harness and
runs the provider text-only. This is a valid RAG-style integration and can
support a bounded claim about quality and provider-token effects of ELM context
prompting versus full-corpus/no-memory prompting after a live three-arm matrix
passes. It is not evidence of autonomous MCP use and is not comparable to the
direct-MCP observations above. The report and aggregation keys preserve that
boundary explicitly.

The isolated Debian canary then passed all three `orion_storage` conditions with
Antigravity CLI 1.1.22 and `gemini-3.7-flash-high`. All answers and evidence
checks passed, the no-memory control returned `INSUFFICIENT_EVIDENCE`, canonical
Markdown was unchanged, provider usage was complete, and every provider trace
contained zero tool calls. The brokered ELM cell used a 521-token packet,
reported 23,458 provider tokens and 20.125 seconds combined latency; full corpus
reported 37,211 tokens and 55.844 seconds. The within-route ratio was `0.6304`.
This is a claim-capable integration canary, but one case is not a statistical
efficiency claim or a general crossover result.

## Next evidence step

The deterministic corpus-size curve is implemented in
[CORPUS_SIZE_CURVE.md](CORPUS_SIZE_CURVE.md). It generates nested synthetic
corpora at several token targets, counterbalances condition and size order,
repeats exact within-route pairs, and requires conservative statistical and
provenance gates before reporting a bounded crossover. The original no-memory
control remains in this pilot; the curve focuses on the two conditions whose
cost can cross as corpus size grows. The former Phase 5B path is separately
archived and does not block this evidence work.

This workspace separation is an evaluation-integrity control, not a security
boundary against a hostile local host. A client that can inspect its process
configuration or arbitrary readable OS paths may still discover the temporary
root. The pilot therefore tests cooperative coding-agent behavior under an
explicit tool-use contract; it does not claim adversarial data isolation.
