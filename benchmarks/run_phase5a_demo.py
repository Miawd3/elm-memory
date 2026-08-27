#!/usr/bin/env python3
"""Run the synthetic Phase 5A proposal-only safety and idempotency demo."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"
READ_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}
PROPOSAL_TOOLS = READ_TOOLS | {
    "propose_memory",
    "list_memory_proposals",
    "preview_memory_transition",
}

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from mcp import Client

from elm_memory.mcp_server import ProposalServerPolicy, create_server


def run_cli(root: Path, *arguments: str) -> dict:
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(SOURCE_ROOT) if not current else os.pathsep.join((str(SOURCE_ROOT), current))
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "elm_memory.cli",
            *arguments,
            "--root",
            str(root),
            "--json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


async def exercise(root: Path) -> dict:
    policy = ProposalServerPolicy(allowed_projects=frozenset({"orion"}))
    async with Client(create_server(root), raise_exceptions=True) as default_client:
        default_tools = await default_client.list_tools()
    async with Client(
        create_server(root, mutation_mode="proposal-only", proposal_policy=policy),
        raise_exceptions=True,
    ) as client:
        tools = await client.list_tools()
        request = {
            "submission_id": "submission_88888888-8888-4888-8888-888888888888",
            "project": "orion",
            "subject": "Aurora",
            "predicate": "uses",
            "object": "CandidateDB",
            "valid_from": "2026-08-26T00:00:00Z",
            "rationale": "Synthetic proposal-only demo candidate.",
            "source_refs": [],
            "evidence": [],
        }
        first = await client.call_tool("propose_memory", request)
        replay = await client.call_tool("propose_memory", request)
        conflict = await client.call_tool(
            "propose_memory",
            {**request, "object": "ConflictingDB"},
        )
        listed = await client.call_tool("list_memory_proposals", {"project": "orion"})
        preview = await client.call_tool(
            "preview_memory_transition",
            {
                "project": "orion",
                "proposal_id": first.structured_content["proposal_id"],
            },
        )
        history = await client.call_tool("history", {"project": "orion"})
        status = await client.call_tool("status", {})
    return {
        "default_tools": default_tools.tools,
        "tools": tools.tools,
        "first": first,
        "replay": replay,
        "conflict": conflict,
        "listed": listed.structured_content,
        "preview": preview.structured_content,
        "history": history.structured_content,
        "status": status.structured_content,
    }


def run_demo() -> dict:
    started = time.perf_counter()
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="elm-phase5a-demo-") as temporary:
        root = Path(temporary) / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        initial_rebuild = run_cli(root, "rebuild")
        identity_absent_after_indexing = not (root / "00_registry" / "ELM_ROOT_ID.json").exists()
        identity = run_cli(
            root,
            "root-id",
            "init",
            "--apply",
            "--creator",
            "operator:phase5a-demo",
        )
        rebuilt = run_cli(root, "rebuild")
        result = asyncio.run(exercise(root))
        proposal_files = list(root.rglob("proposal_*.json"))
        evidence_files = list(root.rglob("evidence_*.json"))
        transaction_files = list((root / "01_inbox" / "elm_transactions").glob("*.json"))
        with closing(sqlite3.connect(root / ".elm" / "index.sqlite")) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]

        tool_map = {tool.name: tool for tool in result["tools"]}
        preview = result["preview"]["review_plan"]
        checks.update({
            "indexing_does_not_create_root_identity": (
                initial_rebuild["errors"] == [] and identity_absent_after_indexing
            ),
            "root_identity_has_backup": identity["created"] and (root / identity["backup"]).is_file(),
            "rebuild_clean": rebuilt["errors"] == [],
            "default_surface_is_exactly_seven_reads": (
                {tool.name for tool in result["default_tools"]} == READ_TOOLS
            ),
            "proposal_surface_is_exactly_ten": set(tool_map) == PROPOSAL_TOOLS,
            "proposal_annotation_is_additive": (
                tool_map["propose_memory"].annotations.read_only_hint is False
                and tool_map["propose_memory"].annotations.destructive_hint is False
                and tool_map["propose_memory"].annotations.idempotent_hint is True
            ),
            "proposal_is_untrusted_and_unratified": (
                result["first"].structured_content["candidate_untrusted"] is True
                and result["first"].structured_content["actor"] == "mcp:unverified"
                and result["first"].structured_content["requested_authority"] == "agent_proposal"
            ),
            "same_submission_is_idempotent": (
                result["replay"].structured_content["idempotent_replay"] is True
                and result["replay"].structured_content["proposal_id"]
                == result["first"].structured_content["proposal_id"]
            ),
            "conflicting_submission_is_rejected": result["conflict"].is_error is True,
            "one_canonical_proposal_no_orphans": (
                len(proposal_files) == 1
                and len(evidence_files) == 0
                and len(transaction_files) == 0
            ),
            "list_returns_one_candidate": result["listed"]["count"] == 1,
            "preview_is_non_signable": (
                preview["signable"] is False
                and "executor_id" not in preview
                and "policy_digest" not in preview
            ),
            "accepted_state_unchanged": (
                result["history"]["claims"] == [] and result["history"]["events"] == []
            ),
            "status_reports_proposal_only_without_acceptance": (
                result["status"]["healthy"] is True
                and result["status"]["mutation_mode"] == "proposal-only"
                and result["status"]["accepted_state_mutation_available"] is False
            ),
            "sqlite_quick_check": quick_check == "ok",
        })

    return {
        "schema": "elm-phase5a-demo-v1",
        "fixture": "synthetic",
        "passed": all(checks.values()),
        "checks": checks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-pass", action="store_true")
    arguments = parser.parse_args()
    result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
