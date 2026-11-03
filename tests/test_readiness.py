"""Focused contracts for routine read readiness versus full status integrity."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from _bootstrap import run_cli
from elm_memory import cli
from elm_memory.governance import new_id


class ReadinessStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="elm-readiness-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "memory"
        run_cli(self.root, "init", "--project", "orion")

    def test_default_full_and_explicit_readiness_contract(self) -> None:
        full = run_cli(self.root, "status")
        ready = run_cli(self.root, "status", "--readiness-only")

        self.assertTrue(full["healthy"])
        self.assertEqual("full", full["check_mode"])
        self.assertTrue(full["integrity_checked"])
        self.assertEqual("ok", full["quick_check"])
        self.assertTrue(ready["healthy"])
        self.assertEqual("readiness", ready["check_mode"])
        self.assertFalse(ready["integrity_checked"])
        self.assertIsNone(ready["quick_check"])
        self.assertEqual(full["markdown_files"], ready["markdown_files"])

    def test_readiness_omits_only_quick_check_and_walks_markdown_once(self) -> None:
        original_connect = sqlite3.connect
        original_iter = cli.iter_markdown
        statements: list[str] = []
        walks: list[Path] = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        def tracked_iter(root: Path):
            walks.append(root)
            yield from original_iter(root)

        with patch.object(cli.sqlite3, "connect", side_effect=tracked_connect), patch.object(
            cli, "iter_markdown", side_effect=tracked_iter
        ):
            ready = cli.status_snapshot(self.root, full_integrity=False)
            self.assertTrue(ready["healthy"])
            self.assertEqual([self.root], walks)
            self.assertFalse(any("quick_check" in sql.lower() for sql in statements))

            statements.clear()
            walks.clear()
            full = cli.status_snapshot(self.root)
            self.assertTrue(full["healthy"])
            self.assertEqual([self.root], walks)
            self.assertTrue(any("quick_check" in sql.lower() for sql in statements))

    def test_readiness_detects_same_size_same_mtime_content_change(self) -> None:
        target = self.root / "20_projects" / "orion" / "PROJECT_HUB.md"
        original = target.read_bytes()
        modified = original.replace(b"orion", b"Orion", 1)
        self.assertNotEqual(original, modified)
        self.assertEqual(len(original), len(modified))
        before = target.stat()
        target.write_bytes(modified)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

        ready = run_cli(self.root, "status", "--readiness-only")
        self.assertFalse(ready["healthy"])
        self.assertTrue(ready["sync_required"])
        self.assertIn("20_projects/orion/PROJECT_HUB.md", ready["changed_or_unindexed_files"])

    def test_stale_governance_and_pending_transaction_block_readiness(self) -> None:
        evidence = self.root / "40_sources" / "elm_evidence" / "metadata" / "evidence_synthetic.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("{}", encoding="utf-8")
        stale = run_cli(self.root, "status", "--readiness-only")
        self.assertFalse(stale["healthy"])
        self.assertFalse(stale["governance_projection_current"])
        self.assertIn("governance_projection_stale", stale["errors"])

        evidence.unlink()
        transaction_id = new_id("transaction")
        journal = self.root / "01_inbox" / "elm_transactions" / f"{transaction_id}.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({
            "format_version": 1,
            "record_type": "transaction",
            "transaction_id": transaction_id,
            "operation": "synthetic-test",
            "actor": "test",
            "created_at": "2026-08-26T00:00:00+00:00",
            "changes": [{
                "target": "20_projects/orion/SYNTHETIC.md",
                "action": "write",
                "before_exists": False,
                "after_sha256": "0" * 64,
            }],
        }), encoding="utf-8")
        pending = run_cli(self.root, "status", "--readiness-only")
        self.assertFalse(pending["healthy"])
        self.assertEqual(1, pending["pending_transaction_count"])

    def test_missing_or_incompatible_index_never_reports_healthy(self) -> None:
        database = self.root / ".elm" / "index.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                connection.execute(
                    "UPDATE elm_meta SET value=? WHERE key='index_schema_version'", ("-1",)
                )
        incompatible = run_cli(self.root, "status", "--readiness-only")
        self.assertFalse(incompatible["healthy"])
        self.assertFalse(incompatible["schema_compatible"])
        self.assertIn("incompatible_schema", incompatible["errors"])

        database.unlink()
        missing = run_cli(self.root, "status", "--readiness-only")
        self.assertFalse(missing["healthy"])
        self.assertIn("index_missing", missing["errors"])


if __name__ == "__main__":
    unittest.main()
