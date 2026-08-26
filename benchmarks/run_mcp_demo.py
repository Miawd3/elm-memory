#!/usr/bin/env python3
"""Run a synthetic Phase 4 MCP/CLI equivalence and failure-isolation smoke test."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"
EXPECTED_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from mcp import Client

from elm_memory.mcp_server import create_server


def run_cli(root: Path, *arguments: str, check: bool = True) -> dict:
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
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    return json.loads(stream)


async def exercise(root: Path) -> dict:
    server = create_server(root)
    async with Client(server, raise_exceptions=True) as client:
        tools = await client.list_tools()
        search = await client.call_tool(
            "search",
            {"query": "Aurora gateway PostgreSQL", "project": "orion"},
        )
        context = await client.call_tool(
            "context",
            {
                "task": "Aurora gateway PostgreSQL",
                "budget": 900,
                "project": "orion",
            },
        )
        selected = search.structured_content["results"][0]
        section = await client.call_tool(
            "read",
            {"section": selected["section_key"], "project": "orion"},
        )
        related = await client.call_tool(
            "related",
            {"document": selected["path"], "project": "orion"},
        )
        history = await client.call_tool("history", {"project": "orion"})
        stats = await client.call_tool("stats", {})
        status = await client.call_tool("status", {})
    return {
        "tools": tools.tools,
        "search": search.structured_content,
        "context": context.structured_content,
        "section": section.structured_content,
        "related": related.structured_content,
        "history": history.structured_content,
        "stats": stats.structured_content,
        "status": status.structured_content,
    }


async def exercise_missing_index(root: Path):
    async with Client(create_server(root), raise_exceptions=True) as client:
        return await client.call_tool("search", {"query": "Aurora gateway PostgreSQL"})


def run_demo() -> dict:
    started = time.perf_counter()
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="elm-mcp-demo-") as temporary:
        root = Path(temporary) / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        missing_failure = asyncio.run(exercise_missing_index(root))
        rebuilt = run_cli(root, "rebuild")
        cli_search = run_cli(
            root,
            "search",
            "Aurora gateway PostgreSQL",
            "--project",
            "orion",
            "--no-sync",
        )
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
        mcp = asyncio.run(exercise(root))
        direct_after_failure = run_cli(
            root,
            "search",
            "Aurora gateway PostgreSQL",
            "--no-sync",
        )

        cli_ids = [item["section_key"] for item in cli_search["results"]]
        mcp_ids = [item["section_key"] for item in mcp["search"]["results"]]
        tools = {tool.name for tool in mcp["tools"]}
        annotations_ok = all(
            tool.annotations
            and tool.annotations.read_only_hint is True
            and tool.annotations.open_world_hint is False
            for tool in mcp["tools"]
        )
        checks.update(
            {
                "rebuild_clean": rebuilt["errors"] == [],
                "only_read_tools_exposed": tools == EXPECTED_TOOLS,
                "read_annotations_present": annotations_ok,
                "cli_mcp_source_identity_equivalent": cli_ids == mcp_ids,
                "cli_mcp_scope_equivalent": cli_context["scope"] == mcp["context"]["scope"],
                "cli_mcp_packet_selection_equivalent": (
                    cli_context["selected_section_keys"]
                    == mcp["context"]["selected_section_keys"]
                ),
                "exact_section_read": "PostgreSQL 17" in mcp["section"]["text"],
                "related_links_returned": bool(mcp["related"]["outgoing"]),
                "history_available": mcp["history"]["project"] == "orion",
                "stats_available": mcp["stats"]["docs"] == 8,
                "status_healthy": mcp["status"]["healthy"] is True,
                "missing_index_fails_as_tool_result": missing_failure.is_error is True,
                "mcp_failure_does_not_break_cli": direct_after_failure["count"] > 0,
                "context_trace_disabled": mcp["context"]["trace"]["recorded"] is False,
            }
        )

    return {
        "schema": "elm-mcp-demo-v1",
        "fixture": "synthetic",
        "passed": all(checks.values()),
        "checks": checks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assert-pass",
        action="store_true",
        help="Exit non-zero when any Phase 4 invariant fails.",
    )
    arguments = parser.parse_args()
    result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
