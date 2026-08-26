from __future__ import annotations

import asyncio
from pathlib import Path
import unittest

from _bootstrap import FixtureCopy, run_cli

try:
    from mcp import Client
except ModuleNotFoundError:  # The MCP adapter is an optional installation extra.
    Client = None

from elm_memory.mcp_server import create_server


EXPECTED_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}


async def list_tools(root: Path):
    async with Client(create_server(root), raise_exceptions=True) as client:
        return await client.list_tools()


async def call_tool(root: Path, name: str, arguments: dict):
    async with Client(create_server(root), raise_exceptions=True) as client:
        return await client.call_tool(name, arguments)


@unittest.skipUnless(Client is not None, "install elm-memory[mcp] to run MCP tests")
class ReadOnlyMCPTests(unittest.TestCase):
    def test_only_the_seven_read_tools_are_exposed_and_annotated(self) -> None:
        with FixtureCopy() as root:
            tools = asyncio.run(list_tools(root)).tools

        self.assertEqual(EXPECTED_TOOLS, {tool.name for tool in tools})
        for tool in tools:
            self.assertIsNotNone(tool.annotations)
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.open_world_hint)

    def test_search_context_read_related_stats_and_status_match_cli_identity(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            cli_search = run_cli(
                root,
                "search",
                "Aurora gateway PostgreSQL",
                "--project",
                "orion",
                "--no-sync",
            )
            mcp_search = asyncio.run(
                call_tool(
                    root,
                    "search",
                    {"query": "Aurora gateway PostgreSQL", "project": "orion"},
                )
            ).structured_content
            selected = mcp_search["results"][0]
            cli_context = run_cli(
                root,
                "context",
                "Aurora gateway PostgreSQL",
                "--budget",
                "900",
                "--project",
                "orion",
                "--no-sync",
                "--no-trace",
            )
            mcp_context = asyncio.run(
                call_tool(
                    root,
                    "context",
                    {
                        "task": "Aurora gateway PostgreSQL",
                        "budget": 900,
                        "project": "orion",
                    },
                )
            ).structured_content
            mcp_read = asyncio.run(
                call_tool(root, "read", {"section": selected["section_key"], "project": "orion"})
            ).structured_content
            mcp_related = asyncio.run(
                call_tool(root, "related", {"document": selected["path"], "project": "orion"})
            ).structured_content
            mcp_stats = asyncio.run(call_tool(root, "stats", {})).structured_content
            mcp_status = asyncio.run(call_tool(root, "status", {})).structured_content

        cli_identities = [
            (item["path"], item["section_key"], item["section_namespace"])
            for item in cli_search["results"]
        ]
        mcp_identities = [
            (item["path"], item["section_key"], item["section_namespace"])
            for item in mcp_search["results"]
        ]
        self.assertEqual(cli_identities, mcp_identities)
        self.assertEqual(cli_context["scope"], mcp_context["scope"])
        self.assertEqual(cli_context["selected_section_keys"], mcp_context["selected_section_keys"])
        self.assertEqual(selected["section_key"], mcp_read["section_key"])
        self.assertIn("PostgreSQL 17", mcp_read["text"])
        self.assertTrue(mcp_related["outgoing"])
        self.assertEqual(8, mcp_stats["docs"])
        self.assertTrue(mcp_status["healthy"])

    def test_mcp_reads_do_not_sync_or_write_traces(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            database = root / ".elm" / "index.sqlite"
            before_database = database.read_bytes()
            before_markdown = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            target = root / "20_projects" / "orion" / "PROJECT_HUB.md"
            target.write_text(
                target.read_text(encoding="utf-8") + "\n# Unsynced MCP probe\n\nInvisible.\n",
                encoding="utf-8",
            )
            expected_markdown = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            result = asyncio.run(
                call_tool(root, "context", {"task": "Unsynced MCP probe", "budget": 500})
            ).structured_content
            after_markdown = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.md")
            }
            after_database = database.read_bytes()
            trace_exists = (root / ".elm" / "traces").exists()

        self.assertEqual([], result["selected_section_keys"])
        self.assertEqual(before_database, after_database)
        self.assertNotEqual(before_markdown, expected_markdown)
        self.assertEqual(expected_markdown, after_markdown)
        self.assertFalse(trace_exists)

    def test_mcp_error_does_not_break_direct_cli(self) -> None:
        with FixtureCopy() as root:
            missing_status = asyncio.run(call_tool(root, "status", {})).structured_content
            failed = asyncio.run(
                call_tool(root, "search", {"query": "Aurora gateway PostgreSQL"})
            )
            rebuilt = run_cli(root, "rebuild")
            direct = run_cli(root, "search", "Aurora gateway PostgreSQL", "--no-sync")

        self.assertFalse(missing_status["index_exists"])
        self.assertTrue(failed.is_error)
        self.assertEqual([], rebuilt["errors"])
        self.assertGreater(direct["count"], 0)

    def test_mcp_exact_reads_cannot_bypass_project_or_archive_policy(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            active = run_cli(
                root,
                "search",
                "PostgreSQL durable telemetry records",
                "--project",
                "orion",
                "--no-sync",
            )["results"][0]
            archived = run_cli(
                root,
                "search",
                "Zephyr unsigned HTML packets",
                "--include-archive",
                "--no-sync",
            )["results"][0]

            wrong_project = asyncio.run(
                call_tool(
                    root,
                    "read",
                    {"section": active["section_key"], "project": "lighthouse"},
                )
            )
            hidden_archive = asyncio.run(
                call_tool(root, "read", {"section": archived["section_key"]})
            )

        self.assertTrue(wrong_project.is_error)
        self.assertTrue(hidden_archive.is_error)


if __name__ == "__main__":
    unittest.main()
