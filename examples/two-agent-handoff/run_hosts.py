#!/usr/bin/env python3
"""Validate one sanitized ELM state through two independent MCP hosts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"
SCHEMA_PATH = Path(__file__).with_name("result.schema.json")
PROMPT = (
    "Use only the ELM MCP server. Call status, then context with project='orion', budget=900, "
    "and task='Recover the accepted Orion decision ODR-001 about durable storage'. Read the "
    "exact selected section if needed. Return only the requested JSON object. Set host to HOST."
)


def environment() -> dict[str, str]:
    result = os.environ.copy()
    current = result.get("PYTHONPATH")
    result["PYTHONPATH"] = (
        str(SOURCE_ROOT) if not current else os.pathsep.join((str(SOURCE_ROOT), current))
    )
    result["PYTHONIOENCODING"] = "utf-8"
    result["PYTHONUTF8"] = "1"
    return result


def run_process(command: list[str], *, cwd: Path, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment(),
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def rebuild(root: Path) -> dict[str, Any]:
    completed = run_process(
        [
            sys.executable,
            "-m",
            "elm_memory.cli",
            "rebuild",
            "--root",
            str(root),
            "--json",
        ],
        cwd=REPOSITORY_ROOT,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ELM fixture rebuild failed")
    return json.loads(completed.stdout)


def server_entry(root: Path) -> tuple[str, list[str]]:
    return sys.executable, ["-m", "elm_memory.mcp_server", "--root", str(root)]


def parse_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if {"host", "project", "database", "source_path", "section_key"}.issubset(value):
            return value
        for candidate in (value.get("structured_output"), value.get("result")):
            parsed = parse_object(candidate)
            if parsed:
                return parsed
    if isinstance(value, str):
        try:
            return parse_object(json.loads(value))
        except json.JSONDecodeError:
            return None
    return None


def run_claude(root: Path, scratch: Path, schema: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude Code executable was not found")
    command, arguments = server_entry(root)
    config = scratch / "claude-mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "elm": {
                        "type": "stdio",
                        "command": command,
                        "args": arguments,
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    completed = run_process(
        [
            executable,
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--strict-mcp-config",
            "--mcp-config",
            str(config),
            "--permission-mode",
            "plan",
            "--allowedTools",
            "mcp__elm__status,mcp__elm__context,mcp__elm__read",
            "--no-session-persistence",
            PROMPT.replace("HOST", "claude"),
        ],
        cwd=root,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or "Claude Code host run failed"
        )
    parsed = parse_object(json.loads(completed.stdout))
    if not parsed:
        raise RuntimeError("Claude Code returned no schema-matching object")
    return parsed


def antigravity_model(executable: str) -> str:
    override = os.environ.get("ELM_ANTIGRAVITY_MODEL")
    if override:
        return override
    completed = run_process([executable, "models"], cwd=REPOSITORY_ROOT, timeout=30.0)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Antigravity model discovery failed")
    available = [
        line.split("\t", 1)[0].strip()
        for line in completed.stdout.splitlines()
        if line.startswith("gemini-") and line.split("\t", 1)[0].endswith("-high")
    ]
    if not available:
        raise RuntimeError("Antigravity reported no Gemini high-effort model")
    return available[0]


def run_antigravity(root: Path, scratch: Path, schema: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which("agy")
    if not executable:
        raise RuntimeError("Antigravity CLI executable was not found")
    command, arguments = server_entry(root)
    agents = root / ".agents"
    agents.mkdir(exist_ok=True)
    (agents / "mcp_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "elm": {
                        "command": command,
                        "args": arguments,
                        "cwd": str(root),
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    completed = run_process(
        [
            executable,
            "--print",
            PROMPT.replace("HOST", "antigravity"),
            "--mode",
            "plan",
            "--sandbox",
            "--add-dir",
            str(root),
            "--model",
            antigravity_model(executable),
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--print-timeout",
            "5m",
        ],
        cwd=root,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or "Antigravity host run failed"
        )
    parsed = parse_object(json.loads(completed.stdout))
    if not parsed:
        detail = completed.stderr.strip()
        raise RuntimeError(
            "Antigravity returned no schema-matching object"
            + (f": {detail}" if detail else "")
        )
    return parsed


def run_codex(root: Path, scratch: Path, schema: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex executable was not found")
    command, arguments = server_entry(root)
    output = scratch / "codex-result.json"
    schema_file = scratch / "codex-result.schema.json"
    schema_file.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    completed = run_process(
        [
            executable,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(root),
            "--config",
            f"mcp_servers.elm.command={json.dumps(command)}",
            "--config",
            f"mcp_servers.elm.args={json.dumps(arguments)}",
            "--config",
            "mcp_servers.elm.required=true",
            "--config",
            'mcp_servers.elm.enabled_tools=["status","context","read"]',
            "--output-schema",
            str(schema_file),
            "--output-last-message",
            str(output),
            PROMPT.replace("HOST", "codex"),
        ],
        cwd=root,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Codex host run failed")
    parsed = parse_object(output.read_text(encoding="utf-8"))
    if not parsed:
        raise RuntimeError("Codex returned no schema-matching object")
    return parsed


def command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    completed = run_process([executable, "--version"], cwd=REPOSITORY_ROOT, timeout=30.0)
    return (completed.stdout or completed.stderr).strip() or None


def run_demo(hosts: tuple[str, ...]) -> dict[str, Any]:
    started = time.perf_counter()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="elm-host-demo-") as temporary:
        scratch = Path(temporary)
        root = scratch / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        rebuilt = rebuild(root)
        runners = {
            "antigravity": run_antigravity,
            "claude": run_claude,
            "codex": run_codex,
        }
        for name in hosts:
            try:
                results[name] = runners[name](root, scratch, schema)
            except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                errors[name] = str(exc)

    checks: dict[str, bool] = {"fixture_rebuild_clean": rebuilt["errors"] == []}
    for host in hosts:
        recovered = results.get(host)
        checks[f"{host}_recovered_state"] = bool(
            recovered
            and recovered["host"] == host
            and recovered["database"] == "PostgreSQL 17"
        )
    identities = {
        (result["source_path"], result["section_key"])
        for result in results.values()
    }
    checks["same_source_identity"] = len(results) == len(hosts) and len(identities) == 1
    source_identity = next(iter(identities)) if checks["same_source_identity"] else None
    return {
        "schema": "elm-heterogeneous-host-demo-v1",
        "fixture": "synthetic-orion",
        "hosts": {host: command_version("agy" if host == "antigravity" else host) for host in hosts},
        "passed": all(checks.values()) and not errors,
        "checks": checks,
        "source_identity": (
            {
                "path": source_identity[0],
                "section_key": source_identity[1],
            }
            if checks["same_source_identity"]
            else None
        ),
        "errors": errors,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-pass", action="store_true")
    parser.add_argument(
        "--hosts",
        nargs="+",
        choices=("antigravity", "claude", "codex"),
        default=("antigravity", "codex"),
        help="Two or more host CLIs to exercise (default: antigravity codex)",
    )
    arguments = parser.parse_args()
    hosts = tuple(dict.fromkeys(arguments.hosts))
    if len(hosts) < 2:
        parser.error("--hosts requires at least two distinct hosts")
    result = run_demo(hosts)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
