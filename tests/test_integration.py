from __future__ import annotations

from contextlib import closing
import sqlite3
import unittest

from _bootstrap import FixtureCopy, run_cli


class IndexIntegrationTests(unittest.TestCase):
    def test_sync_is_incremental_and_doctor_is_clean(self) -> None:
        with FixtureCopy() as root:
            first = run_cli(root, "sync")
            second = run_cli(root, "sync")
            doctor = run_cli(root, "doctor", "--no-sync")

        self.assertEqual(8, first["files_seen"])
        self.assertEqual(8, first["changed"])
        self.assertEqual([], first["errors"])
        self.assertEqual(0, second["changed"])
        self.assertEqual(0, doctor["issue_count"])

    def test_search_reads_exact_section_and_resolves_links(self) -> None:
        with FixtureCopy() as root:
            results = run_cli(root, "search", "Aurora gateway PostgreSQL")
            selected = results["results"][0]
            section = run_cli(root, "read", str(selected["section_id"]))
            outline = run_cli(root, "outline", selected["path"], "--no-sync")
            related = run_cli(root, "related", selected["path"], "--no-sync")

        self.assertEqual("20_projects/orion/PROJECT_HUB.md", selected["path"])
        self.assertIn("PostgreSQL 17", section["text"])
        self.assertGreaterEqual(len(outline["sections"]), 3)
        outgoing = {item["target_path"] for item in related["outgoing"]}
        self.assertIn("20_projects/orion/ACTIVE_CONTEXT.md", outgoing)
        self.assertIn("20_projects/orion/DECISIONS.md", outgoing)

    def test_archives_are_excluded_by_default_and_available_by_opt_in(self) -> None:
        with FixtureCopy() as root:
            ordinary = run_cli(root, "search", "Mercury Borealis")
            historical = run_cli(root, "search", "Mercury Borealis", "--include-archive")
            global_archive = run_cli(root, "search", "Zephyr Cobalt")

        self.assertEqual(0, ordinary["count"])
        self.assertEqual("20_projects/orion/backups/PROJECT_HUB.old.md", historical["results"][0]["path"])
        self.assertEqual(0, global_archive["count"])

    def test_rebuild_produces_a_healthy_disposable_database(self) -> None:
        with FixtureCopy() as root:
            rebuilt = run_cli(root, "rebuild")
            stats = run_cli(root, "stats", "--no-sync")
            database = root / ".elm" / "index.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]

        self.assertEqual([], rebuilt["errors"])
        self.assertEqual(8, stats["docs"])
        self.assertEqual(6, stats["active_docs"])
        self.assertEqual(2, stats["archive_docs"])
        self.assertEqual(1, stats["index_schema_version"])
        self.assertEqual("ok", quick_check)

    def test_changed_document_is_reindexed_without_touching_others(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "sync")
            before = run_cli(root, "search", "Atlas handbook")
            target = root / "20_projects" / "orion" / "ACTIVE_CONTEXT.md"
            target.write_text(target.read_text(encoding="utf-8") + "\nNew verified focus: Comet parser fixture.\n", encoding="utf-8")
            changed = run_cli(root, "sync")
            after = run_cli(root, "search", "Atlas handbook", "--no-sync")

        self.assertEqual(1, changed["changed"])
        self.assertEqual(before["results"][0]["document_id"], after["results"][0]["document_id"])
        self.assertEqual(before["results"][0]["section_key"], after["results"][0]["section_key"])

    def test_sync_and_rebuild_never_modify_canonical_markdown(self) -> None:
        with FixtureCopy() as root:
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            run_cli(root, "sync")
            run_cli(root, "rebuild")
            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
