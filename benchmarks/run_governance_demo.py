#!/usr/bin/env python3
"""Run a fully synthetic end-to-end Phase 3 lifecycle smoke test."""
from __future__ import annotations

import argparse
import hashlib
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
PROJECT = "orion"
SOURCE_BYTES = b"synthetic Phase 3 evidence; the bytes are never given to ELM"
SOURCE_HASH = hashlib.sha256(SOURCE_BYTES).hexdigest()
SOURCE_URI = "repo://synthetic/config.py"


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


def propose(
    root: Path,
    object_value: str,
    valid_from: str,
    *,
    evidence_id: str | None = None,
) -> dict:
    arguments = [
        "propose",
        "--actor",
        "agent:synthetic-researcher",
        "--project",
        PROJECT,
        "--subject",
        "backend",
        "--predicate",
        "uses_database",
        "--object",
        object_value,
        "--requested-authority",
        "agent_proposal",
        "--valid-from",
        valid_from,
        "--source-ref",
        f"{SOURCE_URI}@sha256:{SOURCE_HASH}",
        "--rationale",
        "Synthetic public-fixture proposal.",
    ]
    if evidence_id:
        arguments.extend(("--evidence", evidence_id))
    return run_cli(root, *arguments)


def run_demo() -> dict:
    started = time.perf_counter()
    checks: dict[str, bool] = {}

    with tempfile.TemporaryDirectory(prefix="elm-governance-demo-") as temporary:
        root = Path(temporary) / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        initial = run_cli(root, "rebuild")

        evidence = run_cli(
            root,
            "evidence",
            "add",
            "--actor",
            "agent:synthetic-researcher",
            "--project",
            PROJECT,
            "--kind",
            "repository_file",
            "--source-uri",
            SOURCE_URI,
            "--content-sha256",
            SOURCE_HASH,
        )
        first_proposal = propose(
            root,
            "HeliosDB",
            "2025-01-01T00:00:00Z",
            evidence_id=evidence["evidence_id"],
        )
        first_claim = run_cli(
            root,
            "accept",
            first_proposal["proposal_id"],
            "--actor",
            "human:synthetic-reviewer",
            "--authority",
            "user_ratified",
        )

        current_before = run_cli(root, "search", "HeliosDB", "--no-sync")
        second_proposal = propose(root, "NovaDB", "2026-01-01T00:00:00Z")
        second_claim = run_cli(
            root,
            "supersede",
            first_claim["claim_id"],
            second_proposal["proposal_id"],
            "--actor",
            "human:synthetic-reviewer",
            "--authority",
            "ratified_project_decision",
        )

        stale_current = run_cli(root, "search", "HeliosDB", "--no-sync")
        stale_history = run_cli(
            root, "search", "HeliosDB", "--include-history", "--no-sync"
        )
        current_after = run_cli(root, "search", "NovaDB", "--no-sync")
        history = run_cli(root, "history", "--project", PROJECT, "--no-sync")

        rebuilt = run_cli(root, "rebuild")
        current_rebuilt = run_cli(root, "search", "NovaDB", "--no-sync")
        doctor = run_cli(root, "doctor", "--no-sync")

        connection = sqlite3.connect(root / ".elm" / "index.sqlite")
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()

        evidence_path = root / evidence["path"]
        evidence_record = json.loads(evidence_path.read_text(encoding="utf-8"))
        forbidden_payload_fields = {"payload", "body", "text", "content", "excerpt"}

        checks.update(
            {
                "initial_rebuild_clean": initial["errors"] == [],
                "proposal_did_not_auto_accept": first_proposal["status"] == "pending",
                "accepted_claim_was_current": current_before["count"] >= 1,
                "old_claim_hidden_after_supersession": stale_current["count"] == 0,
                "old_claim_available_only_in_history": stale_history["count"] >= 1,
                "new_claim_is_current": any(
                    item.get("claim_id") == second_claim["claim_id"]
                    for item in current_after["results"]
                ),
                "history_preserves_both_claims": {
                    first_claim["claim_id"],
                    second_claim["claim_id"],
                }.issubset({item["claim_id"] for item in history["claims"]}),
                "rebuild_preserves_current_claim": any(
                    item.get("claim_id") == second_claim["claim_id"]
                    for item in current_rebuilt["results"]
                ),
                "doctor_clean": doctor["issue_count"] == 0,
                "sqlite_quick_check": quick_check == "ok",
                "evidence_is_reference_only": (
                    evidence_record.get("retention") == "reference_only"
                    and forbidden_payload_fields.isdisjoint(evidence_record)
                ),
                "rebuild_projection_clean": rebuilt["errors"] == [],
            }
        )

    passed = all(checks.values())
    return {
        "schema": "elm-governance-demo-v1",
        "fixture": "synthetic",
        "passed": passed,
        "checks": checks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assert-pass",
        action="store_true",
        help="Exit non-zero when any lifecycle invariant fails.",
    )
    arguments = parser.parse_args()
    result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
