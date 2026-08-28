# Contributing

ELM accepts focused changes that preserve its core invariants: Markdown is canonical, SQLite is disposable, retrieved content is untrusted data, and no hosted or model service is mandatory.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[mcp]"
```

## Required local checks

```bash
python -m compileall -q src tests benchmarks scripts packaging
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass
python benchmarks/run_holdout_confirmation.py --validate-only --assert-pass
```

This repository intentionally has no hosted CI workflow. Include the operating system, Python version, commands, and results in the pull request.

## Contribution rules

- Add or update tests for observable behavior.
- Use synthetic fixtures only.
- Never commit private memory, chats, credentials, user-specific paths, generated indexes, provider responses, or terminal dumps.
- Keep every derived database rebuildable from canonical files.
- Preserve exact source attribution and the distinction between evidence, proposals, accepted claims, and `agent_curated` memory.
- Do not add mandatory embeddings, hosted services, model APIs, or database daemons without a measured failure case and an accepted architecture change.
- Provider-backed pilots require explicit local execution, bounded calls, sanitized reports, and zero credentials in the repository.
- Keep documentation concise and describe evidence limits honestly.

## Pull requests

Explain:

1. what observable behavior changed;
2. which invariant the change protects;
3. what tests were added or run;
4. compatibility or migration impact;
5. privacy, security, or retrieval-policy consequences.

Contributions are accepted under Apache-2.0, as described by Section 5 of the license.
