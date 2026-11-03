"""Complete reads retain canonical source fidelity and indexed read policy."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from _bootstrap import FixtureCopy, run_cli, run_cli_process

try:
    from mcp import Client
except ModuleNotFoundError:
    Client = None

from elm_memory.mcp_server import create_server


DOCUMENT = "20_projects/orion/ACTIVE_CONTEXT.md"
DOCUMENT_UID = "doc_11111111-1111-4111-8111-111111111111"


class CompleteDocumentReadTests(unittest.TestCase):
    def test_full_text_and_provenance_match_canonical_source(self) -> None:
        with FixtureCopy() as root:
            source = root / DOCUMENT
            original = source.read_bytes()
            source.write_bytes(
                b"\xef\xbb\xbf" + original.replace(
                    b"Title: Orion Active Context\n",
                    f"Title: Orion Active Context\nELM ID: {DOCUMENT_UID}\n".encode("utf-8"),
                    1,
                )
            )
            run_cli(root, "rebuild")
            doc = run_cli(root, "read", DOCUMENT, "--document", "--project", "orion")
            by_id = run_cli(root, "read", str(doc["id"]), "--document", "--project", "orion")
            by_uid = run_cli(root, "read", DOCUMENT_UID, "--document", "--project", "orion")
            outline = run_cli(root, "outline", DOCUMENT, "--project", "orion", "--no-sync")
            section = run_cli(root, "read", outline["sections"][0]["section_key"], "--project", "orion")
            expected = source.read_bytes().decode("utf-8-sig", errors="replace")

        self.assertEqual(expected, doc["text"])
        self.assertEqual("document", doc["read_mode"])
        self.assertTrue(doc["source_current"])
        self.assertTrue(doc["text"].startswith("Title: Orion Active Context\n"))
        self.assertIn("# Current Focus\n", doc["text"])
        self.assertIn("# Next Check\n", doc["text"])
        self.assertEqual(doc["text"], by_id["text"])
        self.assertEqual(DOCUMENT_UID, doc["document_uid"])
        self.assertEqual(doc["text"], by_uid["text"])
        self.assertEqual(hashlib.sha256(b"\xef\xbb\xbf" + doc["text"].encode("utf-8")).hexdigest(), doc["content_hash"])
        self.assertEqual("orion", doc["project"])
        self.assertEqual("project", doc["namespace"])
        self.assertEqual("project_document", doc["authority"])
        self.assertEqual("untrusted_memory_data", doc["content_role"])
        self.assertNotIn("read_mode", section)
        self.assertNotIn("Title: Orion Active Context", section["text"])

    def test_scope_and_stale_source_refused(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            denied = run_cli_process(root, "read", DOCUMENT, "--document", "--project", "lighthouse")
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("not found under the current read policy", denied.stderr)
            with (root / DOCUMENT).open("a", encoding="utf-8") as stream:
                stream.write("\nUnindexed change.\n")
            stale = run_cli_process(root, "read", DOCUMENT, "--document", "--project", "orion")
            self.assertNotEqual(0, stale.returncode)
            self.assertIn("source changed", stale.stderr)

    def test_indexed_path_escape_refused(self) -> None:
        with FixtureCopy() as root, tempfile.TemporaryDirectory(prefix="elm-external-") as external:
            run_cli(root, "rebuild")
            outside = Path(external) / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            con = sqlite3.connect(root / ".elm" / "index.sqlite")
            try:
                con.execute("UPDATE documents SET path=?,content_hash=? WHERE path=?", (
                    outside.as_posix(), hashlib.sha256(outside.read_bytes()).hexdigest(), DOCUMENT,
                ))
                con.commit()
            finally:
                con.close()
            denied = run_cli_process(root, "read", str(outside), "--document", "--project", "orion")
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("outside the ELM root", denied.stderr)

    def test_symlink_escape_refused_when_supported(self) -> None:
        with FixtureCopy() as root, tempfile.TemporaryDirectory(prefix="elm-external-") as external:
            run_cli(root, "rebuild")
            outside = Path(external) / "outside.md"
            outside.write_bytes((root / DOCUMENT).read_bytes())
            source = root / DOCUMENT
            source.unlink()
            try:
                source.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            denied = run_cli_process(root, "read", DOCUMENT, "--document", "--project", "orion")
            self.assertNotEqual(0, denied.returncode)
            self.assertIn("outside the ELM root", denied.stderr)


@unittest.skipUnless(Client is not None, "install elm-memory[mcp] to run MCP tests")
class CompleteDocumentMCPTests(unittest.TestCase):
    def test_existing_read_tool_routes_document_flag(self) -> None:
        async def exercise(root: Path):
            async with Client(create_server(root), raise_exceptions=True) as client:
                tools = await client.list_tools()
                full = await client.call_tool("read", {
                    "section": DOCUMENT, "project": "orion", "document": True,
                })
                outline = run_cli(root, "outline", DOCUMENT, "--project", "orion", "--no-sync")
                section = await client.call_tool("read", {
                    "section": outline["sections"][0]["section_key"], "project": "orion",
                })
                return tools, full.structured_content, section.structured_content

        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            tools, full, section = asyncio.run(exercise(root))
            self.assertEqual((root / DOCUMENT).read_text(encoding="utf-8-sig"), full["text"])
        self.assertEqual(7, len(tools.tools))
        self.assertEqual("document", full["read_mode"])
        self.assertTrue(full["source_current"])
        self.assertNotIn("read_mode", section)
        self.assertIn("# Current Focus", full["text"])


if __name__ == "__main__":
    unittest.main()
