#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"
DEFAULT_CASES = Path(__file__).with_name("cases.json")


def run_cli(root: Path, *arguments: str) -> tuple[dict, float]:
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(SOURCE_ROOT) if not current else os.pathsep.join((str(SOURCE_ROOT), current))
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "elm_memory.cli", *arguments, "--root", str(root), "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return json.loads(completed.stdout), elapsed_ms


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def evaluate_case(root: Path, case: dict) -> dict:
    arguments = ["search", case["query"], "--limit", "5", "--no-sync"]
    if case.get("include_archive"):
        arguments.append("--include-archive")
    if case.get("broad"):
        arguments.append("--broad")
    if case.get("project"):
        arguments.extend(("--project", case["project"]))

    response, elapsed_ms = run_cli(root, *arguments)
    paths = [item["path"] for item in response["results"]]
    expected_count = case.get("expected_count")
    expected_paths = case.get("expected_paths")
    if expected_paths is None and case.get("expected_path"):
        expected_paths = [case["expected_path"]]

    if expected_count is not None:
        passed = response["count"] == expected_count
        rank = None
    else:
        matching_ranks = [paths.index(path) + 1 for path in expected_paths if path in paths]
        passed = bool(matching_ranks)
        rank = min(matching_ranks) if matching_ranks else None

    return {
        "id": case["id"],
        "query": case["query"],
        "passed": passed,
        "rank": rank,
        "result_count": response["count"],
        "paths": paths,
        "latency_ms": round(elapsed_ms, 3),
    }


def run_benchmark(cases_path: Path) -> dict:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="elm-benchmark-") as temporary:
        root = Path(temporary) / "memory"
        shutil.copytree(FIXTURE_ROOT, root)
        sync, sync_ms = run_cli(root, "rebuild")
        doctor, doctor_ms = run_cli(root, "doctor", "--no-sync")
        results = [evaluate_case(root, case) for case in cases]

    latencies = [result["latency_ms"] for result in results]
    ranked = [result for result in results if result["rank"] is not None]
    reciprocal_ranks = [1 / result["rank"] for result in ranked]
    passed = sum(1 for result in results if result["passed"])
    return {
        "schema_version": 1,
        "synthetic_only": True,
        "cases_path": str(cases_path),
        "health": {
            "files_seen": sync["files_seen"],
            "index_errors": sync["errors"],
            "doctor_issue_count": doctor["issue_count"],
            "sync_ms": round(sync_ms, 3),
            "doctor_ms": round(doctor_ms, 3),
        },
        "results": results,
        "summary": {
            "passed": passed,
            "total": len(results),
            "hit_rate_pct": round(100 * passed / len(results), 1),
            "mean_reciprocal_rank": round(statistics.fmean(reciprocal_ranks), 4),
            "median_search_ms": round(statistics.median(latencies), 3),
            "p95_search_ms": round(percentile(latencies, 0.95), 3),
            "overall_passed": passed == len(results) and not sync["errors"] and doctor["issue_count"] == 0,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the sanitized ELM deterministic-core benchmark.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assert-pass", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_benchmark(args.cases.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.assert_pass and not report["summary"]["overall_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
