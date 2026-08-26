from __future__ import annotations

import os
import sqlite3
import unittest

from _bootstrap import FixtureCopy, run_cli


class ReadOnlyStatusTests(unittest.TestCase):
    def test_missing_index_status_does_not_create_runtime_state(self) -> None:
        with FixtureCopy() as root:
            status = run_cli(root, "status")
            elm_runtime = root / ".elm"

            self.assertFalse(status["index_exists"])
            self.assertTrue(status["sync_required"])
            self.assertFalse(status["healthy"])
            self.assertFalse(elm_runtime.exists())

    def test_status_reports_fresh_then_stale_index_without_syncing(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            fresh = run_cli(root, "status")
            target = root / "20_projects" / "orion" / "PROJECT_HUB.md"
            target.write_text(
                target.read_text(encoding="utf-8") + "\n# Pending change\n\nNot indexed yet.\n",
                encoding="utf-8",
            )
            stale = run_cli(root, "status")
            after = run_cli(root, "search", "Pending change", "--no-sync")

        self.assertTrue(fresh["healthy"])
        self.assertEqual("ok", fresh["quick_check"])
        self.assertFalse(fresh["sync_required"])
        self.assertFalse(stale["healthy"])
        self.assertTrue(stale["sync_required"])
        self.assertIn("20_projects/orion/PROJECT_HUB.md", stale["changed_or_unindexed_files"])
        self.assertEqual(0, after["count"])

    def test_status_detects_same_size_content_change_with_preserved_mtime(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            target = root / "20_projects" / "orion" / "DECISIONS.md"
            before = target.stat()
            original = target.read_text(encoding="utf-8")
            self.assertIn("PostgreSQL 17", original)
            target.write_text(
                original.replace("PostgreSQL 17", "PostgreSQL 18"),
                encoding="utf-8",
            )
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

            status = run_cli(root, "status")

        self.assertFalse(status["healthy"])
        self.assertTrue(status["sync_required"])
        self.assertIn("20_projects/orion/DECISIONS.md", status["changed_or_unindexed_files"])

    def test_corrupt_index_status_fails_closed_without_rewriting_database(self) -> None:
        with FixtureCopy() as root:
            runtime = root / ".elm"
            runtime.mkdir()
            database = runtime / "index.sqlite"
            database.write_bytes(b"not-a-sqlite-database")
            before = database.read_bytes()

            status = run_cli(root, "status")

            after = database.read_bytes()

        self.assertFalse(status["healthy"])
        self.assertTrue(status["sync_required"])
        self.assertTrue(any(error.startswith("index_read_failed:") for error in status["errors"]))
        self.assertEqual(before, after)

    def test_incompatible_index_schema_is_reported_without_migration(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            database = root / ".elm" / "index.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE elm_meta SET value='999' WHERE key='index_schema_version'"
                )
                connection.commit()
            finally:
                connection.close()
            before = database.read_bytes()

            status = run_cli(root, "status")

            after = database.read_bytes()

        self.assertFalse(status["healthy"])
        self.assertFalse(status["schema_compatible"])
        self.assertEqual(999, status["index_schema_version"])
        self.assertIn("incompatible_schema", status["errors"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
