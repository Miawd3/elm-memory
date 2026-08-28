#!/usr/bin/env python3
"""Deterministic wheel install, validation, and rollback acceptance harness for ELM.

Uses the Python standard library only. Safe on Windows and Linux.
Never uses a personal ELM root; exercises synthetic memory only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any


WHEEL_FILENAME_RE = re.compile(
    r"^([A-Za-z0-9_]+)-([0-9A-Za-z.+~]+)(?:-[0-9]+)?-([^-]+)-([^-]+)-([^-]+)\.whl$"
)
DEFAULT_TIMEOUT_SECONDS = 60.0
FAST_TIMEOUT_SECONDS = 30.0


def parse_wheel_filename(path: Path) -> dict[str, str]:
    """Parse and validate wheel filename according to PEP 427."""
    if not path.is_file():
        raise FileNotFoundError(f"Wheel file not found: {path.name}")
    if path.suffix.lower() != ".whl":
        raise ValueError(f"File is not a wheel archive (.whl): {path.name}")
    match = WHEEL_FILENAME_RE.match(path.name)
    if not match:
        raise ValueError(f"Wheel filename does not conform to PEP 427: {path.name}")
    raw_dist, version, py_tag, abi_tag, plat_tag = match.groups()
    if raw_dist != "elm_memory":
        raise ValueError(
            f"Invalid distribution in wheel filename: '{raw_dist}' (PEP 427 requires normalized 'elm_memory')"
        )
    return {
        "distribution": raw_dist,
        "version": version,
        "python_tag": py_tag,
        "abi_tag": abi_tag,
        "platform_tag": plat_tag,
    }


def validate_wheel_path(path: Path) -> tuple[Path, dict[str, str]]:
    """Validate wheel existence and parse metadata."""
    resolved = path.resolve()
    info = parse_wheel_filename(resolved)
    return resolved, info


def validate_virtualenv_pyz_path(path: Path) -> Path:
    """Validate that the virtualenv zipapp exists, is a regular file, is not a symlink, and ends in .pyz."""
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"virtualenv zipapp must not be a symlink: {p.name}")
    if not p.is_file():
        raise FileNotFoundError(f"virtualenv zipapp file not found: {p.name}")
    if p.suffix.lower() != ".pyz":
        raise ValueError(f"virtualenv zipapp must have .pyz extension: {p.name}")
    return p.resolve()


def validate_fixture_root(root: Path) -> Path:
    """Ensure fixture exists, has no symlinks or .elm runtime state, and contains markdown."""
    if not root.is_dir():
        raise FileNotFoundError(f"Fixture root is not a directory: {root.name}")
    resolved_root = root.resolve()
    has_md = False
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Fixture contains forbidden symlink: {path.name}")
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"Fixture path resolves outside fixture root: {path.name}") from exc
        parts_lower = tuple(part.lower() for part in path.relative_to(root).parts)
        if ".elm" in parts_lower or any(p.startswith(".elm") for p in parts_lower):
            raise ValueError(f"Fixture contains forbidden .elm runtime state: {path.name}")
        if path.is_file() and path.suffix.lower() == ".md":
            has_md = True
    if not has_md:
        raise ValueError(f"Fixture root contains no Markdown files: {root.name}")
    return root


def hash_canonical_markdown(root: Path) -> dict[str, str]:
    """Compute sha256 digests of all canonical Markdown files in deterministic relative order."""
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"Symlink encountered in canonical memory: {path.name}")
        rel_posix = path.relative_to(root).as_posix()
        hashes[rel_posix] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hashes:
        raise ValueError("No canonical Markdown files found to hash")
    return hashes


def assert_markdown_unchanged(root: Path, baseline: dict[str, str], stage: str) -> None:
    """Verify that canonical Markdown files have not drifted from the baseline."""
    current = hash_canonical_markdown(root)
    if current != baseline:
        added = sorted(set(current) - set(baseline))
        removed = sorted(set(baseline) - set(current))
        modified = sorted(k for k in current if k in baseline and current[k] != baseline[k])
        details: list[str] = []
        if added:
            details.append(f"added: {added}")
        if removed:
            details.append(f"removed: {removed}")
        if modified:
            details.append(f"modified: {modified}")
        raise RuntimeError(f"Canonical Markdown drift detected at stage '{stage}': {', '.join(details)}")


def locate_venv_executables(venv_dir: Path) -> dict[str, Path]:
    """Locate executables inside the virtual environment."""
    if sys.platform == "win32":
        scripts_dir = venv_dir / "Scripts"
        python_exe = scripts_dir / "python.exe"
        pip_exe = scripts_dir / "pip.exe"
        elm_exe = scripts_dir / "elm.exe"
        elm_mcp_exe = scripts_dir / "elm-mcp.exe"
    else:
        bin_dir = venv_dir / "bin"
        python_exe = bin_dir / "python"
        pip_exe = bin_dir / "pip"
        elm_exe = bin_dir / "elm"
        elm_mcp_exe = bin_dir / "elm-mcp"
    return {
        "python": python_exe,
        "pip": pip_exe,
        "elm": elm_exe,
        "elm-mcp": elm_mcp_exe,
    }


def assert_entry_point_containment(entry_path: Path, venv_dir: Path, name: str) -> None:
    """Fail closed if an entry point is missing or escapes the virtual environment prefix."""
    if not entry_path.is_file():
        raise FileNotFoundError(f"Console entry point '{name}' not found at: {entry_path.name}")
    try:
        entry_path.resolve().relative_to(venv_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"Console entry point '{name}' escaped the virtual environment boundary"
        ) from exc


def run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run an isolated subprocess with bounded timeouts and sanitized environment."""
    env = {
        "HOME": str(cwd),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "USERPROFILE": str(cwd),
    }
    for var in ("SYSTEMROOT", "COMSPEC", "WINDIR", "PATHEXT", "TEMP", "TMP"):
        if var in os.environ:
            env[var] = os.environ[var]
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        cmd_name = Path(cmd[0]).name if cmd else "unknown"
        raise TimeoutError(f"Subprocess timed out after {timeout}s: {cmd_name}") from exc


