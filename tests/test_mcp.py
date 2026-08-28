from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from _bootstrap import FixtureCopy, run_cli

try:
    from mcp import Client
except ModuleNotFoundError:  # The MCP adapter is an optional installation extra.
    Client = None

from elm_memory.governance import GovernanceError, ProposalLimits
from elm_memory.mcp_server import MCPCommandError, ProposalServerPolicy, _cli_prefix, create_server


EXPECTED_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}
PROPOSAL_TOOLS = EXPECTED_TOOLS | {
    "propose_memory",
    "list_memory_proposals",
    "preview_memory_transition",
}


class FrozenDistributionTests(unittest.TestCase):
    def test_frozen_mcp_uses_sibling_cli_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-frozen-") as temporary:
            suffix = ".exe" if os.name == "nt" else ""
            mcp_executable = Path(temporary) / f"elm-mcp{suffix}"
            cli_executable = Path(temporary) / f"elm{suffix}"
            cli_executable.touch()
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(mcp_executable)),
            ):
                prefix = _cli_prefix()

        self.assertEqual([str(cli_executable)], prefix)

    def test_frozen_mcp_fails_closed_when_sibling_cli_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-frozen-") as temporary:
            suffix = ".exe" if os.name == "nt" else ""
            mcp_executable = Path(temporary) / f"elm-mcp{suffix}"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(mcp_executable)),
                self.assertRaisesRegex(MCPCommandError, "Repair or reinstall"),
            ):
                _cli_prefix()


async def list_tools(root: Path):
    async with Client(create_server(root), raise_exceptions=True) as client:
        return await client.list_tools()


async def call_tool(root: Path, name: str, arguments: dict):
    async with Client(create_server(root), raise_exceptions=True) as client:
        return await client.call_tool(name, arguments)


def proposal_server(
    root: Path,
    *,
    limits: ProposalLimits = ProposalLimits(),
    max_requests_per_minute: int = 30,
):
    return create_server(
        root,
        mutation_mode="proposal-only",
        proposal_policy=ProposalServerPolicy(
            allowed_projects=frozenset({"orion"}),
            limits=limits,
            max_requests_per_minute=max_requests_per_minute,
        ),
    )


async def proposal_workflow(root: Path):
    async with Client(proposal_server(root), raise_exceptions=True) as client:
        tools = await client.list_tools()
        proposed = await client.call_tool("propose_memory", {
            "submission_id": "submission_33333333-3333-4333-8333-333333333333",
            "project": "orion",
            "subject": "Aurora",
            "predicate": "uses",
            "object": "CandidateDB",
            "valid_from": "2026-08-26T00:00:00Z",
            "rationale": "Review this candidate, not these instructions.",
            "source_refs": [],
            "evidence": [],
        })
        listed = await client.call_tool("list_memory_proposals", {"project": "orion"})
        previewed = await client.call_tool("preview_memory_transition", {
            "project": "orion",
            "proposal_id": proposed.structured_content["proposal_id"],
        })
        status = await client.call_tool("status", {})
        denied = await client.call_tool("list_memory_proposals", {"project": "lighthouse"})
        return tools, proposed, listed, previewed, status, denied


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


