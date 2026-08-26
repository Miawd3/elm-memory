from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from _bootstrap import FixtureCopy, SOURCE_ROOT, run_cli, run_cli_process
from elm_memory.atomic import atomic_create_bytes, atomic_write_bytes
from elm_memory.locking import WriterLock, WriterLockError


class ConcurrencyTests(unittest.TestCase):
    def test_simultaneous_no_sync_reads_succeed(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "sync")
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(run_cli, root, "search", "Aurora gateway", "--no-sync")
                    for _ in range(2)
                ]
                results = [future.result() for future in futures]

        self.assertTrue(all(result["count"] > 0 for result in results))

    def test_competing_writer_fails_cleanly_at_zero_timeout(self) -> None:
        with FixtureCopy() as root:
            with WriterLock(root, "test-owner"):
                completed = run_cli_process(root, "sync", "--lock-timeout", "0")

        self.assertEqual(2, completed.returncode)
        self.assertIn('"error": "writer_lock_unavailable"', completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_killed_writer_requires_explicit_stale_recovery(self) -> None:
        with FixtureCopy() as root:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            script = (
                "import os,sys; from pathlib import Path; "
                "from elm_memory.locking import WriterLock; "
                "lock=WriterLock(Path(sys.argv[1]),'crash-fixture'); "
                "lock.acquire(); os._exit(0)"
            )
            subprocess.run(
                [sys.executable, "-c", script, str(root)],
                env=environment,
                check=True,
            )
            with self.assertRaises(WriterLockError):
                WriterLock(root, "blocked", timeout=0).acquire()
            with WriterLock(root, "recovery", timeout=0, recover_stale=True):
                self.assertTrue((root / ".elm" / "writer.lock").exists())
            recovery_log = root / ".elm" / "writer-lock-recovery.jsonl"
            recovery_log_exists = recovery_log.exists()
            recovery_log_text = recovery_log.read_text(encoding="utf-8")

        self.assertTrue(recovery_log_exists)
        self.assertIn("crash-fixture", recovery_log_text)

    def test_failed_atomic_replace_preserves_canonical_file(self) -> None:
        with FixtureCopy() as root:
            target = root / "atomic.md"
            target.write_bytes(b"original\n")
            with patch("elm_memory.atomic.os.replace", side_effect=OSError("injected replace failure")):
                with self.assertRaises(OSError):
                    atomic_write_bytes(target, b"replacement\n")
            temporary_files = list(root.glob(".atomic.md.*.tmp"))
            content = target.read_bytes()

        self.assertEqual(b"original\n", content)
        self.assertEqual([], temporary_files)

    def test_atomic_create_never_replaces_an_existing_immutable_record(self) -> None:
        with FixtureCopy() as root:
            target = root / "immutable.json"
            target.write_bytes(b"original\n")
            with self.assertRaises(FileExistsError):
                atomic_create_bytes(target, b"replacement\n")
            content = target.read_bytes()
            temporary_files = list(root.glob(".immutable.json.*.tmp"))

        self.assertEqual(b"original\n", content)
        self.assertEqual([], temporary_files)


if __name__ == "__main__":
    unittest.main()