def create_isolated_venv(
    venv_dir: Path,
    virtualenv_pyz: Path | None = None,
) -> dict[str, Path]:
    """Create an isolated virtual environment using standard venv or a verified virtualenv zipapp."""
    if virtualenv_pyz is not None:
        pyz_path = validate_virtualenv_pyz_path(virtualenv_pyz)
        cmd = [sys.executable, str(pyz_path), "--no-download", str(venv_dir)]
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]

    ret, _, _ = run_command(
        cmd,
        cwd=venv_dir.parent,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"Failed to create isolated virtual environment (code {ret})")
    executables = locate_venv_executables(venv_dir)
    if not executables["python"].is_file():
        raise FileNotFoundError(f"Python executable missing in venv: {executables['python'].name}")
    return executables


def pip_install_wheel(
    venv_python: Path,
    wheel_path: Path,
    *,
    force_reinstall: bool = False,
) -> None:
    """Install wheel using pip without network, without dependencies, and with version checks disabled."""
    cmd = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-index",
        "--disable-pip-version-check",
    ]
    if force_reinstall:
        cmd.append("--force-reinstall")
    cmd.append(str(wheel_path))
    ret, _, _ = run_command(cmd, cwd=wheel_path.parent, timeout=DEFAULT_TIMEOUT_SECONDS)
    if ret != 0:
        raise RuntimeError(f"pip install failed for wheel {wheel_path.name} (code {ret})")


def pip_uninstall_package(venv_python: Path, package_name: str = "elm-memory") -> None:
    """Uninstall package using pip."""
    cmd = [
        str(venv_python),
        "-m",
        "pip",
        "uninstall",
        "-y",
        "--disable-pip-version-check",
        package_name,
    ]
    ret, _, _ = run_command(cmd, cwd=venv_python.parent, timeout=DEFAULT_TIMEOUT_SECONDS)
    if ret != 0:
        raise RuntimeError(f"pip uninstall failed for package {package_name} (code {ret})")