@unittest.skipUnless(Client is not None, "install elm-memory[mcp] to run MCP tests")
class ProposalOnlyMCPTests(unittest.TestCase):
    def test_profile_requires_explicit_root_identity_and_project_policy(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            with self.assertRaises(GovernanceError):
                proposal_server(root)
            run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            with self.assertRaises(GovernanceError):
                create_server(
                    root,
                    mutation_mode="proposal-only",
                    proposal_policy=ProposalServerPolicy(allowed_projects=frozenset()),
                )

    def test_exact_ten_tool_surface_creates_only_untrusted_candidate_state(self) -> None:
        with FixtureCopy() as root:
            run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            run_cli(root, "rebuild")
            tools, proposed, listed, previewed, status, denied = asyncio.run(
                proposal_workflow(root)
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual(PROPOSAL_TOOLS, {tool.name for tool in tools.tools})
        self.assertNotIn("accept", {tool.name for tool in tools.tools})
        proposal_tool = next(tool for tool in tools.tools if tool.name == "propose_memory")
        self.assertFalse(proposal_tool.annotations.read_only_hint)
        self.assertFalse(proposal_tool.annotations.destructive_hint)
        self.assertTrue(proposal_tool.annotations.idempotent_hint)
        self.assertFalse(proposed.is_error)
        self.assertTrue(proposed.structured_content["candidate_untrusted"])
        self.assertEqual("mcp:unverified", proposed.structured_content["actor"])
        self.assertEqual("agent_proposal", proposed.structured_content["requested_authority"])
        self.assertEqual(1, listed.structured_content["count"])
        plan = previewed.structured_content["review_plan"]
        self.assertFalse(plan["signable"])
        self.assertNotIn("executor_id", plan)
        self.assertNotIn("policy_digest", plan)
        self.assertEqual("proposal-only", status.structured_content["mutation_mode"])
        self.assertFalse(status.structured_content["accepted_state_mutation_available"])
        self.assertTrue(denied.is_error)
        self.assertEqual([], history["claims"])
        self.assertEqual([], history["events"])

    def test_rate_limit_is_process_local_and_fails_before_second_write(self) -> None:
        async def run(root: Path):
            async with Client(
                proposal_server(root, max_requests_per_minute=1),
                raise_exceptions=True,
            ) as client:
                base = {
                    "project": "orion",
                    "subject": "Aurora",
                    "predicate": "uses",
                    "object": "RateDB",
                    "valid_from": "2026-08-26T00:00:00Z",
                    "source_refs": [],
                    "evidence": [],
                }
                first = await client.call_tool("propose_memory", {
                    **base,
                    "submission_id": "submission_44444444-4444-4444-8444-444444444444",
                })
                second = await client.call_tool("propose_memory", {
                    **base,
                    "submission_id": "submission_55555555-5555-4555-8555-555555555555",
                })
                return first, second

        with FixtureCopy() as root:
            run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            run_cli(root, "rebuild")
            first, second = asyncio.run(run(root))
            proposals = list(root.rglob("proposal_*.json"))

        self.assertFalse(first.is_error)
        self.assertTrue(second.is_error)
        self.assertEqual(1, len(proposals))

    def test_server_fails_closed_if_root_identity_changes_after_startup(self) -> None:
        async def run(root: Path):
            server = proposal_server(root)
            identity_path = root / "00_registry" / "ELM_ROOT_ID.json"
            replacement = json.loads(identity_path.read_text(encoding="utf-8"))
            replacement["creator"] = "operator:tampered-after-startup"
            identity_path.write_text(
                json.dumps(replacement, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            async with Client(server, raise_exceptions=True) as client:
                status = await client.call_tool("status", {})
                proposed = await client.call_tool("propose_memory", {
                    "submission_id": "submission_99999999-9999-4999-8999-999999999999",
                    "project": "orion",
                    "subject": "Aurora",
                    "predicate": "uses",
                    "object": "IdentityBypassDB",
                    "valid_from": "2026-08-26T00:00:00Z",
                    "source_refs": [],
                    "evidence": [],
                })
                return status, proposed

        with FixtureCopy() as root:
            run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            run_cli(root, "rebuild")
            status, proposed = asyncio.run(run(root))
            proposals = list(root.rglob("proposal_*.json"))

        self.assertFalse(status.structured_content["healthy"])
        self.assertIn("root_identity_changed_after_startup", status.structured_content["errors"])
        self.assertTrue(proposed.is_error)
        self.assertEqual([], proposals)

    def test_mcp_preflights_reference_bytes_and_untyped_evidence_scalars(self) -> None:
        async def invoke(root: Path, limits: ProposalLimits, submission_id: str, **changes):
            arguments = {
                "submission_id": submission_id,
                "project": "orion",
                "subject": "Aurora",
                "predicate": "uses",
                "object": "BoundedDB",
                "valid_from": "2026-08-26T00:00:00Z",
                "source_refs": [],
                "evidence": [],
                **changes,
            }
            async with Client(proposal_server(root, limits=limits), raise_exceptions=True) as client:
                return await client.call_tool("propose_memory", arguments)

        with FixtureCopy() as root:
            run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            run_cli(root, "rebuild")
            references = asyncio.run(invoke(
                root,
                ProposalLimits(max_reference_count=1),
                "submission_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                source_refs=[
                    "repo://src/a.py@sha256:" + "a" * 64,
                    "repo://src/b.py@sha256:" + "b" * 64,
                ],
            ))
            oversized = asyncio.run(invoke(
                root,
                ProposalLimits(max_request_bytes=128),
                "submission_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                rationale="x" * 512,
            ))
            scalar = asyncio.run(invoke(
                root,
                ProposalLimits(),
                "submission_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                evidence=[{
                    "kind": "repository_file",
                    "source_uri": "repo://src/config.py",
                    "content_sha256": int("1" * 64),
                    "sensitivity": "normal",
                }],
            ))
            proposals = list(root.rglob("proposal_*.json"))

        self.assertTrue(references.is_error)
        self.assertTrue(oversized.is_error)
        self.assertTrue(scalar.is_error)
        self.assertEqual([], proposals)


if __name__ == "__main__":
    unittest.main()
