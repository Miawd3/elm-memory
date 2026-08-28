# ELM Release Readiness — Phase 6B.3 / Private-v1 Candidate

Status: Phase 6B.3 validated; private-v1 candidate release hardening; not ready for a public tag

Evaluated: 2026-08-28

Package name: `elm-memory`
Package version: `0.10.0.dev0`
Requires Python: `>=3.11`
License: `Apache-2.0`

## Verified Repository Truth

The active development line reflects completed, validated capabilities through Phase 6B.3:

- **Windows baseline (Python 3.14.3)**:
  - Source bytecode compilation passed cleanly:
    `python -m compileall -q src tests benchmarks scripts`
  - Full test suite passed (251/251 tests across 20 test modules):
    `python -m unittest discover -s tests -v`
  - Sanitized deterministic retrieval benchmark passed (50/50 cases with zero leakage):
    `python benchmarks/run_benchmark.py --assert-pass`
  - All three offline evaluation preflights passed:
    `python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass`
    `python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass`
    `python benchmarks/run_holdout_confirmation.py --validate-only --assert-pass`
  - The release-hardening diff is limited to synchronized documentation and the
    stdlib-only acceptance harness with its tests.
- **Hosted CI status**:
  - Previous branch and pull request validation covered Python 3.11–3.14 on both Windows and Linux, passing all jobs.
  - The latest hosted `main` run did not start because of GitHub account spending limits; this is an external rerun gate, not a code defect or regression.
- **Debian snapshot (Debian 13, Python 3.13.5, ARM64)**:
  - Source compilation and the full 249-test suite passed; 19 optional or
    platform-specific tests were skipped.
  - Validation runs only inside `/root/agy-workspaces/elm-memory-v1-20260828`.
    The production application and data paths are outside that workspace and
    were not accessed or modified.
- **Cross-platform candidate artifact acceptance**:
  - The final pure-Python candidate wheel has SHA-256
    `0f581b8c5024f0c960fd811402a1230e9d5180f99ee0c7ae2a0d2009903d816a`.
  - Windows and Debian both passed the isolated previous `0.9.0.dev0` →
    candidate `0.10.0.dev0` → previous `0.9.0.dev0` rollback sequence.
  - Each run preserved all eight canonical Markdown fixture documents, rebuilt
    the disposable index, indexed 16 sections, and verified installed CLI/MCP
    entry points without network package resolution.

## Implemented Phase Capabilities (Phases 1–6B.3)

1. **Phase 1 (Foundations)**: Rebuild-stable UUIDs and derived section keys, versioned disposable index schema with migrations, atomic single-writer locking, cross-platform read concurrency, and consistent archive/namespace read filtering.
2. **Phase 2 (Bounded Context)**: Deterministic token-bounded context compilation (`elm context`), privacy-minimized disposable traces, trace retention cleanup, and 50-case benchmark evaluation.
3. **Phase 3 (Governed Memory)**: Immutable proposal records, reference-only evidence metadata, canonical claim Markdown, explicit lifecycle transitions (accept, reject, defer, dispute, supersede, delete), valid/recorded-time history, tombstones, and recoverable journaled transactions.
4. **Phase 4 (Read-Only MCP)**: Exactly seven read tools (`status`, `search`, `context`, `read`, `related`, `history`, `stats`) delegating strictly to CLI JSON contracts without independent state or unverified escalation.
5. **Phase 5A (Opt-In Proposal-Only MCP)**: Exactly ten tools (default seven plus `propose_memory`, `list_memory_proposals`, `preview_memory_transition`) with server-side project allowlists, rate limiting, and durable quotas; cannot alter accepted memory. Phase 5B signed human ratification is archived historical research.
6. **Phase 6A (Opt-In Autonomous Memory)**: Exactly eight tools (default seven plus `remember_memory`) writing active-but-unverified `agent_curated` memory for allowlisted projects; duplicates are reused, conflicts and quota overflow are deferred.
7. **Phase 6B.1 (Validity Leases)**: Deterministic default and maximum TTL leases bound into proposal-v3 replay digests; expired claims disappear from ordinary retrieval and stop consuming active quota without destructive background writers.
8. **Phase 6B.2 (Source-Verified CAS)**: Operator-configured contained source roots (`--source-root ALIAS=/path`); source-verified compare-and-swap replacing or renewing only the sole current `agent_curated` lineage head with proposal-v4 and index schema v5.
9. **Phase 6B.3 (Logical Compaction)**: Bounded, deterministic, read-only lineage manifest (`--compact`) under requested token budget and exact canonical expansion (`--lineage`) on the existing `history` tool without writing compaction records or rewriting history.

## Private-v1 Blockers vs. Eventual Public-Release Blockers

### Private-v1 Blockers (Active Release-Hardening Gate)

1. **Antigravity Evaluation Confinement**: Antigravity CLI 1.1.22 exposes no
   verified tool-schema pruning equivalent to the Codex/Claude routes. A
   2026-08-28 isolated recheck found that broad read or command denials also
   block workspace MCP discovery or local MCP startup; the documented exact
   allowlist produced no MCP trace in that host snapshot. Prompt tool binding is
   hardened, but it is defense in depth only. Keep the route non-claim-capable
   until a future host mechanism or streamed rerun proves an exact MCP-only
   trace with `non_mcp_tool_call_count == 0`; do not expand the allowlist or
   weaken the gate.
2. **External Hosted CI Rerun**: Re-trigger hosted CI once GitHub spending limits are refreshed.

### Public-Release Blockers (Deferred to Eventual Public Tag)

1. **Distribution / Package Name Ratification**: Final confirmation of the PyPI name `elm-memory` after public namespace and collision review.
2. **Minimum Supported Python Version**: Formal ratification of Python `>=3.11` vs. `>=3.12` for long-term public support.
3. **GitHub Private Vulnerability Reporting**: Enabling GitHub security advisories and private reporting links in `SECURITY.md`.
4. **Action Pin Modernization**: Updating `actions/checkout` to clear the Node.js 20 deprecation annotation under Node.js 24.
5. **External-Facing Release Documentation**: Public quickstart guides, release announcements, and external contributor governance.

## Evidence Limits

- Local validation covers Windows (Python 3.14.3) and isolated Debian snapshot; hosted CI additionally covers Ubuntu and Windows on Python 3.11–3.14.
- Deterministic benchmarks are synthetic and lexical; they measure retrieval accuracy and budget conformance, not general semantic-memory quality or billed-token savings.
- Search latency includes Python subprocess startup in the benchmark harness.
- GitHub currently emits a non-failing Node.js 20 deprecation annotation for the pinned `actions/checkout` revision while executing it under Node.js 24.

## Historical Phase 0 Baseline (Archived Record)

- Package version: `0.1.0.dev0` (evaluated 2026-08-26).
- 22 tests passing on Windows Python 3.14.3; 20-case retrieval benchmark.
- Apache-2.0 accepted as project license (`LICENSE` and `NOTICE` included, SPDX `Apache-2.0`).
- Clean repository separation from private bootstrap archives.
- Hosted CI run `32943414897` passed all nine initial jobs.
