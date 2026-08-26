# Phase 0 Release Readiness

Status: Phase 0 private baseline published and verified; not ready for a public tag

Evaluated: 2026-08-26

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
- The approved bootstrap commit is published to the private `Miawd3/elm-memory`
  repository on `main`.
- Hosted CI run `32943414897` passes all nine jobs: the sanitized benchmark and
  Python 3.11-3.14 on both Ubuntu and Windows.

## Evidence limits

- Local validation covers Windows and Python 3.14.3; hosted CI additionally
  covers Ubuntu and Windows on Python 3.11-3.14.
- The benchmark is synthetic and lexical; it does not establish general semantic-memory quality.
- Search latency includes Python subprocess startup in the benchmark harness.
- No actual model token billing or end-to-end agent task outcome is measured.
- GitHub currently emits a non-failing Node.js 20 deprecation annotation for the
  pinned `actions/checkout` revision while executing it under Node.js 24.

## Blocking decisions before public publication

1. Ratify or change the repository and distribution name `elm-memory`.
2. Ratify or change the provisional minimum Python version, currently 3.11.
3. Refresh the pinned checkout action to a native Node.js 24 release and rerun
   hosted CI.
4. Enable GitHub private vulnerability reporting and update `SECURITY.md`.

## Decision resolved after the Phase 0 baseline

- Apache-2.0 is the accepted project license. The repository and built wheel
  include `LICENSE` and `NOTICE`, and package metadata uses the SPDX expression
  `Apache-2.0`.

## Phase 0 completion gate

Phase 0 now has a verified private baseline. It becomes public-release-ready only
after all blocking decisions above are resolved and the release documentation is
reviewed for an external audience.
