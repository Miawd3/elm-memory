# Contributing

ELM is currently building its Phase 1 identity, migration, read-policy, and concurrency foundations. Contributions should preserve existing CLI behavior unless a change includes a documented compatibility decision and migration path.

## Development setup

```bash
python -m venv .venv
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
```

## Contribution rules

- Add or update tests for observable behavior.
- Use synthetic fixtures only.
- Never commit private ELM content, chats, secrets, user-specific absolute paths, generated SQLite indexes, or terminal dumps.
- Keep SQLite-derived state disposable.
- Do not add mandatory embeddings, hosted services, model APIs, or database daemons without an accepted architecture change and benchmark evidence.
- Preserve exact source attribution and the distinction between evidence, proposals, and accepted state.
- Keep GitHub Actions permissions minimal and pin action references to immutable commit SHAs.

## Pull requests

Describe:

1. the behavior changed;
2. the invariant protected;
3. tests added;
4. compatibility or migration impact;
5. any privacy or retrieval-policy consequence.

The project is licensed under Apache-2.0. Unless explicitly stated otherwise,
contributions intentionally submitted for inclusion are provided under the same
license, as described by Section 5 of Apache-2.0.
