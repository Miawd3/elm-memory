# Evaluation and evidence limits

ELM's tests are designed to protect its architectural claims: Markdown remains canonical, derived state is rebuildable, retrieval stays scoped and bounded, and lower-authority memory cannot silently become stronger truth.

## Reproducible local gate

```bash
python -m compileall -q src tests benchmarks scripts packaging
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass
python benchmarks/run_holdout_confirmation.py --validate-only --assert-pass
```

Provider-backed pilots require an explicit local `--execute` flag. They are never part of unattended hosted automation.

## What is covered

- Windows and Linux source/package behavior.
- SQLite FTS5 rebuild, migration, quick-check, and disposable-index recovery.
- Stable document and section identity.
- Archive, project, namespace, and history filtering on every retrieval path.
- Deterministic context budgets and privacy-minimized traces.
- Concurrent readers, serialized writers, stale-lock handling, and transaction recovery.
- Proposal, claim, supersession, dispute, deletion, lease, source-CAS, and logical-lineage behavior.
- CLI/MCP identity and scope equivalence.
- Isolated wheel installation, update, rollback, and uninstall without canonical Markdown changes.
- Windows frozen runtime, silent installer, and uninstaller smoke tests.
- Linux per-user install, check, update, rollback, and uninstall on a disposable root.

## Current bounded evidence

The deterministic retrieval benchmark contains 50 sanitized lexical cases and passes all 50 with zero controlled archive leakage. It is a regression suite, not a general memory benchmark.

The frozen holdout and corpus-size protocols validate their manifests, counterbalancing, prompt budgets, and claim gates without making provider calls during ordinary tests.

A single isolated Antigravity/Gemini canary completed one three-arm task panel: ELM context, full corpus, and no memory. All three expected outcomes passed; the ELM/full-corpus provider-token ratio was `0.6304` for that canary. This is evidence that the harness and adapter can measure a bounded case. It is not statistical proof of general token savings, better model quality, or lower latency.

The default `direct-mcp` Antigravity route remained non-claim-capable under the tested CLI permission boundary. The separately attested host-broker route passed zero-provider-tool and provenance checks. ELM does not describe that result as autonomous provider MCP use.

## Claims ELM can support

- The local context packet never exceeds its requested deterministic estimate in covered tests.
- Canonical Markdown remains unchanged by indexing and read operations in covered tests.
- The SQLite projection can be deleted and rebuilt from canonical files.
- Controlled backups and `99_archive` documents do not appear in ordinary retrieval.
- The same core CLI contract is available to heterogeneous terminal-capable agents.
- The project has no mandatory hosted or model dependency.

## Claims ELM does not support yet

- A universal percentage reduction in billed tokens.
- Better task quality than every provider's native memory.
- Semantic recall comparable to embedding search.
- Multi-tenant authorization or hostile-process isolation.
- Production behavior on untested operating systems or architectures.
- Secure erasure from Git, backups, snapshots, or provider logs.

## Why local validation instead of hosted CI

The repository intentionally keeps the release gate in one local command path. This avoids account billing dependency and prevents authenticated provider pilots from running unattended. The tradeoff is that maintainers must publish the exact platform, Python version, artifact hashes, and commands used for each release instead of relying on a green hosted badge.

Release assets include `SHA256SUMS.txt`. Release notes should state which Windows and Linux environments actually passed artifact acceptance and should not generalize beyond them.
