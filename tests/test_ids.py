from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _bootstrap import FixtureCopy, SOURCE_ROOT, run_cli
from elm_memory import cli


class DocumentIdAssignmentTests(unittest.TestCase):
    def test_dry_run_is_non_mutating_and_apply_is_idempotent(self) -> None:
        with FixtureCopy() as root:
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            dry_run = run_cli(root, "ids", "assign", "--dry-run")
            after_dry_run = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            applied = run_cli(root, "ids", "assign", "--apply")
            second = run_cli(root, "ids", "assign", "--dry-run")

            active_paths = [
                path for path in root.rglob("*.md")
                if not cli.is_archive_path(path.relative_to(root).as_posix())
            ]
            archived = root / "20_projects" / "orion" / "backups" / "PROJECT_HUB.old.md"
            manifest = Path(applied["backup"]) / "manifest.json"
            active_have_ids = all(
                "ELM ID: doc_" in path.read_text(encoding="utf-8") for path in active_paths
            )
            archived_text = archived.read_text(encoding="utf-8")
            manifest_status = json.loads(manifest.read_text(encoding="utf-8"))["status"]

        self.assertEqual(before, after_dry_run)
        self.assertGreater(dry_run["planned"], 0)
        self.assertEqual(dry_run["planned"], applied["changed"])
        self.assertEqual(0, second["planned"])
        self.assertTrue(active_have_ids)
        self.assertNotIn("ELM ID:", archived_text)
        self.assertEqual("applied", manifest_status)

    def test_applied_document_and_section_identity_survive_rebuild(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "ids", "assign", "--apply")
            first = run_cli(root, "search", "Aurora gateway PostgreSQL")["results"][0]
            run_cli(root, "rebuild")
            second = run_cli(root, "search", "Aurora gateway PostgreSQL")["results"][0]
            by_uid = run_cli(root, "outline", first["document_uid"], "--no-sync")
            by_key = run_cli(root, "read", first["section_key"])
            by_numeric = run_cli(root, "read", str(second["section_id"]))

        self.assertEqual(first["document_uid"], second["document_uid"])
        self.assertEqual(first["section_key"], second["section_key"])
        self.assertEqual(first["document_uid"], by_uid["document"]["document_uid"])
        self.assertEqual(first["section_key"], by_key["section_key"])
        self.assertEqual(second["section_id"], by_numeric["id"])

    def test_failed_apply_rolls_back_every_changed_document(self) -> None:
        with FixtureCopy() as root:
            args = SimpleNamespace(
                include_archive=False,
                path_prefix=None,
                lock_timeout=1.0,
                recover_stale_lock=False,
            )
            plan = cli._plan_document_uids(root, args)
            originals = {item["path"]: item["raw"] for item in plan}
            fail_target = (root / plan[1]["path"]).resolve()
            real_atomic_write = cli.atomic_write_bytes

            def flaky_write(path: Path, data: bytes) -> None:
                if Path(path).resolve() == fail_target and data == plan[1]["updated"]:
                    raise OSError("injected canonical write failure")
                real_atomic_write(Path(path), data)

            with patch("elm_memory.cli.atomic_write_bytes", side_effect=flaky_write):
                with self.assertRaises(RuntimeError):
                    cli._apply_document_uids(root, args, plan)

            restored = {
                relative: (root / relative).read_bytes()
                for relative in originals
            }
            manifests = list((root / "backups").glob("elm-ids-*/manifest.json"))
            manifest_status = json.loads(manifests[0].read_text(encoding="utf-8"))["status"]

        self.assertEqual(originals, restored)
        self.assertEqual(1, len(manifests))
        self.assertEqual("rolled_back", manifest_status)


if __name__ == "__main__":
    unittest.main()