def verify_installed_distribution(
    venv_python: Path,
    expected_version: str,
    package_name: str = "elm-memory",
) -> str:
    """Query and assert the installed package distribution version via importlib.metadata."""
    script = (
        f"import importlib.metadata as m, sys; "
        f"sys.stdout.write(m.version('{package_name}'))"
    )
    ret, out, _ = run_command(
        [str(venv_python), "-c", script],
        cwd=venv_python.parent,
        timeout=FAST_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"Failed to query installed {package_name} version (code {ret})")
    version = out.strip()
    if version != expected_version:
        raise ValueError(
            f"Installed {package_name} version mismatch: expected '{expected_version}', got '{version}'"
        )
    return version


def verify_import_fails(
    venv_python: Path,
    module_name: str = "elm_memory",
) -> None:
    """Verify that importing the package fails cleanly after uninstallation."""
    script = f"import {module_name}"
    ret, _, _ = run_command(
        [str(venv_python), "-c", script],
        cwd=venv_python.parent,
        timeout=FAST_TIMEOUT_SECONDS,
    )
    if ret == 0:
        raise RuntimeError(f"Module '{module_name}' remains importable after uninstallation")


def validate_memory_operations(
    elm_bin: Path,
    memory_root: Path,
) -> dict[str, int]:
    """Execute rebuild, doctor, search, context, and quick_check; validate parsed responses."""
    # 1. rebuild
    ret, out, _ = run_command(
        [str(elm_bin), "rebuild", "--root", str(memory_root), "--json"],
        cwd=memory_root,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"elm rebuild failed (code {ret})")
    try:
        rebuild_data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ValueError("elm rebuild returned malformed JSON") from exc
    if not isinstance(rebuild_data, dict):
        raise ValueError("elm rebuild output is not a JSON object")
    if rebuild_data.get("errors"):
        raise RuntimeError("elm rebuild reported index errors in synthetic memory")

    # 2. doctor --no-sync
    ret, out, _ = run_command(
        [str(elm_bin), "doctor", "--root", str(memory_root), "--json", "--no-sync"],
        cwd=memory_root,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"elm doctor failed (code {ret})")
    try:
        doctor_data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ValueError("elm doctor returned malformed JSON") from exc
    if not isinstance(doctor_data, dict):
        raise ValueError("elm doctor output is not a JSON object")
    if doctor_data.get("healthy") is False or doctor_data.get("issue_count", 0) != 0:
        raise RuntimeError("elm doctor reported unhealthy memory state")

    # 3. search for known synthetic fact: PostgreSQL
    ret, out, _ = run_command(
        [str(elm_bin), "search", "PostgreSQL", "--root", str(memory_root), "--json"],
        cwd=memory_root,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"elm search failed (code {ret})")
    try:
        search_data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ValueError("elm search returned malformed JSON") from exc
    if not isinstance(search_data, dict) or search_data.get("count", 0) < 1:
        raise RuntimeError("elm search failed to recover known synthetic PostgreSQL fact")

    # 4. context with bounded budget
    ret, out, _ = run_command(
        [
            str(elm_bin),
            "context",
            "database choices",
            "--budget",
            "700",
            "--root",
            str(memory_root),
            "--json",
            "--no-trace",
        ],
        cwd=memory_root,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if ret != 0:
        raise RuntimeError(f"elm context failed (code {ret})")
    try:
        context_data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ValueError("elm context returned malformed JSON") from exc
    if not isinstance(context_data, dict):
        raise ValueError("elm context output is not a JSON object")
    estimated_tokens = context_data.get("estimated_tokens", 0)
    if estimated_tokens <= 0 or estimated_tokens > 700:
        raise RuntimeError(
            f"elm context estimated tokens ({estimated_tokens}) violated budget limit (700)"
        )

    # 5. SQLite PRAGMA quick_check and document/section count inspection
    db_file = memory_root / ".elm" / "index.sqlite"
    if not db_file.is_file():
        raise FileNotFoundError(f"Derived index database file missing: {db_file.name}")
    con = sqlite3.connect(str(db_file))
    try:
        rows = con.execute("PRAGMA quick_check;").fetchall()
        if rows != [("ok",)]:
            raise RuntimeError(f"SQLite PRAGMA quick_check failed: {rows}")
        doc_count = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        sec_count = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    finally:
        con.close()

    if doc_count <= 0 or sec_count <= 0:
        raise RuntimeError(f"Indexed item counts invalid: documents={doc_count}, sections={sec_count}")

    return {"documents": doc_count, "sections": sec_count}


def exercise_disposable_index(
    elm_bin: Path,
    memory_root: Path,
    expected_counts: dict[str, int],
) -> None:
    """Delete index.sqlite, rebuild it, and assert stable counts."""
    db_file = memory_root / ".elm" / "index.sqlite"
    if not db_file.is_file():
        raise FileNotFoundError(f"Index database missing before deletion: {db_file.name}")
    db_file.unlink()
    for suffix in ("-wal", "-shm"):
        extra = memory_root / ".elm" / f"index.sqlite{suffix}"
        if extra.exists():
            extra.unlink()

    counts = validate_memory_operations(elm_bin, memory_root)
    if counts != expected_counts:
        raise RuntimeError(
            f"Rebuilt index counts ({counts}) do not match pre-deletion counts ({expected_counts})"
        )


def sanitize_error_message(exc: Exception) -> str:
    """Produce a bounded, deterministic public error message with all filesystem paths redacted."""
    msg = str(exc)
    exc_type = type(exc).__name__

    try:
        home_path = Path.home().resolve()
        if str(home_path) and str(home_path) != "/":
            msg = msg.replace(str(home_path), "<home>")
            msg = msg.replace(home_path.as_posix(), "<home>")
    except Exception:
        pass

    try:
        temp_path = Path(tempfile.gettempdir()).resolve()
        if str(temp_path) and str(temp_path) != "/":
            msg = msg.replace(str(temp_path), "<temp>")
            msg = msg.replace(temp_path.as_posix(), "<temp>")
    except Exception:
        pass

    # Scrub temporary directory patterns (e.g. elm-release-acceptance-*)
    msg = re.sub(r"elm-release-acceptance-[A-Za-z0-9_\-]+", "<temp_dir>", msg)

    # Scrub Windows drive paths: C:\... or C:/...
    msg = re.sub(r"(?i)[A-Za-z]:[\\/][^ \t\r\n'\"]+", "<path>", msg)

    # Scrub POSIX absolute paths: /.../...
    msg = re.sub(r"/(?:[^ \t\r\n'\"]+/)+[^ \t\r\n'\"]*", "<path>", msg)

    # Scrub wheel filenames or references
    msg = re.sub(r"[A-Za-z0-9._\-]+\.whl", "<wheel>", msg)

    # Scrub zipapp filenames or references (.pyz)
    msg = re.sub(r"[A-Za-z0-9._\-]+\.pyz", "<virtualenv_pyz>", msg)

    # Scrub auth credentials or URI schemes
    msg = re.sub(r"[A-Za-z0-9._%+-]+:[A-Za-z0-9._%+-]+@", "<redacted_auth>@", msg)
    msg = re.sub(r"(?:https?|file|repo)://\S+", "<redacted_uri>", msg)

    # Collapse duplicate <path> markers
    msg = re.sub(r"(?:<path>[\\/]?)+", "<path>", msg)
    msg = re.sub(r"\s+", " ", msg).strip()

    result = f"{exc_type}: {msg}"
    if len(result) > 200:
        result = result[:197] + "..."
    return result


def build_sanitized_report(
    *,
    status: str,
    mode: str,
    candidate_version: str,
    previous_version: str | None,
    markdown_documents: int,
    documents_indexed: int,
    sections_indexed: int,
    transitions_verified: list[str],
    error_message: str | None = None,
) -> dict[str, Any]:
    """Produce a bounded machine-readable report with zero sensitive data."""
    report: dict[str, Any] = {
        "status": status,
        "mode": mode,
        "candidate_version": candidate_version,
        "previous_version": previous_version,
        "markdown_documents": markdown_documents,
        "documents_indexed": documents_indexed,
        "sections_indexed": sections_indexed,
        "transitions_verified": list(transitions_verified),
    }
    if error_message is not None:
        report["error"] = error_message
    return report


def run_acceptance_pipeline(
    *,
    candidate_wheel: Path,
    previous_wheel: Path | None,
    fixture_root: Path,
    virtualenv_pyz: Path | None = None,
) -> dict[str, Any]:
    """Run full candidate-only or rollback acceptance pipeline in an isolated temporary directory."""
    cand_path, cand_info = validate_wheel_path(candidate_wheel)
    prev_path, prev_info = (
        validate_wheel_path(previous_wheel) if previous_wheel else (None, None)
    )
    validate_fixture_root(fixture_root)
    validated_pyz = (
        validate_virtualenv_pyz_path(virtualenv_pyz) if virtualenv_pyz else None
    )

    mode = "rollback" if prev_path else "candidate_only"
    transitions: list[str] = ["fixture_validated"]

    with tempfile.TemporaryDirectory(prefix="elm-release-acceptance-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        memory_root = temp_dir / "memory"
        venv_dir = temp_dir / "venv"

        shutil.copytree(fixture_root, memory_root, symlinks=False)
        baseline_hashes = hash_canonical_markdown(memory_root)
        transitions.append("synthetic_memory_initialized")

        executables = create_isolated_venv(venv_dir, virtualenv_pyz=validated_pyz)
        transitions.append("isolated_venv_created")

        if mode == "candidate_only":
            # 1. Install candidate
            pip_install_wheel(executables["python"], cand_path, force_reinstall=False)
            assert_markdown_unchanged(memory_root, baseline_hashes, "candidate_installed")
            transitions.append("candidate_installed")

            # 2. Verify distribution version and entry points
            verify_installed_distribution(executables["python"], cand_info["version"])
            assert_entry_point_containment(executables["elm"], venv_dir, "elm")
            assert_entry_point_containment(executables["elm-mcp"], venv_dir, "elm-mcp")
            transitions.append("entry_points_verified")

            # 3. Validate memory operations
            counts = validate_memory_operations(executables["elm"], memory_root)
            assert_markdown_unchanged(memory_root, baseline_hashes, "memory_validated")
            transitions.append("memory_operations_validated")

            # 4. Exercise disposable index rebuild
            exercise_disposable_index(executables["elm"], memory_root, counts)
            assert_markdown_unchanged(memory_root, baseline_hashes, "index_rebuilt")
            transitions.append("disposable_index_rebuilt")

            # 5. Uninstall candidate
            pip_uninstall_package(executables["python"], "elm-memory")
            verify_import_fails(executables["python"], "elm_memory")
            assert_markdown_unchanged(memory_root, baseline_hashes, "candidate_uninstalled")
            transitions.append("candidate_uninstalled")

            return build_sanitized_report(
                status="passed",
                mode=mode,
                candidate_version=cand_info["version"],
                previous_version=None,
                markdown_documents=len(baseline_hashes),
                documents_indexed=counts["documents"],
                sections_indexed=counts["sections"],
                transitions_verified=transitions,
            )

        else:
            assert prev_path is not None and prev_info is not None
            # 1. Install previous wheel
            pip_install_wheel(executables["python"], prev_path, force_reinstall=False)
            assert_markdown_unchanged(memory_root, baseline_hashes, "previous_installed")
            transitions.append("previous_installed")

            verify_installed_distribution(executables["python"], prev_info["version"])
            assert_entry_point_containment(executables["elm"], venv_dir, "elm")
            assert_entry_point_containment(executables["elm-mcp"], venv_dir, "elm-mcp")
            transitions.append("previous_entry_points_verified")

            prev_counts = validate_memory_operations(executables["elm"], memory_root)
            assert_markdown_unchanged(memory_root, baseline_hashes, "previous_memory_validated")
            transitions.append("previous_memory_validated")

            # 2. Upgrade to candidate wheel
            pip_install_wheel(executables["python"], cand_path, force_reinstall=True)
            assert_markdown_unchanged(memory_root, baseline_hashes, "candidate_upgraded")
            transitions.append("candidate_upgraded")

            verify_installed_distribution(executables["python"], cand_info["version"])
            cand_counts = validate_memory_operations(executables["elm"], memory_root)
            assert_markdown_unchanged(memory_root, baseline_hashes, "candidate_memory_validated")
            transitions.append("candidate_memory_validated")

            # 3. Rollback to previous wheel
            pip_install_wheel(executables["python"], prev_path, force_reinstall=True)
            assert_markdown_unchanged(memory_root, baseline_hashes, "previous_rolled_back")
            transitions.append("previous_rolled_back")

            verify_installed_distribution(executables["python"], prev_info["version"])
            exercise_disposable_index(executables["elm"], memory_root, prev_counts)
            assert_markdown_unchanged(memory_root, baseline_hashes, "rolled_back_index_rebuilt")
            transitions.append("rolled_back_index_rebuilt")

            return build_sanitized_report(
                status="passed",
                mode=mode,
                candidate_version=cand_info["version"],
                previous_version=prev_info["version"],
                markdown_documents=len(baseline_hashes),
                documents_indexed=cand_counts["documents"],
                sections_indexed=cand_counts["sections"],
                transitions_verified=transitions,
            )


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated wheel install, validation, and rollback acceptance for ELM."
    )
    parser.add_argument(
        "--candidate-wheel",
        type=Path,
        required=True,
        help="Path to candidate .whl archive to validate",
    )
    parser.add_argument(
        "--previous-wheel",
        type=Path,
        default=None,
        help="Optional path to previous .whl archive for rollback verification",
    )
    parser.add_argument(
        "--virtualenv-pyz",
        type=Path,
        default=None,
        help="Optional path to official virtualenv .pyz zipapp for offline environment creation",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=None,
        help="Optional path to synthetic memory fixture (default: tests/fixtures/sample_elm)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output bounded sanitized JSON report",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_arguments(arguments)
    repo_root = Path(__file__).resolve().parent.parent
    fixture_root = (
        args.fixture_root.resolve()
        if args.fixture_root
        else repo_root / "tests" / "fixtures" / "sample_elm"
    )

    cand_version = "unknown"
    prev_version = None
    try:
        _, cand_info = validate_wheel_path(args.candidate_wheel)
        cand_version = cand_info["version"]
    except Exception:
        pass

    if args.previous_wheel:
        try:
            _, prev_info = validate_wheel_path(args.previous_wheel)
            prev_version = prev_info["version"]
        except Exception:
            pass

    try:
        report = run_acceptance_pipeline(
            candidate_wheel=args.candidate_wheel,
            previous_wheel=args.previous_wheel,
            fixture_root=fixture_root,
            virtualenv_pyz=args.virtualenv_pyz,
        )
    except Exception as exc:
        sanitized_error = sanitize_error_message(exc)
        report = build_sanitized_report(
            status="failed",
            mode="rollback" if args.previous_wheel else "candidate_only",
            candidate_version=cand_version,
            previous_version=prev_version,
            markdown_documents=0,
            documents_indexed=0,
            sections_indexed=0,
            transitions_verified=[],
            error_message=sanitized_error,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL: ELM release acceptance failed: {sanitized_error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        mode_desc = "rollback" if report["mode"] == "rollback" else "candidate-only"
        print(
            f"PASS: ELM release acceptance ({mode_desc}) passed for {report['candidate_version']} "
            f"({report['markdown_documents']} documents, {report['sections_indexed']} sections indexed)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
