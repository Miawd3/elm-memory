# Phase 0 Release Readiness

Status: Phase 0 implementation complete locally; not ready for a public tag

Evaluated: 2026-08-25

Package version: `0.1.0.dev0`

## Completed locally

- Clean Git repository created separately from the private ELM and bootstrap archives.
- Public engine contains no machine-specific default memory path.
- Editable installation succeeds in an isolated Windows virtual environment.
- Console entry point `elm` works inside the virtual environment.
- A pure-Python wheel builds successfully.
- 22 unit, integration, CLI-contract, and CI-policy tests pass on Python 3.14.3 / Windows.
- The sanitized 20-case benchmark passes 20/20 with MRR 1.0.
- Fixture rebuild and `doctor` complete without errors.
- SQLite `PRAGMA quick_check` returns `ok` in the integration suite.
- The public Agent Skill passes `quick_validate.py`.
- GitHub Actions is configured for Python 3.11–3.14 on Windows and Linux.
- CI actions are pinned to full commit SHAs and the token is read-only.
- A clean export from the Git index passes all 26 tests, the 20-case benchmark, compilation, and skill validation.
- The privacy/secret-pattern scan and `git diff --cached --check` pass.

## Evidence limits

- Local validation covers Windows and Python 3.14.3 only.
- Linux and older Python versions are configured in CI but have not run on GitHub yet.
- The benchmark is synthetic and lexical; it does not establish general semantic-memory quality.
- Search latency includes Python subprocess startup in the benchmark harness.
- No actual model token billing or end-to-end agent task outcome is measured.

## Blocking decisions before public publication

1. Select a license and add `LICENSE`.
2. Ratify or change the repository and distribution name `elm-memory`.
3. Ratify or change the provisional minimum Python version, currently 3.11.
4. Create the GitHub repository and run the complete hosted CI matrix.
5. Enable GitHub private vulnerability reporting and update `SECURITY.md`.

## Phase 0 completion gate

Phase 0 is implementation-complete locally. It becomes public-release-ready only after all blocking decisions above are resolved, the first commit uses an approved public identity, and hosted CI passes.
