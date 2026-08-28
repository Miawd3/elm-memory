from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from _bootstrap import SOURCE_ROOT  # noqa: F401
from elm_memory.cli import connect, db_path
from elm_memory.schema import (
    INDEX_SCHEMA_VERSION,
    SchemaMigrationError,
    UnsupportedSchemaError,
    schema_version,
)


V0_SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    area TEXT,
    project TEXT,
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
CREATE TABLE sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    heading TEXT,
    heading_path TEXT,
    level INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    token_estimate INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE properties (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY(document_id, key)
);
"""


def create_v0_database(root: Path, *, duplicate_uid: bool = False) -> str:
    database = db_path(root)
    database.parent.mkdir(parents=True, exist_ok=True)
    document_uid = "doc_" + str(uuid.uuid4())
    with closing(sqlite3.connect(database)) as con:
        con.executescript(V0_SCHEMA)
        document_count = 2 if duplicate_uid else 1
        for document_id in range(1, document_count + 1):
            path = f"20_projects/orion/DOC_{document_id}.md"
            con.execute(
                """INSERT INTO documents(
                       id,path,area,project,title,scope,summary,status,last_updated,
                       mtime_ns,size_bytes,content_hash,is_archive,indexed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document_id,
                    path,
                    "20_projects",
                    "orion",
                    f"Document {document_id}",
                    "fixture",
                    "migration",
                    "active",
                    "2026-08-26",
                    1,
                    1,
                    str(document_id) * 64,
                    0,
                    "2026-08-26T00:00:00+00:00",
                ),
            )
            con.execute(
                """INSERT INTO sections(
                       id,document_id,parent_id,heading,heading_path,level,ordinal,
                       start_line,end_line,token_estimate,text
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (document_id, document_id, None, "Decision", "Decision", 1, 0, 1, 2, 1, "Body"),
            )
            con.execute(
                "INSERT INTO properties(document_id,key,value) VALUES(?,?,?)",
                (document_id, "elm_id", document_uid),
            )
        con.commit()
    return document_uid


class IndexMigrationTests(unittest.TestCase):
    def test_unversioned_v0_index_migrates_in_place(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-migrate-") as temporary:
            root = Path(temporary)
            expected_uid = create_v0_database(root)
            with closing(connect(root)) as con:
                document = con.execute(
                    "SELECT namespace,document_uid FROM documents"
                ).fetchone()
                section = con.execute(
                    "SELECT section_key,section_namespace FROM sections"
                ).fetchone()
                version = schema_version(con)

        self.assertEqual(INDEX_SCHEMA_VERSION, version)
        self.assertEqual("project", document["namespace"])
        self.assertEqual(expected_uid, document["document_uid"])
        self.assertTrue(section["section_key"].startswith("section_"))
        self.assertEqual("document_uid", section["section_namespace"])

    def test_duplicate_uid_failure_rolls_back_v0_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-migrate-rollback-") as temporary:
            root = Path(temporary)
            create_v0_database(root, duplicate_uid=True)
            with self.assertRaises(SchemaMigrationError):
                connect(root)
            with closing(sqlite3.connect(db_path(root))) as con:
                document_columns = {
                    row[1] for row in con.execute("PRAGMA table_info(documents)")
                }
                has_meta = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='elm_meta'"
                ).fetchone()

        self.assertNotIn("document_uid", document_columns)
        self.assertNotIn("namespace", document_columns)
        self.assertIsNone(has_meta)

    def test_newer_index_schema_is_refused_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-newer-schema-") as temporary:
            root = Path(temporary)
            database = db_path(root)
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as con:
                con.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY)")
                con.execute("CREATE TABLE elm_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                con.execute(
                    "INSERT INTO elm_meta(key,value) VALUES('index_schema_version','99')"
                )
                con.commit()
            with self.assertRaises(UnsupportedSchemaError):
                connect(root)
            with closing(sqlite3.connect(database)) as con:
                value = con.execute(
                    "SELECT value FROM elm_meta WHERE key='index_schema_version'"
                ).fetchone()[0]

        self.assertEqual("99", value)

    def test_v1_projection_migrates_to_governance_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-v1-migrate-") as temporary:
            root = Path(temporary)
            with closing(connect(root)):
                pass
            with closing(sqlite3.connect(db_path(root))) as con:
                for table in (
                    "governance_tombstones",
                    "governance_events",
                    "claims",
                    "governance_evidence",
                    "governance_proposals",
                ):
                    con.execute(f"DROP TABLE {table}")
                con.execute(
                    "UPDATE elm_meta SET value='1' WHERE key='index_schema_version'"
                )
                con.commit()
            with closing(connect(root)) as con:
                version = schema_version(con)
                tables = {
                    row[0] for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

        self.assertEqual(5, version)
        self.assertIn("claims", tables)
        self.assertIn("governance_events", tables)

    def test_v1_migration_failure_rolls_back_partial_ddl(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-v1-rollback-") as temporary:
            root = Path(temporary)
            with closing(connect(root)):
                pass
            with closing(sqlite3.connect(db_path(root))) as con:
                for table in (
                    "governance_tombstones",
                    "governance_events",
                    "claims",
                    "governance_evidence",
                    "governance_proposals",
                ):
                    con.execute(f"DROP TABLE {table}")
                con.execute("UPDATE elm_meta SET value='1' WHERE key='index_schema_version'")
                con.commit()

            def fail_after_ddl(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE partial_governance(id INTEGER)")
                raise RuntimeError("injected migration failure")

            with patch("elm_memory.schema._migrate_one_to_two", side_effect=fail_after_ddl):
                with self.assertRaises(SchemaMigrationError):
                    connect(root)
            with closing(sqlite3.connect(db_path(root))) as con:
                version = schema_version(con)
                partial = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_governance'"
                ).fetchone()

        self.assertEqual(1, version)
        self.assertIsNone(partial)

    def test_v2_proposal_projection_migrates_without_losing_v1_rows(self) -> None:
        proposal_id = "proposal_11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory(prefix="elm-v2-migrate-") as temporary:
            root = Path(temporary)
            with closing(connect(root)):
                pass
            with closing(sqlite3.connect(db_path(root))) as con:
                con.execute("DROP INDEX idx_governance_proposals_submission")
                con.execute("ALTER TABLE governance_proposals RENAME TO governance_proposals_v3")
                con.execute(
                    """CREATE TABLE governance_proposals (
                        proposal_id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,project TEXT NOT NULL,
                        subject TEXT NOT NULL,predicate TEXT NOT NULL,object TEXT NOT NULL,status TEXT NOT NULL,
                        proposed_at TEXT NOT NULL,valid_from TEXT NOT NULL,actor TEXT NOT NULL,
                        requested_authority TEXT NOT NULL,sensitivity TEXT NOT NULL,
                        evidence_ids_json TEXT NOT NULL,source_refs_json TEXT NOT NULL,content_hash TEXT NOT NULL
                    )"""
                )
                con.execute(
                    """INSERT INTO governance_proposals VALUES(
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )""",
                    (
                        proposal_id,
                        f"01_inbox/elm_proposals/orion/{proposal_id}.json",
                        "orion",
                        "Aurora",
                        "uses",
                        "LegacyDB",
                        "pending",
                        "2026-08-26T00:00:00+00:00",
                        "2026-08-26T00:00:00+00:00",
                        "agent:test",
                        "agent_proposal",
                        "normal",
                        "[]",
                        "[]",
                        "a" * 64,
                    ),
                )
                con.execute("DROP TABLE governance_proposals_v3")
                con.execute("UPDATE elm_meta SET value='2' WHERE key='index_schema_version'")
                con.commit()
            with closing(connect(root)) as con:
                version = schema_version(con)
                row = con.execute(
                    "SELECT format_version,submission_id,payload_digest,source_channel,valid_to,"
                    "supersedes_claim_id,expected_claim_sha256 "
                    "FROM governance_proposals WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchone()

        self.assertEqual(5, version)
        self.assertEqual(1, row[0])
        self.assertEqual((None, None, None, None, None, None), tuple(row[1:]))

    def test_v3_projection_adds_proposal_validity_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-v3-migrate-") as temporary:
            root = Path(temporary)
            with closing(connect(root)):
                pass
            with closing(sqlite3.connect(db_path(root))) as con:
                con.execute("ALTER TABLE governance_proposals DROP COLUMN expected_claim_sha256")
                con.execute("ALTER TABLE governance_proposals DROP COLUMN supersedes_claim_id")
                con.execute("ALTER TABLE governance_proposals DROP COLUMN valid_to")
                con.execute("UPDATE elm_meta SET value='3' WHERE key='index_schema_version'")
                con.commit()
            with closing(connect(root)) as con:
                version = schema_version(con)
                columns = {
                    row[1] for row in con.execute("PRAGMA table_info(governance_proposals)")
                }

        self.assertEqual(5, version)
        self.assertIn("valid_to", columns)
        self.assertIn("supersedes_claim_id", columns)
        self.assertIn("expected_claim_sha256", columns)

    def test_v4_projection_adds_cas_preconditions_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-v4-migrate-") as temporary:
            root = Path(temporary)
            with closing(connect(root)):
                pass
            with closing(sqlite3.connect(db_path(root))) as con:
                con.execute("ALTER TABLE governance_proposals DROP COLUMN expected_claim_sha256")
                con.execute("ALTER TABLE governance_proposals DROP COLUMN supersedes_claim_id")
                con.execute("UPDATE elm_meta SET value='4' WHERE key='index_schema_version'")
                con.commit()
            with closing(connect(root)) as con:
                version = schema_version(con)
                columns = {
                    row[1] for row in con.execute("PRAGMA table_info(governance_proposals)")
                }

        self.assertEqual(5, version)
        self.assertIn("supersedes_claim_id", columns)
        self.assertIn("expected_claim_sha256", columns)


if __name__ == "__main__":
    unittest.main()
