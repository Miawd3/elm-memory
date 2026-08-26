"""Versioned SQLite projection schema and derived migrations."""
from __future__ import annotations

import sqlite3

from .identity import (
    derive_namespace,
    derive_section_key,
    normalize_heading_path,
    validate_document_uid,
)


INDEX_SCHEMA_VERSION = 1


class UnsupportedSchemaError(RuntimeError):
    pass


class SchemaMigrationError(RuntimeError):
    pass


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def schema_version(con: sqlite3.Connection) -> int | None:
    if not _table_exists(con, "documents"):
        return None
    if not _table_exists(con, "elm_meta"):
        return 0
    row = con.execute(
        "SELECT value FROM elm_meta WHERE key='index_schema_version'"
    ).fetchone()
    if row is None:
        raise SchemaMigrationError(
            "elm_meta exists without index_schema_version; rebuild the disposable index."
        )
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise SchemaMigrationError(
            "Invalid index_schema_version; rebuild the disposable index."
        ) from exc


def _create_latest(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE elm_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            area TEXT,
            project TEXT,
            namespace TEXT NOT NULL,
            document_uid TEXT,
            title TEXT,
            scope TEXT,
            summary TEXT,
            status TEXT,
            last_updated TEXT,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            is_archive INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_documents_uid
            ON documents(document_uid) WHERE document_uid IS NOT NULL;
        CREATE INDEX idx_documents_namespace ON documents(namespace, project, is_archive);

        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
            section_key TEXT NOT NULL UNIQUE,
            section_namespace TEXT NOT NULL,
            heading TEXT,
            heading_path TEXT,
            level INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            token_estimate INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX idx_sections_document ON sections(document_id, ordinal);

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE document_tags (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY(document_id, tag_id)
        );

        CREATE TABLE properties (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY(document_id, key)
        );

        CREATE TABLE links (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            target_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            target_path TEXT NOT NULL,
            anchor_text TEXT,
            relation_type TEXT NOT NULL DEFAULT 'markdown_link'
        );
        CREATE INDEX idx_links_source ON links(source_document_id);
        CREATE INDEX idx_links_target ON links(target_document_id);

        CREATE VIRTUAL TABLE sections_fts USING fts5(
            section_id UNINDEXED,
            document_id UNINDEXED,
            title,
            heading,
            body,
            tags,
            scope,
            summary,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    con.execute(
        "INSERT INTO elm_meta(key,value) VALUES('index_schema_version',?)",
        (str(INDEX_SCHEMA_VERSION),),
    )
    con.commit()


def _migrate_zero_to_one(con: sqlite3.Connection) -> None:
    document_columns = _columns(con, "documents")
    section_columns = _columns(con, "sections")
    if "namespace" not in document_columns:
        con.execute("ALTER TABLE documents ADD COLUMN namespace TEXT")
    if "document_uid" not in document_columns:
        con.execute("ALTER TABLE documents ADD COLUMN document_uid TEXT")
    if "section_key" not in section_columns:
        con.execute("ALTER TABLE sections ADD COLUMN section_key TEXT")
    if "section_namespace" not in section_columns:
        con.execute("ALTER TABLE sections ADD COLUMN section_namespace TEXT")

    properties = {
        int(row[0]): row[1]
        for row in con.execute("SELECT document_id,value FROM properties WHERE key='elm_id'")
    }
    documents = con.execute("SELECT id,path,area,project FROM documents ORDER BY id").fetchall()
    for document in documents:
        document_id = int(document[0])
        document_uid = validate_document_uid(properties.get(document_id))
        namespace = derive_namespace(document[2], document[3])
        con.execute(
            "UPDATE documents SET namespace=?,document_uid=? WHERE id=?",
            (namespace, document_uid, document_id),
        )
        occurrences: dict[str, int] = {}
        sections = con.execute(
            "SELECT id,heading_path FROM sections WHERE document_id=? ORDER BY ordinal",
            (document_id,),
        ).fetchall()
        for section in sections:
            heading_path = str(section[1])
            normalized_heading = normalize_heading_path(heading_path)
            occurrence = occurrences.get(normalized_heading, 0)
            occurrences[normalized_heading] = occurrence + 1
            section_key, section_namespace = derive_section_key(
                document_uid, str(document[1]), heading_path, occurrence
            )
            con.execute(
                "UPDATE sections SET section_key=?,section_namespace=? WHERE id=?",
                (section_key, section_namespace, int(section[0])),
            )

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_uid "
        "ON documents(document_uid) WHERE document_uid IS NOT NULL"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_namespace "
        "ON documents(namespace, project, is_archive)"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sections_key ON sections(section_key)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS elm_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    con.execute(
        "INSERT OR REPLACE INTO elm_meta(key,value) VALUES('index_schema_version','1')"
    )


def ensure_schema(con: sqlite3.Connection) -> None:
    version = schema_version(con)
    if version is None:
        _create_latest(con)
        return
    if version > INDEX_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"Index schema {version} is newer than supported schema {INDEX_SCHEMA_VERSION}. "
            "Upgrade ELM or use a separate index; no mutation was attempted."
        )
    if version == INDEX_SCHEMA_VERSION:
        return
    try:
        con.execute("BEGIN IMMEDIATE")
        while version < INDEX_SCHEMA_VERSION:
            if version == 0:
                _migrate_zero_to_one(con)
                version = 1
            else:
                raise SchemaMigrationError(f"No migration path from index schema {version}.")
        con.commit()
    except Exception as exc:
        con.rollback()
        if isinstance(exc, (SchemaMigrationError, UnsupportedSchemaError)):
            raise
        raise SchemaMigrationError(
            "Derived index migration failed; the canonical Markdown is unchanged. "
            "Delete the disposable index and run rebuild."
        ) from exc
