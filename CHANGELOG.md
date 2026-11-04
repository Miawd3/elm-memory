# Changelog

All notable changes to ELM are documented here.

## 1.0.1.dev0 — local development

- Preserve governed propositions, claim IDs and original authority in context packets; ordinary document status does not imply ratification.
- Keep distinct stable sections with repeated headings and select fitting evidence before manifests, with explicit coverage and omitted-source expansion metadata.
- Add complete indexed document reads with source-hash and root-boundary checks.
- Separate routine read-only MCP readiness checks from explicit full SQLite integrity diagnostics; mutation profiles retain full checks.
- Use canonical document summaries as an explicitly labeled fallback when whole sections do not fit.
- Make the explicit-validity regression test independent of an expired August fixture date.

This development build is not a published release. Validation applies to the local Windows environment; it does not establish new cross-platform or provider-quality claims.

## 1.0.0 — 2026-08-28

First public-ready release.

### Core

- Local Markdown canonical store with a disposable SQLite FTS5 projection.
- Deterministic search and token-bounded context packets with stable source locators.
- Root initialization, health diagnostics, archive exclusion, stable identity, migrations, locking, and recovery.
- Governed proposal/claim lifecycle with reference-only evidence metadata and preserved history.
- Read-only, proposal-only, and bounded autonomous MCP profiles.
- Leased `agent_curated` memory, source-verified compare-and-swap, and bounded logical lineage views.

### Distribution

- Self-contained per-user Windows x64 installer and portable ZIP.
- Architecture-independent Linux bundle with dependency checks, update, rollback, and uninstall.
- Python wheel and source distribution.
- Portable `elm-memory-operator` skill ZIP for Codex.
- Local release builder, artifact smoke tests, and SHA-256 manifest.

### Known limits

- Windows artifacts are not code-signed.
- macOS has no tested installer.
- Retrieval is lexical; embeddings and semantic reranking are not included.
- ELM is a local single-user tool, not a multi-tenant authorization service.
