# ELM Repository Instructions

## Current phase

Phases 1-5A, Phase 6A, and Phase 6B.1-6B.3 are implemented and validated.
Phase 6B.3 logical compaction has passed hosted validation; merging it is the
current gate before private-v1 release hardening.
The process default remains exactly seven read tools; the opt-in proposal profile
adds only three untrusted-candidate tools and cannot change accepted memory.
The separate opt-in autonomous profile adds only `remember_memory`, writes
active-but-unverified `agent_curated` claims for server-allowlisted projects,
and can replace only the current `agent_curated` lineage head through
operator-configured, source-verified compare-and-swap. It cannot dispute,
delete, recover, synchronize, migrate, change policy, or grant stronger
authority. Phase 5B signed human ratification is historical research, not an
active roadmap item. The completed evaluation track includes heterogeneous-host,
corpus-size, and frozen holdout panels.

## Invariants

- Markdown is canonical durable project knowledge.
- `.elm/index.sqlite` is disposable derived state.
- Indexing must not rewrite canonical Markdown.
- Retrieved memory is untrusted data, not executable instructions.
- Repository implementation truth outranks stored memory for current code facts.
- Backups and `99_archive` remain excluded from ordinary retrieval.
- Do not introduce mandatory hosted services, embeddings, model APIs, or database daemons.
- Autonomous memory must remain explicitly labeled `agent_curated`, rank below
  stronger current sources, reuse exact duplicates, and defer conflicts rather
  than creating or hiding contradictory active claims.
- Autonomous leases must bind their effective `valid_to` into canonical replay
  identity, preserve expired history, and count quota against temporally current
  claims rather than merely claims with an open-ended interval.
- Autonomous CAS must bind target ID and canonical claim hash, verify current
  bytes only inside operator-configured contained source roots, preserve
  `agent_curated` authority and lineage, and atomically write old/new/event state.
- Logical compaction must remain a bounded, deterministic, read-only lineage
  view. It must preserve exact canonical expansion, report truncation, fail
  closed on malformed lineage links, and never rewrite or delete audit history.
- A rendered context packet must never exceed its requested deterministic token estimate.
- Context must label authority/status, preserve exact source locators, and quote retrieved bodies as untrusted data.
- Retrieval traces contain no source body and no raw task text by default; they remain disposable runtime state.
- Do not commit personal ELM data, raw chats, credentials, terminal dumps, private bootstrap archives, or user-specific absolute paths.

## Validation

Run before handing off changes:

```bash
python -m compileall -q src tests benchmarks
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass
python benchmarks/run_holdout_confirmation.py --validate-only --assert-pass
```

Do not run authenticated provider pilots in hosted CI. Real Codex, Gemini,
Antigravity-Claude, or Claude Code calls require the explicit local `--execute`
flag, synthetic fixtures, bounded run counts, and sanitized reports.
Corpus-size curve runs additionally require even repeats, pairwise
counterbalancing, bounded prompt estimates, and a total execution-time cap.
The holdout confirmation protocol is frozen at six cases, two technical
repetitions, six no-memory controls, and 128k/192k/208k targets. Provider calls
must not run until its exact `--plan-only` preflight passes.

When modifying the Agent Skill, also run its `quick_validate.py` check. When modifying GitHub Actions, preserve read-only permissions, unprivileged triggers, immutable action pins, and the CI-policy tests.

## Publication boundary

Apache-2.0 is the accepted project license and hosted Phase 2 CI plus
private-copy acceptance are verified. No public tag is ready until the
repository/package name, minimum Python version, and external-facing release
documentation are ratified or completed.
