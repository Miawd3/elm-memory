# Privacy Model — Phase 1

ELM indexes local Markdown into `<root>/.elm/index.sqlite`. The index contains section text and metadata copied from the Markdown root, so filesystem access to the index should be treated as equivalent to access to the indexed memory.

## Defaults

- No network calls.
- No telemetry.
- No embeddings.
- No model API.
- No raw-chat ingestion.
- Backups and `99_archive` are excluded from normal search.
- Archive, project, and namespace filters are enforced across search, outline,
  read, and related operations, including direct numeric-ID and stable-key reads.
- The derived index remains on the local filesystem.

## Not promised

- Archive filtering is not authentication.
- Phase 1 does not isolate multiple OS users or mutually untrusted agents.
- Namespace filters are governance controls, not authenticated authorization.
- Deleting active Markdown does not erase copies from Git, backups, or filesystem snapshots.
- The project does not claim secure physical erasure.

## Public fixtures

Only synthetic project names and facts may be committed. Private ELM snapshots and bootstrap archives must remain outside the repository.
