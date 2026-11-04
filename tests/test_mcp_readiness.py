"""The MCP readiness optimization must never weaken mutation profiles."""
from __future__ import annotations

import asyncio
import unittest

from _bootstrap import FixtureCopy, run_cli

try:
    from mcp import Client
except ModuleNotFoundError:
    Client = None

from elm_memory.mcp_server import (
    AutonomousMemoryPolicy, ProposalServerPolicy, create_server,
)


async def status_pair(server):
    async with Client(server, raise_exceptions=True) as client:
        routine = await client.call_tool("status", {})
        diagnostic = await client.call_tool("status", {"full_integrity": True})
        return routine.structured_content, diagnostic.structured_content


@unittest.skipUnless(Client is not None, "install elm-memory[mcp] to run MCP tests")
class MCPReadinessTests(unittest.TestCase):
    def test_read_only_routine_is_labelled_and_full_scan_remains_available(self):
        with FixtureCopy() as root:
            run_cli(root, "rebuild")
            routine, diagnostic = asyncio.run(status_pair(create_server(root)))
        self.assertTrue(routine["healthy"])
        self.assertEqual("readiness", routine["check_mode"])
        self.assertFalse(routine["integrity_checked"])
        self.assertIsNone(routine["quick_check"])
        self.assertTrue(diagnostic["healthy"])
        self.assertEqual("full", diagnostic["check_mode"])
        self.assertTrue(diagnostic["integrity_checked"])
        self.assertEqual("ok", diagnostic["quick_check"])

    def test_mutation_enabled_profiles_always_use_full_integrity(self):
        for mode in ("proposal-only", "autonomous"):
            with self.subTest(mode=mode), FixtureCopy() as root:
                run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
                run_cli(root, "rebuild")
                options = {"mutation_mode": mode}
                if mode == "proposal-only":
                    options["proposal_policy"] = ProposalServerPolicy(
                        allowed_projects=frozenset({"orion"}))
                else:
                    options["autonomous_policy"] = AutonomousMemoryPolicy(
                        allowed_projects=frozenset({"orion"}))
                routine, _ = asyncio.run(status_pair(create_server(root, **options)))
                self.assertTrue(routine["healthy"])
                self.assertEqual("full", routine["check_mode"])
                self.assertTrue(routine["integrity_checked"])
                self.assertEqual("ok", routine["quick_check"])


if __name__ == "__main__":
    unittest.main()
