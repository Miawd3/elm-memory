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
CONTEXT_BUDGET = 700


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


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def is_archive_path(relative: str) -> bool:
    parts = set(Path(relative).parts)
    name = Path(relative).name.lower()
    return bool(parts & {"backups", "99_archive"}) or ".old." in name or name.endswith(".bak.md")


def project_for_path(relative: str) -> str | None:
    parts = Path(relative).parts
    if len(parts) >= 3 and parts[0] == "20_projects":
        return parts[1]
    return None


def expected_paths(case: dict) -> list[str]:
    paths = case.get("expected_paths")
    if paths is None and case.get("expected_path"):
        paths = [case["expected_path"]]
    return list(paths or [])


def full_file_baseline(root: Path, case: dict) -> dict:
    paths: list[str] = []
    tokens = 0
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if not case.get("include_archive") and is_archive_path(relative):
            continue
        if case.get("project") and project_for_path(relative) != case["project"]:
            continue
        paths.append(relative)
        tokens += estimate_tokens(path.read_text(encoding="utf-8"))
    expected = expected_paths(case)
    return {
        "estimated_tokens": tokens,
        "retrieval_hit": any(path in paths for path in expected) if expected else None,
        "document_count": len(paths),
    }


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
    expected = expected_paths(case)

    if expected_count is not None:
        passed = response["count"] == expected_count
        rank = None
    else:
        matching_ranks = [paths.index(path) + 1 for path in expected if path in paths]
        passed = bool(matching_ranks)
        rank = min(matching_ranks) if matching_ranks else None

    context_arguments = [
        "context",
        case["query"],
        "--budget",
        str(CONTEXT_BUDGET),
        "--limit",
        "12",
        "--no-sync",
        "--no-trace",
    ]
    if case.get("include_archive"):
        context_arguments.append("--include-archive")
    if case.get("project"):
        context_arguments.extend(("--project", case["project"]))
    context, context_ms = run_cli(root, *context_arguments)
    context_paths = [source["path"] for source in context["sources"]]
    context_hit = any(path in context_paths for path in expected) if expected else None
    archive_leak = (
        not case.get("include_archive")
        and any(is_archive_path(path) for path in context_paths)
    )
    search_read_tokens = estimate_tokens(json.dumps(response, ensure_ascii=False))
    if response["results"]:
        search_read_tokens += int(response["results"][0]["token_estimate"])
    full_file = full_file_baseline(root, case)

    return {
        "id": case["id"],
        "query": case["query"],
        "passed": passed,
        "rank": rank,
        "result_count": response["count"],
        "paths": paths,
        "latency_ms": round(elapsed_ms, 3),
        "context_paths": context_paths,
        "context_latency_ms": round(context_ms, 3),
        "archive_leak": archive_leak,
        "baseline_comparison": {
            "no_memory": {
                "estimated_tokens": 0,
                "retrieval_hit": False if expected else None,
            },
            "full_file": full_file,
            "search_read": {
                "estimated_tokens": search_read_tokens,
                "retrieval_hit": passed if expected else None,
            },
            "context_pack": {
                "budget_tokens": CONTEXT_BUDGET,
                "estimated_tokens": context["estimated_tokens"],
                "budget_compliant": context["estimated_tokens"] <= CONTEXT_BUDGET,
                "retrieval_hit": context_hit,
            },
        },
        "task_outcome": {
            "measured": False,
            "value": None,
            "reason": "This deterministic retrieval benchmark does not run a coding agent.",
        },
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
    positive_context_results = [
        result for result in results
        if result["baseline_comparison"]["context_pack"]["retrieval_hit"] is not None
    ]
    context_hits = sum(
        1 for result in positive_context_results
        if result["baseline_comparison"]["context_pack"]["retrieval_hit"]
    )
    budget_compliant = all(
        result["baseline_comparison"]["context_pack"]["budget_compliant"]
        for result in results
    )
    archive_leaks = sum(1 for result in results if result["archive_leak"])
    baseline_token_means = {
        name: round(statistics.fmean(
            result["baseline_comparison"][name]["estimated_tokens"] for result in results
        ), 1)
        for name in ("no_memory", "full_file", "search_read", "context_pack")
    }
    return {
        "schema_version": 2,
        "synthetic_only": True,
        "task_outcome_measured": False,
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
            "context_retrieval_hits": context_hits,
            "context_retrieval_cases": len(positive_context_results),
            "context_hit_rate_pct": (
                round(100 * context_hits / len(positive_context_results), 1)
                if positive_context_results else None
            ),
            "context_budget_compliance": budget_compliant,
            "archive_leak_count": archive_leaks,
            "mean_estimated_tokens_by_baseline": baseline_token_means,
            "overall_passed": (
                passed == len(results)
                and context_hits == len(positive_context_results)
                and budget_compliant
                and archive_leaks == 0
                and not sync["errors"]
                and doctor["issue_count"] == 0
            ),
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
