#!/usr/bin/env python3
"""Run the deterministic offline Phase 5A multi-agent soak pilot."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"
READ_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}
PROPOSAL_TOOLS = READ_TOOLS | {
    "propose_memory",
    "list_memory_proposals",
    "preview_memory_transition",
}
FORBIDDEN_ACCEPTED_STATE_TOOLS = {
    "accept",
    "reject",
    "defer",
    "supersede",
    "dispute",
    "delete",
    "recover",
    "sync",
    "rebuild",
    "execute_approved_transition",
}

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from mcp import Client

from elm_memory.governance import ProposalLimits, submit_proposal_bundle
from elm_memory.mcp_server import ProposalServerPolicy, create_server
from elm_memory.tokens import estimate_tokens


logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.CRITICAL)


def run_cli(root: Path, *arguments: str) -> dict[str, Any]:
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


def prepare_root(base: Path, name: str) -> Path:
    root = base / name
    shutil.copytree(FIXTURE_ROOT, root)
    run_cli(root, "root-id", "init", "--apply", "--creator", "operator:phase5a-soak")
    rebuilt = run_cli(root, "rebuild")
    if rebuilt["errors"]:
        raise RuntimeError(f"Synthetic fixture rebuild failed for scenario {name}.")
    return root


def deterministic_submission_id(scenario: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"elm-phase5a-soak:{scenario}:{ordinal}".encode("ascii")).digest()
    value = uuid.UUID(bytes=digest[:16], version=4)
    return f"submission_{value}"


def proposal_request(
    scenario: str,
    ordinal: int,
    *,
    submission_id: str | None = None,
    object_value: str | None = None,
) -> dict[str, Any]:
    evidence_digest = hashlib.sha256(
        f"synthetic-evidence:{scenario}".encode("ascii")
    ).hexdigest()
    return {
        "submission_id": submission_id or deterministic_submission_id(scenario, ordinal),
        "project": "orion",
        "subject": "SyntheticSoakSubject",
        "predicate": "uses",
        "object": object_value or f"SyntheticValue{ordinal}",
        "valid_from": "2026-08-26T00:00:00Z",
        "sensitivity": "normal",
        "rationale": "Synthetic offline Phase 5A soak candidate.",
        "source_refs": [],
        "evidence": [{
            "kind": "repository_file",
            "source_uri": f"repo://synthetic/{scenario}.json",
            "content_sha256": evidence_digest,
            "sensitivity": "normal",
        }],
    }


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def result_snapshot(result: Any) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for item in result.content or []:
        if hasattr(item, "text"):
            content.append({"type": "text", "text": item.text})
        else:
            content.append({"type": getattr(item, "type", "unknown")})
    return {
        "is_error": bool(result.is_error),
        "structured_content": result.structured_content,
        "content": content,
    }


def result_text(result: Any) -> str:
    return "\n".join(
        str(item.text) for item in (result.content or []) if hasattr(item, "text")
    ).lower()


def classify_result(result: Any) -> str:
    if not result.is_error:
        return "success"
    message = result_text(result)
    if "not ready for proposal mutation" in message or "projection is not current" in message:
        return "transient_unhealthy"
    if "writer lock is unavailable" in message:
        return "transient_writer_lock"
    if "already used with a different" in message:
        return "submission_conflict"
    if "quota" in message:
        return "quota_refused"
    if "rate limit" in message:
        return "rate_limited"
    if "not enabled" in message:
        return "project_refused"
    return "unexpected_error"


def sample_for_call(arguments: dict[str, Any], result: Any, elapsed_ms: float) -> dict[str, Any]:
    request_text = compact_json(arguments)
    response_text = compact_json(result_snapshot(result))
    return {
        "request_utf8_bytes": len(request_text.encode("utf-8")),
        "response_utf8_bytes": len(response_text.encode("utf-8")),
        "request_estimated_tokens": estimate_tokens(request_text),
        "response_estimated_tokens": estimate_tokens(response_text),
        "latency_ms": elapsed_ms,
    }


async def call_tool_once(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = await client.call_tool(name, arguments)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "result": result,
        "classification": classify_result(result),
        "sample": sample_for_call(arguments, result, elapsed_ms),
    }


async def run_agent_workload(
    server: Any,
    requests: list[dict[str, Any]],
    *,
    max_retries: int,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    async with Client(server, raise_exceptions=True) as client:
        for request in requests:
            samples: list[dict[str, Any]] = []
            final: dict[str, Any] | None = None
            for attempt in range(max_retries + 1):
                current = await call_tool_once(client, "propose_memory", request)
                samples.append(current["sample"])
                final = current
                if current["classification"] not in {
                    "transient_unhealthy",
                    "transient_writer_lock",
                } or attempt >= max_retries:
                    break
                await asyncio.sleep(0.025 * (attempt + 1))
            assert final is not None
            structured = final["result"].structured_content or {}
            outcomes.append({
                "classification": final["classification"],
                "idempotent_replay": structured.get("idempotent_replay"),
                "samples": samples,
            })
    return outcomes


def run_concurrent_workloads(
    root: Path,
    policy: ProposalServerPolicy,
    workloads: list[list[dict[str, Any]]],
    *,
    max_retries: int,
) -> list[dict[str, Any]]:
    barrier = threading.Barrier(len(workloads))

    def worker(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        server = create_server(root, mutation_mode="proposal-only", proposal_policy=policy)
        barrier.wait(timeout=15)
        return asyncio.run(run_agent_workload(server, requests, max_retries=max_retries))

    with ThreadPoolExecutor(max_workers=len(workloads)) as executor:
        futures = [executor.submit(worker, requests) for requests in workloads]
        nested = [future.result() for future in futures]
    return [outcome for agent in nested for outcome in agent]


def distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + 0.5)))
        return ordered[index]

    return {
        "min": round(ordered[0], 3),
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "max": round(ordered[-1], 3),
        "mean": round(statistics.fmean(ordered), 3),
    }


def summarize_metrics(outcomes: list[dict[str, Any]], logical_agents: int) -> dict[str, Any]:
    samples = [sample for outcome in outcomes for sample in outcome["samples"]]
    return {
        "logical_agents": logical_agents,
        "logical_operations": len(outcomes),
        "tool_call_attempts": len(samples),
        "bounded_retries": len(samples) - len(outcomes),
        "final_outcomes": dict(sorted(Counter(item["classification"] for item in outcomes).items())),
        "request_utf8_bytes_total": sum(item["request_utf8_bytes"] for item in samples),
        "response_utf8_bytes_total": sum(item["response_utf8_bytes"] for item in samples),
        "request_estimated_tokens_total": sum(item["request_estimated_tokens"] for item in samples),
        "response_estimated_tokens_total": sum(item["response_estimated_tokens"] for item in samples),
        "latency_ms": distribution([item["latency_ms"] for item in samples]),
    }


def settle_projection(root: Path) -> dict[str, Any]:
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(4):
        try:
            return run_cli(root, "sync")
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if "writer_lock_unavailable" not in (exc.stderr or ""):
                raise
            time.sleep(0.1 * (attempt + 1))
    assert last_error is not None
    raise last_error


def root_observation(root: Path) -> dict[str, Any]:
    settled = settle_projection(root)
    status = run_cli(root, "status")
    history = run_cli(root, "history", "--project", "orion", "--no-sync")
    with closing(sqlite3.connect(root / ".elm" / "index.sqlite")) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    proposal_paths = list(root.rglob("proposal_*.json"))
    evidence_paths = list(root.rglob("evidence_*.json"))
    proposal_records = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_paths]
    evidence_records = [json.loads(path.read_text(encoding="utf-8")) for path in evidence_paths]
    linked_evidence = [
        evidence_id
        for proposal in proposal_records
        for evidence_id in proposal.get("evidence_ids", [])
    ]
    evidence_ids = {record.get("evidence_id") for record in evidence_records}
    return {
        "proposal_files": len(proposal_paths),
        "evidence_files": len(evidence_paths),
        "evidence_links_exact": (
            len(linked_evidence) == len(set(linked_evidence))
            and set(linked_evidence) == evidence_ids
        ),
        "pending_transaction_files": len(
            list((root / "01_inbox" / "elm_transactions").glob("*.json"))
        ),
        "accepted_claims": len(history["claims"]),
        "accepted_events": len(history["events"]),
        "healthy": status["healthy"],
        "projection_current": status["governance_projection_current"],
        "sqlite_quick_check": quick_check,
        "operator_settle_errors": settled["errors"],
        "operator_settle_changed": settled["changed"],
    }


def bundle_integrity(observation: dict[str, Any]) -> bool:
    return bool(
        observation["proposal_files"] == observation["evidence_files"]
        and observation["evidence_links_exact"]
        and observation["pending_transaction_files"] == 0
    )


async def inspect_tool_surfaces(root: Path, policy: ProposalServerPolicy) -> tuple[set[str], set[str]]:
    async with Client(create_server(root), raise_exceptions=True) as client:
        default = await client.list_tools()
    async with Client(
        create_server(root, mutation_mode="proposal-only", proposal_policy=policy),
        raise_exceptions=True,
    ) as client:
        proposal = await client.list_tools()
    return ({tool.name for tool in default.tools}, {tool.name for tool in proposal.tools})


def standard_policy(*, limits: ProposalLimits | None = None, rate: int = 10_000) -> ProposalServerPolicy:
    return ProposalServerPolicy(
        allowed_projects=frozenset({"orion"}),
        limits=limits or ProposalLimits(),
        max_requests_per_minute=rate,
    )


def scenario_surface(root: Path) -> dict[str, Any]:
    default_tools, proposal_tools = asyncio.run(inspect_tool_surfaces(root, standard_policy()))
    checks = {
        "default_surface_exactly_seven_reads": default_tools == READ_TOOLS,
        "proposal_surface_exactly_ten_tools": proposal_tools == PROPOSAL_TOOLS,
        "accepted_state_tools_absent": not (proposal_tools & FORBIDDEN_ACCEPTED_STATE_TOOLS),
    }
    return {"passed": all(checks.values()), "checks": checks}


def scenario_exact_replay(root: Path, agents: int, max_retries: int) -> dict[str, Any]:
    submission_id = deterministic_submission_id("exact-replay", 0)
    value = proposal_request("exact-replay", 0, submission_id=submission_id)
    outcomes = run_concurrent_workloads(
        root,
        standard_policy(),
        [[dict(value)] for _ in range(agents)],
        max_retries=max_retries,
    )
    observation = root_observation(root)
    classifications = Counter(item["classification"] for item in outcomes)
    originals = sum(item["idempotent_replay"] is False for item in outcomes)
    replays = sum(item["idempotent_replay"] is True for item in outcomes)
    checks = {
        "all_agents_converged": classifications == {"success": agents},
        "one_original_rest_idempotent": originals == 1 and replays == agents - 1,
        "one_canonical_proposal": observation["proposal_files"] == 1,
        "no_orphan_evidence_or_transactions": bundle_integrity(observation),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(outcomes, agents),
    }


def scenario_conflicting_replay(root: Path, agents: int, max_retries: int) -> dict[str, Any]:
    submission_id = deterministic_submission_id("conflict", 0)
    first_group = (agents + 1) // 2
    requests = [
        proposal_request(
            "conflict",
            ordinal,
            submission_id=submission_id,
            object_value="CandidateA" if ordinal < first_group else "CandidateB",
        )
        for ordinal in range(agents)
    ]
    outcomes = run_concurrent_workloads(
        root,
        standard_policy(),
        [[request] for request in requests],
        max_retries=max_retries,
    )
    observation = root_observation(root)
    counts = Counter(item["classification"] for item in outcomes)
    success_count = counts["success"]
    conflict_count = counts["submission_conflict"]
    possible_winner_sizes = {first_group, agents - first_group}
    checks = {
        "one_payload_wins_atomically": success_count in possible_winner_sizes,
        "other_payload_is_refused": conflict_count == agents - success_count,
        "one_original_for_winning_payload": (
            sum(item["idempotent_replay"] is False for item in outcomes) == 1
        ),
        "one_canonical_proposal": observation["proposal_files"] == 1,
        "no_orphan_evidence_or_transactions": bundle_integrity(observation),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(outcomes, agents),
    }


def scenario_unique_contention(
    root: Path,
    agents: int,
    operations_per_agent: int,
    max_retries: int,
) -> dict[str, Any]:
    workloads: list[list[dict[str, Any]]] = []
    ordinal = 0
    for _ in range(agents):
        workload: list[dict[str, Any]] = []
        for _ in range(operations_per_agent):
            workload.append(proposal_request("unique", ordinal))
            ordinal += 1
        workloads.append(workload)
    outcomes = run_concurrent_workloads(
        root,
        standard_policy(),
        workloads,
        max_retries=max_retries,
    )
    expected = agents * operations_per_agent
    observation = root_observation(root)
    counts = Counter(item["classification"] for item in outcomes)
    checks = {
        "all_unique_operations_committed": counts == {"success": expected},
        "all_commits_are_original": sum(
            item["idempotent_replay"] is False for item in outcomes
        ) == expected,
        "canonical_count_matches": observation["proposal_files"] == expected,
        "no_orphan_evidence_or_transactions": bundle_integrity(observation),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(outcomes, agents),
    }


def scenario_durable_quota(root: Path, agents: int, max_retries: int) -> dict[str, Any]:
    quota = max(1, agents // 2)
    limits = ProposalLimits(max_pending_per_project=quota)
    outcomes = run_concurrent_workloads(
        root,
        standard_policy(limits=limits),
        [[proposal_request("quota", ordinal)] for ordinal in range(agents)],
        max_retries=max_retries,
    )
    observation = root_observation(root)
    counts = Counter(item["classification"] for item in outcomes)
    checks = {
        "quota_accepts_only_configured_count": counts["success"] == quota,
        "excess_agents_are_refused": counts["quota_refused"] == agents - quota,
        "canonical_count_matches_quota": observation["proposal_files"] == quota,
        "no_orphan_evidence_or_transactions": bundle_integrity(observation),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(outcomes, agents),
    }


def scenario_process_rate_limit(root: Path) -> dict[str, Any]:
    requests = [proposal_request("rate", ordinal) for ordinal in range(3)]
    server = create_server(
        root,
        mutation_mode="proposal-only",
        proposal_policy=standard_policy(rate=2),
    )
    outcomes = asyncio.run(run_agent_workload(server, requests, max_retries=0))
    observation = root_observation(root)
    counts = Counter(item["classification"] for item in outcomes)
    checks = {
        "first_two_requests_commit": counts["success"] == 2,
        "third_request_is_rate_limited": counts["rate_limited"] == 1,
        "only_two_canonical_proposals": observation["proposal_files"] == 2,
        "no_orphan_evidence_or_transactions": bundle_integrity(observation),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(outcomes, 1),
    }


async def stale_projection_exercise(root: Path) -> dict[str, Any]:
    policy = standard_policy()
    server = create_server(root, mutation_mode="proposal-only", proposal_policy=policy)
    value = proposal_request("stale", 0)
    raw = compact_json(value).encode("utf-8")
    canonical = submit_proposal_bundle(
        root,
        request=value,
        request_bytes=len(raw),
        allowed_projects={"orion"},
        limits=policy.limits,
        lock_timeout=5,
        recover_stale=False,
    )
    status_before = run_cli(root, "status")
    samples: list[dict[str, Any]] = []
    async with Client(server, raise_exceptions=True) as client:
        before = await call_tool_once(client, "list_memory_proposals", {"project": "orion"})
        samples.append(before["sample"])
        synced = run_cli(root, "sync")
        after = await call_tool_once(client, "list_memory_proposals", {"project": "orion"})
        samples.append(after["sample"])
    return {
        "canonical": canonical,
        "status_before": status_before,
        "before": before,
        "sync": synced,
        "after": after,
        "samples": samples,
    }


def scenario_stale_projection_repair(root: Path) -> dict[str, Any]:
    result = asyncio.run(stale_projection_exercise(root))
    observation = root_observation(root)
    after_content = result["after"]["result"].structured_content or {}
    checks = {
        "canonical_commit_precedes_projection": result["canonical"]["canonical_committed"] is True,
        "stale_projection_reported_unhealthy": (
            result["status_before"]["healthy"] is False
            and result["status_before"]["governance_projection_current"] is False
        ),
        "governed_read_fails_closed": result["before"]["classification"] == "transient_unhealthy",
        "operator_sync_repairs_projection": result["sync"]["errors"] == [],
        "same_server_resumes_after_repair": (
            result["after"]["classification"] == "success" and after_content.get("count") == 1
        ),
        "one_complete_bundle_no_transactions": (
            observation["proposal_files"] == 1 and bundle_integrity(observation)
        ),
        "accepted_state_unchanged": (
            observation["accepted_claims"] == 0 and observation["accepted_events"] == 0
        ),
        "projection_and_sqlite_healthy": (
            observation["healthy"]
            and observation["projection_current"]
            and observation["sqlite_quick_check"] == "ok"
        ),
    }
    synthetic_outcomes = [{
        "classification": result["before"]["classification"],
        "samples": [result["samples"][0]],
    }, {
        "classification": result["after"]["classification"],
        "samples": [result["samples"][1]],
    }]
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": summarize_metrics(synthetic_outcomes, 1),
    }


def aggregate_metrics(scenarios: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metric_sets = [item["metrics"] for item in scenarios.values() if "metrics" in item]
    outcome_counts: Counter[str] = Counter()
    for metrics in metric_sets:
        outcome_counts.update(metrics["final_outcomes"])
    return {
        "logical_agents_across_scenarios": sum(item["logical_agents"] for item in metric_sets),
        "logical_operations": sum(item["logical_operations"] for item in metric_sets),
        "tool_call_attempts": sum(item["tool_call_attempts"] for item in metric_sets),
        "bounded_retries": sum(item["bounded_retries"] for item in metric_sets),
        "final_outcomes": dict(sorted(outcome_counts.items())),
        "request_utf8_bytes_total": sum(item["request_utf8_bytes_total"] for item in metric_sets),
        "response_utf8_bytes_total": sum(item["response_utf8_bytes_total"] for item in metric_sets),
        "request_estimated_tokens_total": sum(
            item["request_estimated_tokens_total"] for item in metric_sets
        ),
        "response_estimated_tokens_total": sum(
            item["response_estimated_tokens_total"] for item in metric_sets
        ),
    }


def run_soak(
    *,
    agents: int,
    operations_per_agent: int,
    max_retries: int,
    repetitions: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="elm-phase5a-soak-") as temporary:
        base = Path(temporary)
        for repetition in range(1, repetitions + 1):
            run_started = time.perf_counter()
            prefix = f"run-{repetition}"
            exact_root = prepare_root(base, f"{prefix}-exact-replay")
            scenarios = {
                "surface_boundary": scenario_surface(exact_root),
                "concurrent_exact_replay": scenario_exact_replay(
                    exact_root, agents, max_retries
                ),
                "concurrent_conflicting_replay": scenario_conflicting_replay(
                    prepare_root(base, f"{prefix}-conflict"), agents, max_retries
                ),
                "unique_writer_contention": scenario_unique_contention(
                    prepare_root(base, f"{prefix}-unique"),
                    agents,
                    operations_per_agent,
                    max_retries,
                ),
                "durable_project_quota": scenario_durable_quota(
                    prepare_root(base, f"{prefix}-quota"), agents, max_retries
                ),
                "process_rate_limit": scenario_process_rate_limit(
                    prepare_root(base, f"{prefix}-rate")
                ),
                "stale_projection_repair": scenario_stale_projection_repair(
                    prepare_root(base, f"{prefix}-stale")
                ),
            }
            runs.append({
                "repetition": repetition,
                "passed": all(item["passed"] for item in scenarios.values()),
                "scenarios": scenarios,
                "aggregate_metrics": aggregate_metrics(scenarios),
                "elapsed_ms": round((time.perf_counter() - run_started) * 1000, 3),
            })
    combined = {
        f"run_{run['repetition']}:{name}": scenario
        for run in runs
        for name, scenario in run["scenarios"].items()
    }
    return {
        "schema": "elm-phase5a-soak-v1",
        "fixture": "synthetic",
        "configuration": {
            "logical_agents_per_concurrent_scenario": agents,
            "operations_per_agent_in_contention_scenario": operations_per_agent,
            "max_transient_retries": max_retries,
            "repetitions": repetitions,
        },
        "token_accounting": {
            "measurement": "elm-model-neutral estimate of serialized tool arguments and results",
            "estimator": "ceil(unicode_code_points / 4)",
            "provider_billed_tokens_available": False,
            "provider_billed_tokens": None,
            "excludes": [
                "agent system and developer prompts",
                "model reasoning and hidden context",
                "provider-specific tokenization",
                "MCP transport framing",
                "provider cache and billing adjustments",
            ],
        },
        "passed": all(run["passed"] for run in runs),
        "runs": runs,
        "aggregate_metrics": aggregate_metrics(combined),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=6)
    parser.add_argument("--operations-per-agent", type=int, default=2)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--assert-pass", action="store_true")
    arguments = parser.parse_args()
    if not 2 <= arguments.agents <= 16:
        parser.error("--agents must be between 2 and 16")
    if not 1 <= arguments.operations_per_agent <= 20:
        parser.error("--operations-per-agent must be between 1 and 20")
    if not 0 <= arguments.max_retries <= 10:
        parser.error("--max-retries must be between 0 and 10")
    if not 1 <= arguments.repetitions <= 20:
        parser.error("--repetitions must be between 1 and 20")
    result = run_soak(
        agents=arguments.agents,
        operations_per_agent=arguments.operations_per_agent,
        max_retries=arguments.max_retries,
        repetitions=arguments.repetitions,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
