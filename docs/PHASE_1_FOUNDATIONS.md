# Phase 1 Foundations

Phase 1 makes ELM identities and local mutation mechanics safe enough for later
provenance work. It does not introduce claims, evidence snapshots, context
packing, embeddings, or MCP.

## Identity contract

Markdown may contain an optional top-level field:

```text
ELM ID: doc_<uuid4>
```

`sync`, `search`, and `rebuild` only parse this field; they never insert it.
`elm ids assign --dry-run` plans missing IDs and `--apply` performs the explicit
canonical migration.

Every indexed section receives a `section_key` derived with UUIDv5 from:

1. the document UID when present, otherwise its normalized relative path;
2. the normalized heading path;
3. the occurrence number of that heading path within the document.

A document-UID-bound key survives index rebuild and document movement. A
path-bound key survives rebuild but changes when the document moves. Heading
renames intentionally change the key.

Legacy integer document and section IDs remain accepted during the compatibility
period, but must not be stored as durable provenance references.

## Disposable-index migrations

`elm_meta.index_schema_version` versions the SQLite projection independently of
the Markdown format. Phase 1 uses index schema version `1` and migrates the
unversioned Phase 0 schema in one SQLite transaction.

- A newer unsupported schema is refused without mutation.
- A failed derived migration rolls back and recommends rebuilding the disposable
  index.
- Rebuild creates the latest schema directly.
- `PRAGMA busy_timeout` is configured on every connection.

## Explicit document-ID migration

`elm ids assign --apply` follows this sequence:

1. plan active Markdown files and reject malformed or duplicate existing IDs;
2. acquire `<root>/.elm/writer.lock`;
3. verify that planned source hashes have not changed;
4. copy every original into `<root>/backups/elm-ids-<timestamp>/`;
5. write each Markdown file through a flushed sibling temporary file and
   `os.replace`;
6. validate every inserted ID;
7. roll back already-written files if any item fails.

The backup manifest records `prepared`, `applied`, or `rolled_back`. Archive and
backup documents are excluded unless the operator explicitly requests
`--include-archive`.

## Concurrency contract

Index sync, rebuild, schema migration, and canonical ID assignment use one
cross-platform lock file containing the owner PID, host, start time, operation,
and random ownership token.

- Readers using the existing index do not hold the writer lock.
- A competing writer waits up to `--lock-timeout` and then exits without a
  traceback or partial canonical write.
- Stale recovery is never automatic. `--recover-stale-lock` is required after
  the previous local process is proven dead or the configured age threshold is
  exceeded.
- Recovery writes a metadata-only event under `.elm`; it does not copy memory
  contents.

The lock coordinates cooperating processes with access to the same root. It is
not an authentication or hostile-process security boundary.

## Read-policy contract

Search, outline, read, and related share archive, project, and namespace policy.
Direct numeric IDs, document UIDs, and section keys are resolved inside that
policy, so guessing an identifier cannot bypass it.

Namespaces are deterministic retrieval categories:

- `project` for `20_projects/<name>/...`;
- `shared` for `10_shared/...`;
- `workspace` for other paths.

They are governance filters, not access control between mutually untrusted users.

## Compatibility

The Phase 0 commands and JSON fields remain available. Phase 1 adds fields rather
than removing the numeric references:

```json
{
  "document_id": 12,
  "document_uid": "doc_...",
  "section_id": 47,
  "section_key": "section_...",
  "section_namespace": "document_uid"
}
```

Consumers should prefer `document_uid` and `section_key` for durable references
and treat integer IDs as short-lived database row identifiers.
