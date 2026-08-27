#!/usr/bin/env python3
"""Validate one sanitized ELM state through two independent MCP hosts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
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
    "exact selected section if needed. Set database to only the exact product name and major "
    "version, with no surrounding prose. Return only the requested JSON object. Set host to HOST."
)
EXPECTED_DATABASE = "PostgreSQL 17"
EXPECTED_SOURCE_PATH = "20_projects/orion/DECISIONS.md"
MCP_SERVER_NAME = "elm_demo"
READ_TOOLS = ("status", "context", "read")


def environment(*, repository_access: bool = True) -> dict[str, str]:
    result = os.environ.copy()
    if repository_access:
        current = result.get("PYTHONPATH")
        result["PYTHONPATH"] = (
            str(SOURCE_ROOT) if not current else os.pathsep.join((str(SOURCE_ROOT), current))
        )
    else:
        result.pop("PYTHONPATH", None)
    result["PYTHONIOENCODING"] = "utf-8"
    result["PYTHONUTF8"] = "1"
    return result


def run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = 300.0,
    repository_access: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment(repository_access=repository_access),
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


def prepare_runtime(target: Path) -> Path:
    runtime = target / "runtime"
    runtime.mkdir(parents=True)
    shutil.copytree(SOURCE_ROOT / "elm_memory", runtime / "elm_memory")
    return runtime


@contextmanager
def disposable_directory(prefix: str):
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        for attempt in range(8):
            try:
                shutil.rmtree(path)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == 7:
                    raise RuntimeError("Disposable demo workspace could not be removed")
                time.sleep(0.1 * (attempt + 1))


def server_entry(root: Path, runtime: Path) -> tuple[str, list[str]]:
    bootstrap = (
        "import sys;"
        f"sys.path.insert(0,{str(runtime)!r});"
        "from elm_memory.mcp_server import main;"
        "raise SystemExit(main())"
    )
    return sys.executable, ["-c", bootstrap, "--root", str(root)]


class HostRunError(RuntimeError):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def provider_failure_category(stdout: str, stderr: str) -> str:
    detail = f"{stdout}\n{stderr}".casefold()
    if any(marker in detail for marker in ("oauth session expired", "failed to authenticate")):
        return "auth_failed"
    if any(marker in detail for marker in ("quota reached", "quota exhausted", "resource_exhausted")):
        return "quota_exhausted"
    if "permission" in detail and any(marker in detail for marker in ("denied", "auto-denied")):
        return "permission_denied"
    return "execution_failed"


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


def normalized_fact(value: Any) -> str:
    return str(value).strip().rstrip(".").casefold()


def expected_section_key(root: Path) -> str:
    completed = run_process(
        [
            sys.executable,
            "-m",
            "elm_memory.cli",
            "search",
            "durable telemetry records PostgreSQL",
            "--project",
            "orion",
            "--no-sync",
            "--root",
            str(root),
            "--json",
        ],
        cwd=REPOSITORY_ROOT,
    )
    if completed.returncode != 0:
        raise RuntimeError("Synthetic expected-section lookup failed")
    matches = [
        item
        for item in json.loads(completed.stdout).get("results", [])
        if item.get("path") == EXPECTED_SOURCE_PATH and item.get("heading") == "ODR-001 — PostgreSQL storage"
    ]
    if len(matches) != 1:
        raise RuntimeError("Synthetic expected-section lookup was ambiguous")
    return str(matches[0]["section_key"])


def run_claude(
    root: Path, runtime: Path, workspace: Path, schema: dict[str, Any]
) -> dict[str, Any]:
    executable = shutil.which("claude")
    if not executable:
        raise HostRunError("unavailable")
    command, arguments = server_entry(root, runtime)
    config = workspace / "claude-mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
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
            ",".join(f"mcp__{MCP_SERVER_NAME}__{tool}" for tool in READ_TOOLS),
            "--no-session-persistence",
            PROMPT.replace("HOST", "claude"),
        ],
        cwd=workspace,
        repository_access=False,
    )
    if completed.returncode != 0:
        raise HostRunError(provider_failure_category(completed.stdout, completed.stderr))
    parsed = parse_object(json.loads(completed.stdout))
    if not parsed:
        raise HostRunError("schema_response_missing")
    return parsed


def antigravity_model(executable: str) -> str:
    override = os.environ.get("ELM_ANTIGRAVITY_MODEL")
    if override:
        return override
    completed = run_process(
        [executable, "models"],
        cwd=REPOSITORY_ROOT,
        timeout=30.0,
        repository_access=False,
    )
    if completed.returncode != 0:
        raise HostRunError("model_discovery_failed")
    available = [
        line.split("\t", 1)[0].strip()
        for line in completed.stdout.splitlines()
        if line.startswith("gemini-") and line.split("\t", 1)[0].endswith("-high")
    ]
    if not available:
        raise HostRunError("model_unavailable")
    return available[0]


def run_antigravity(
    root: Path, runtime: Path, workspace: Path, schema: dict[str, Any]
) -> dict[str, Any]:
    executable = shutil.which("agy")
    if not executable:
        raise HostRunError("unavailable")
    command, arguments = server_entry(root, runtime)
    agents = workspace / ".agents"
    agents.mkdir(exist_ok=True)
    (agents / "mcp_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "command": command,
                        "args": arguments,
                        "cwd": str(workspace),
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
            str(workspace),
            "--model",
            antigravity_model(executable),
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--print-timeout",
            "5m",
        ],
        cwd=workspace,
        repository_access=False,
    )
    if completed.returncode != 0:
        raise HostRunError(provider_failure_category(completed.stdout, completed.stderr))
    parsed = parse_object(json.loads(completed.stdout))
    if not parsed:
        raise HostRunError("schema_response_missing")
    return parsed


def run_codex(
    root: Path, runtime: Path, workspace: Path, schema: dict[str, Any]
) -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        raise HostRunError("unavailable")
    command, arguments = server_entry(root, runtime)
    output = workspace / "codex-result.json"
    schema_file = workspace / "codex-result.schema.json"
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
            str(workspace),
            "--config",
            f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(command)}",
            "--config",
            f"mcp_servers.{MCP_SERVER_NAME}.args={json.dumps(arguments)}",
            "--config",
            f"mcp_servers.{MCP_SERVER_NAME}.required=true",
            "--config",
            f"mcp_servers.{MCP_SERVER_NAME}.enabled_tools={json.dumps(list(READ_TOOLS))}",
            "--output-schema",
            str(schema_file),
            "--output-last-message",
            str(output),
            PROMPT.replace("HOST", "codex"),
        ],
        cwd=workspace,
        repository_access=False,
    )
    if completed.returncode != 0:
        raise HostRunError(provider_failure_category(completed.stdout, completed.stderr))
    parsed = parse_object(output.read_text(encoding="utf-8"))
    if not parsed:
        raise HostRunError("schema_response_missing")
    return parsed


def command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    completed = run_process(
        [executable, "--version"],
        cwd=REPOSITORY_ROOT,
        timeout=30.0,
        repository_access=False,
    )
    lines = (completed.stdout or completed.stderr).strip().splitlines()
    if len(lines) != 1:
        return None
    value = lines[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()+/-]{0,119}", value):
        return None
    return value


def run_demo(hosts: tuple[str, ...]) -> dict[str, Any]:
    started = time.perf_counter()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    with disposable_directory("elm-host-demo-") as scratch:
        runtime = prepare_runtime(scratch)
        root = scratch / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        rebuilt = rebuild(root)
        expected_key = expected_section_key(root)
        runners = {
            "antigravity": run_antigravity,
            "claude": run_claude,
            "codex": run_codex,
        }
        for name in hosts:
            workspace = scratch / "workspaces" / name
            workspace.mkdir(parents=True)
            try:
                results[name] = runners[name](root, runtime, workspace, schema)
            except HostRunError as exc:
                errors[name] = exc.category
            except subprocess.TimeoutExpired:
                errors[name] = "timeout"
            except (OSError, json.JSONDecodeError):
                errors[name] = "invalid_host_response"

    checks: dict[str, bool] = {"fixture_rebuild_clean": rebuilt["errors"] == []}
    for host in hosts:
        recovered = results.get(host)
        host_checks = {
            f"{host}_host_label": bool(recovered and recovered["host"] == host),
            f"{host}_project": bool(
                recovered and normalized_fact(recovered["project"]) == "orion"
            ),
            f"{host}_database": bool(
                recovered
                and normalized_fact(recovered["database"])
                == normalized_fact(EXPECTED_DATABASE)
            ),
            f"{host}_source_path": bool(
                recovered and recovered["source_path"] == EXPECTED_SOURCE_PATH
            ),
            f"{host}_section_key": bool(
                recovered and recovered["section_key"] == expected_key
            ),
        }
        checks.update(host_checks)
        checks[f"{host}_recovered_state"] = all(host_checks.values())
    identities = {
        (result["source_path"], result["section_key"])
        for result in results.values()
    }
    checks["same_source_identity"] = len(results) == len(hosts) and len(identities) == 1
    all_hosts_recovered = all(checks[f"{host}_recovered_state"] for host in hosts)
    source_identity_verified = checks["same_source_identity"] and all_hosts_recovered
    return {
        "schema": "elm-heterogeneous-host-demo-v1",
        "fixture": "synthetic-orion",
        "hosts": {host: command_version("agy" if host == "antigravity" else host) for host in hosts},
        "passed": all(checks.values()) and not errors,
        "checks": checks,
        "source_identity": (
            {
                "path": EXPECTED_SOURCE_PATH,
                "section_key": expected_key,
            }
            if source_identity_verified
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
