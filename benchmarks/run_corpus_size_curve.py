#!/usr/bin/env python3
"""Run a deterministic, counterbalanced ELM corpus-size curve."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import sys
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PILOT_PATH = Path(__file__).with_name("run_heterogeneous_pilot.py")
DEFAULT_TARGET_TOKENS = (2_000, 8_000, 32_000, 128_000)
CURVE_CONDITIONS = ("elm", "full_corpus")
CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
DISTRACTOR_DIRECTORY = Path("10_shared") / "corpus_curve"
DISTRACTOR_PAYLOAD_CHARS = 3_600
ANTIGRAVITY_TIMEOUT_GRACE_SECONDS = 10.0
FILLER_PHRASE = (
    "amber cobalt cedar quartz meadow lantern harbor velvet compass mosaic "
    "ripple summit copper willow archive pebble canvas orchard silver "
)


def load_pilot_module():
    specification = importlib.util.spec_from_file_location(
        "elm_heterogeneous_pilot", PILOT_PATH
    )
    if not specification or not specification.loader:
        raise RuntimeError("Could not load the heterogeneous pilot module")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


PILOT = load_pilot_module()


def distractor_document(index: int) -> str:
    prefix = FILLER_PHRASE + f"item{index:04d} "
    payload = (prefix * (DISTRACTOR_PAYLOAD_CHARS // len(prefix) + 2))[
        :DISTRACTOR_PAYLOAD_CHARS
    ]
    return (
        f"Title: Synthetic Curve Distractor {index:04d}\n"
        "Scope: Supplies deterministic irrelevant text for a synthetic retrieval benchmark.\n"
        "Tags: synthetic, benchmark, distractor, corpus-curve\n"
        "Related files: ../../00_registry/ROOT_INDEX.md\n"
        "Last updated: 2026-08-27\n"
        "Status: active\n"
        f"Summary: Deterministic distractor item {index:04d}; it contains no benchmark oracle.\n\n"
        f"# Synthetic item {index:04d}\n\n"
        f"{payload}\n"
    )


def normalized_lexical_vocabulary(text: str) -> set[str]:
    import re

    vocabulary: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(token) < 5:
            continue
        vocabulary.add(token)
        if token.endswith("s") and len(token) > 5:
            vocabulary.add(token[:-1])
    return vocabulary


def case_query_vocabulary() -> set[str]:
    return {
        token
        for case in PILOT.load_cases()
        for token in normalized_lexical_vocabulary(
            f"{case['question']} {case['lookup_query']}"
        )
    }


def markdown_manifest(hashes: dict[str, str]) -> str:
    serialized = json.dumps(hashes, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def prepare_scaled_root(target: Path, target_tokens: int) -> dict[str, Any]:
    if target.exists():
        raise FileExistsError(f"Curve target already exists: {target}")
    shutil.copytree(PILOT.FIXTURE_ROOT, target)
    distractor_root = target / DISTRACTOR_DIRECTORY
    distractor_root.mkdir(parents=True)
    distractor_count = 0
    corpus = PILOT.active_corpus(target)
    while PILOT.estimate_tokens(corpus) < target_tokens:
        distractor_count += 1
        document = distractor_root / f"DISTRACTOR_{distractor_count:04d}.md"
        document.write_text(distractor_document(distractor_count), encoding="utf-8")
        corpus = PILOT.active_corpus(target)
    rebuilt = PILOT.run_cli(target, "rebuild")
    if rebuilt.get("errors"):
        raise RuntimeError("Scaled synthetic fixture rebuild was not clean")
    hashes = PILOT.canonical_markdown_hashes(target)
    return {
        "target_corpus_estimated_tokens": target_tokens,
        "actual_corpus_estimated_tokens": PILOT.estimate_tokens(corpus),
        "active_markdown_documents": corpus.count("FILE: "),
        "distractor_documents": distractor_count,
        "corpus_utf8_bytes": len(corpus.encode("utf-8")),
        "manifest_sha256": markdown_manifest(hashes),
        "markdown_hashes": hashes,
        "rebuild_errors": rebuilt.get("errors", []),
        "corpus": corpus,
    }


def shared_documents_are_nested(
    smaller_hashes: dict[str, str], larger_hashes: dict[str, str]
) -> bool:
    return all(larger_hashes.get(path) == digest for path, digest in smaller_hashes.items())


def ordered_targets(targets: tuple[int, ...], repeat: int) -> list[int]:
    rotation = (repeat // 2) % len(targets)
    rotated = list(targets[rotation:] + targets[:rotation])
    return list(reversed(rotated)) if repeat % 2 else rotated


def ordered_cases(case_ids: tuple[str, ...], repeat: int) -> list[str]:
    rotation = repeat % len(case_ids)
    return list(case_ids[rotation:] + case_ids[:rotation])


def build_schedule(
    routes: tuple[str, ...],
    case_ids: tuple[str, ...],
    targets: tuple[int, ...],
    repeats: int,
) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    for route in routes:
        sequence = 0
        for repeat in range(repeats):
            for target in ordered_targets(targets, repeat):
                target_index = targets.index(target)
                for case_id in ordered_cases(case_ids, repeat):
                    case_index = case_ids.index(case_id)
                    conditions = (
                        CURVE_CONDITIONS
                        if (repeat + target_index + case_index) % 2 == 0
                        else tuple(reversed(CURVE_CONDITIONS))
                    )
                    pair_id = f"{route}:{target}:{case_id}:r{repeat + 1}"
                    for condition_position, condition in enumerate(conditions, start=1):
                        sequence += 1
                        schedule.append(
                            {
                                "sequence": sequence,
                                "route": route,
                                "target_corpus_estimated_tokens": target,
                                "case_id": case_id,
                                "repeat": repeat + 1,
                                "pair_id": pair_id,
                                "condition": condition,
                                "condition_position": condition_position,
                            }
                        )
    return schedule


def schedule_is_counterbalanced(schedule: list[dict[str, Any]]) -> bool:
    first_counts: dict[tuple[str, int, str], Counter[str]] = {}
    pair_counts: Counter[str] = Counter()
    for cell in schedule:
        pair_counts[cell["pair_id"]] += 1
        if cell["condition_position"] == 1:
            key = (
                cell["route"],
                cell["target_corpus_estimated_tokens"],
                cell["case_id"],
            )
            first_counts.setdefault(key, Counter())[cell["condition"]] += 1
    return (
        bool(schedule)
        and all(count == len(CURVE_CONDITIONS) for count in pair_counts.values())
        and all(
            counts["elm"] == counts["full_corpus"] and counts["elm"] > 0
            for counts in first_counts.values()
        )
    )


def one_sided_sign_test_p(below: int, above: int) -> float | None:
    exact = one_sided_sign_test_fraction(below, above)
    return exact[0] / exact[1] if exact is not None else None


def one_sided_sign_test_fraction(below: int, above: int) -> tuple[int, int] | None:
    trials = below + above
    if trials == 0:
        return None
    numerator = sum(math.comb(trials, successes) for successes in range(below, trials + 1))
    return numerator, 2**trials


def expected_pair_identities(
    schedule: list[dict[str, Any]],
) -> dict[tuple[str, int], set[tuple[str, int]]]:
    expected: dict[tuple[str, int], set[tuple[str, int]]] = {}
    for cell in schedule:
        expected.setdefault(
            (cell["route"], cell["target_corpus_estimated_tokens"]), set()
        ).add((cell["case_id"], cell["repeat"]))
    return expected


def build_pair_comparisons(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int, str, int], dict[str, dict[str, Any]]] = {}
    for run in runs:
        key = (
            run["route"],
            run.get("adapter_mode", "direct-mcp"),
            run["target_corpus_estimated_tokens"],
            run["case_id"],
            run["repeat"],
        )
        grouped.setdefault(key, {})[run["condition"]] = run
    comparisons: list[dict[str, Any]] = []
    for (route, adapter_mode, target, case_id, repeat), conditions in sorted(grouped.items()):
        elm = conditions.get("elm")
        full = conditions.get("full_corpus")
        ordered = sorted(conditions.values(), key=lambda run: run["sequence"])
        item: dict[str, Any] = {
            "route": route,
            "adapter_mode": adapter_mode,
            "target_corpus_estimated_tokens": target,
            "actual_corpus_estimated_tokens": (
                ordered[0].get("actual_corpus_estimated_tokens") if ordered else None
            ),
            "case_id": case_id,
            "repeat": repeat,
            "condition_order": [run["condition"] for run in ordered],
            "comparable": False,
            "metric_basis": None,
            "elm_value": None,
            "full_corpus_value": None,
            "elm_minus_full_corpus": None,
            "elm_to_full_corpus_ratio": None,
        }
        if elm and full and elm.get("passed") and full.get("passed"):
            elm_basis, elm_value = PILOT.comparison_value(elm)
            full_basis, full_value = PILOT.comparison_value(full)
            if elm_basis and elm_basis == full_basis and elm_value is not None and full_value:
                item.update(
                    {
                        "comparable": True,
                        "metric_basis": elm_basis,
                        "elm_value": int(elm_value),
                        "full_corpus_value": int(full_value),
                        "elm_minus_full_corpus": int(elm_value - full_value),
                        "elm_to_full_corpus_ratio": round(elm_value / full_value, 6),
                    }
                )
        comparisons.append(item)
    return comparisons


def aggregate_curve(
    comparisons: list[dict[str, Any]],
    *,
    min_pairs: int,
    alpha: float,
    expected_pairs: dict[tuple[str, int], set[tuple[str, int]]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for item in comparisons:
        grouped.setdefault(
            (
                item["route"],
                item.get("adapter_mode", "direct-mcp"),
                item["target_corpus_estimated_tokens"],
            ),
            [],
        ).append(item)
    aggregates: list[dict[str, Any]] = []
    for (route, adapter_mode, target), items in sorted(grouped.items()):
        comparable = [item for item in items if item["comparable"]]
        ratios = [
            int(item["elm_value"]) / int(item["full_corpus_value"])
            for item in comparable
        ]
        below = sum(int(item["elm_value"]) < int(item["full_corpus_value"]) for item in comparable)
        above = sum(int(item["elm_value"]) > int(item["full_corpus_value"]) for item in comparable)
        ties = len(ratios) - below - above
        exact_sign = one_sided_sign_test_fraction(below, above)
        p_value = exact_sign[0] / exact_sign[1] if exact_sign is not None else None
        observed_identities = {(item["case_id"], item["repeat"]) for item in items}
        expected_identities = (
            expected_pairs.get((route, target), set())
            if expected_pairs is not None
            else observed_identities
        )
        exact_population = observed_identities == expected_identities and bool(expected_identities)
        all_comparable = (
            exact_population
            and len(items) == len(expected_identities)
            and len(comparable) == len(expected_identities)
        )
        claim_qualified = (
            all_comparable
            and below + above >= min_pairs
            and bool(ratios)
            and statistics.median(ratios) < 1.0
            and p_value is not None
            and p_value <= alpha
        )
        aggregates.append(
            {
                "route": route,
                "adapter_mode": adapter_mode,
                "target_corpus_estimated_tokens": target,
                "actual_corpus_estimated_tokens": (
                    comparable[0].get("actual_corpus_estimated_tokens")
                    if comparable
                    else None
                ),
                "planned_pair_count": len(expected_identities),
                "observed_pair_count": len(items),
                "exact_planned_pair_population": exact_population,
                "comparable_pair_count": len(comparable),
                "all_pairs_comparable": all_comparable,
                "median_elm_to_full_corpus_ratio": (
                    round(statistics.median(ratios), 6) if ratios else None
                ),
                "minimum_ratio": round(min(ratios), 6) if ratios else None,
                "maximum_ratio": round(max(ratios), 6) if ratios else None,
                "elm_below_full_corpus_count": below,
                "elm_above_full_corpus_count": above,
                "tie_count": ties,
                "one_sided_exact_sign_numerator": exact_sign[0] if exact_sign else None,
                "one_sided_exact_sign_denominator": exact_sign[1] if exact_sign else None,
                "one_sided_exact_sign_p": p_value,
                "claim_qualified": claim_qualified,
            }
        )
    return aggregates


def build_crossover_summary(
    aggregates: list[dict[str, Any]],
    *,
    min_consecutive_sizes: int,
    global_integrity_passed: bool = True,
    claim_mode_enabled: bool = True,
) -> list[dict[str, Any]]:
    route_adapters = sorted(
        {
            (item["route"], item.get("adapter_mode", "direct-mcp"))
            for item in aggregates
        }
    )
    summaries: list[dict[str, Any]] = []
    for route, adapter_mode in route_adapters:
        items = sorted(
            (
                item
                for item in aggregates
                if item["route"] == route
                and item.get("adapter_mode", "direct-mcp") == adapter_mode
            ),
            key=lambda item: item["target_corpus_estimated_tokens"],
        )
        crossover = None
        for index, item in enumerate(items):
            suffix = items[index:]
            if (
                claim_mode_enabled
                and global_integrity_passed
                and len(suffix) >= min_consecutive_sizes
                and all(candidate["claim_qualified"] for candidate in suffix)
            ):
                crossover = item["target_corpus_estimated_tokens"]
                break
        summaries.append(
            {
                "route": route,
                "adapter_mode": adapter_mode,
                "benchmark_qualified_crossover": crossover is not None,
                "crossover_target_corpus_estimated_tokens": crossover,
                "interpretation": (
                    "bounded_benchmark_evidence_only"
                    if crossover is not None
                    else (
                        "claim_mode_not_enabled"
                        if not claim_mode_enabled
                        else (
                            "no_qualified_crossover_observed"
                            if global_integrity_passed
                            else "global_integrity_gate_failed"
                        )
                    )
                ),
            }
        )
    return summaries


def generator_validation() -> dict[str, bool]:
    oracle_values = {
        case[field].casefold()
        for case in PILOT.load_cases()
        for field in ("expected_answer", "expected_source_path", "expected_heading")
    }
    serialized_distractors = "\n".join(
        distractor_document(index) for index in range(1, 4)
    ).casefold()
    distractor_vocabulary = normalized_lexical_vocabulary(serialized_distractors)
    with PILOT.disposable_directory("elm-corpus-size-static-") as scratch:
        small = prepare_scaled_root(scratch / "small", 2_000)
        large = prepare_scaled_root(scratch / "large", 5_000)
        repeat = prepare_scaled_root(scratch / "repeat", 2_000)
    return {
        "generated_corpora_reach_targets": (
            small["actual_corpus_estimated_tokens"] >= 2_000
            and large["actual_corpus_estimated_tokens"] >= 5_000
        ),
        "generated_corpora_are_deterministic": (
            small["manifest_sha256"] == repeat["manifest_sha256"]
        ),
        "generated_corpora_are_nested": shared_documents_are_nested(
            small["markdown_hashes"], large["markdown_hashes"]
        ),
        "generated_distractors_are_oracle_free": not any(
            value in serialized_distractors for value in oracle_values
        ),
        "generated_distractors_avoid_query_vocabulary": not (
            distractor_vocabulary & case_query_vocabulary()
        ),
        "generated_fixture_rebuilds_are_clean": (
            not small["rebuild_errors"]
            and not large["rebuild_errors"]
            and not repeat["rebuild_errors"]
        ),
    }


def validate_static_contract(
    *,
    routes: tuple[str, ...],
    case_ids: tuple[str, ...],
    targets: tuple[int, ...],
    repeats: int,
) -> dict[str, bool]:
    schedule = build_schedule(routes, case_ids, targets, repeats)
    pilot_checks = {
        f"heterogeneous_{name}": passed
        for name, passed in PILOT.validate_static_contract().items()
    }
    return {
        **pilot_checks,
        "targets_are_strictly_increasing": (
            list(targets) == sorted(set(targets)) and bool(targets)
        ),
        "repeats_support_pairwise_counterbalancing": repeats >= 2 and repeats % 2 == 0,
        "schedule_sequences_are_unique": len(
            {(cell["route"], cell["sequence"]) for cell in schedule}
        )
        == len(schedule),
        "schedule_is_pairwise_counterbalanced": schedule_is_counterbalanced(schedule),
        **generator_validation(),
    }


def route_models(arguments: argparse.Namespace) -> tuple[dict[str, str | None], list[str]]:
    agy = shutil.which("agy")
    requests = {
        "gemini-antigravity": arguments.gemini_model,
        "claude-antigravity": arguments.antigravity_claude_model,
    }
    discovery_required = any(
        route in arguments.routes and requests[route] is None for route in requests
    )
    models = PILOT.antigravity_models(agy) if agy and discovery_required else []
    return (
        {
            "codex": arguments.openai_model,
            "gemini-antigravity": (
                arguments.gemini_model
                or PILOT.select_model(models, None, ("gemini-",))
            ),
            "claude-antigravity": (
                arguments.antigravity_claude_model
                or PILOT.select_model(
                    models, None, ("claude-sonnet-", "claude-opus-")
                )
            ),
            "claude-code": arguments.claude_code_model,
        },
        models,
    )


def configured_antigravity_adapter(arguments: argparse.Namespace) -> str:
    return getattr(
        arguments,
        "antigravity_adapter",
        PILOT.DEFAULT_ANTIGRAVITY_ADAPTER,
    )


def configured_claim_scope(arguments: argparse.Namespace) -> str:
    if (
        any(route.endswith("antigravity") for route in arguments.routes)
        and configured_antigravity_adapter(arguments) == "host-brokered-context"
    ):
        return "brokered_context_prompt_efficiency_bounded_panel"
    return "bounded_benchmark_panel_only"


def claim_capable_configuration_is_valid(
    arguments: argparse.Namespace, models: dict[str, str | None]
) -> bool:
    if not arguments.claim_capable:
        return True
    if (
        configured_claim_scope(arguments)
        == "brokered_context_prompt_efficiency_bounded_panel"
        and not getattr(arguments, "acknowledge_brokered_claim_scope", False)
    ):
        return False
    requested = {
        "codex": arguments.openai_model,
        "gemini-antigravity": arguments.gemini_model,
        "claude-antigravity": arguments.antigravity_claude_model,
        "claude-code": arguments.claude_code_model,
    }
    return (
        len(arguments.case_ids) * arguments.repeats
        >= arguments.min_pairs_for_claim
        and len(arguments.target_corpus_tokens) >= arguments.min_consecutive_sizes
        and all(requested[route] and models[route] == requested[route] for route in arguments.routes)
        and (
            "codex" not in arguments.routes
            or arguments.openai_reasoning_effort is not None
        )
    )


def remaining_run_timeout(
    *, started: float, now: float, per_run_timeout: float, max_total_seconds: float
) -> float | None:
    remaining = max_total_seconds - (now - started)
    if remaining <= 0:
        return None
    return min(per_run_timeout, remaining)


def provider_timeout_within_budget(route: str, bounded_timeout: float) -> float | None:
    if route.endswith("antigravity"):
        if bounded_timeout <= ANTIGRAVITY_TIMEOUT_GRACE_SECONDS:
            return None
        return bounded_timeout - ANTIGRAVITY_TIMEOUT_GRACE_SECONDS
    return bounded_timeout


def execute_provider_cell(
    *,
    cell: dict[str, Any],
    workspace: Path,
    root: Path,
    runtime: Path,
    prompt: str,
    model: str | None,
    arguments: argparse.Namespace,
    timeout: float,
) -> tuple[str, dict[str, Any] | None, dict[str, Any], dict[str, Any], float]:
    route = cell["route"]
    if route == "codex":
        return PILOT.run_codex(
            workspace=workspace,
            root=root,
            runtime=runtime,
            prompt=prompt,
            condition=cell["condition"],
            model=model,
            reasoning_effort=arguments.openai_reasoning_effort,
            timeout=timeout,
        )
    if route.endswith("antigravity"):
        return PILOT.run_antigravity(
            route=route,
            workspace=workspace,
            root=root,
            runtime=runtime,
            prompt=prompt,
            condition=cell["condition"],
            model=model,
            timeout=timeout,
            adapter_mode=PILOT.route_adapter_mode(
                route, configured_antigravity_adapter(arguments)
            ),
        )
    return PILOT.run_claude_code(
        workspace=workspace,
        root=root,
        runtime=runtime,
        prompt=prompt,
        condition=cell["condition"],
        model=model,
        timeout=timeout,
        max_cost_usd=arguments.max_cost_usd,
    )


def finalize_run(
    *,
    run: dict[str, Any],
    status: str,
    response: dict[str, Any] | None,
    usage: dict[str, Any],
    audit: dict[str, Any],
    elapsed: float,
    case: dict[str, str],
    section_key: str,
    broker_audit: dict[str, Any] | None = None,
    retrieval_elapsed: float = 0.0,
    prompt: str | None = None,
) -> None:
    broker_audit = broker_audit or PILOT.empty_broker_audit()
    adapter_mode = run.get("adapter_mode", "direct-mcp")
    checks = PILOT.evaluate_response(
        response,
        case=case,
        condition=run["condition"],
        section_key=section_key,
        adapter_mode=adapter_mode,
    )
    checks["provider_usage_complete"] = PILOT.usage_complete_for_route(
        run["route"], usage
    )
    checks["tool_provenance_verified"] = PILOT.tool_audit_passes(
        run["condition"], audit, adapter_mode
    )
    checks["broker_provenance_verified"] = PILOT.broker_audit_passes(
        run["condition"],
        adapter_mode,
        broker_audit,
        prompt=prompt,
        case=case,
    )
    if status == "completed":
        quality_keys = {
            "schema_response_present",
            "answer_correct",
            "evidence_correct",
        }
        if not all(checks[key] for key in quality_keys):
            final_status = "failed_quality"
        elif not checks["provider_usage_complete"]:
            final_status = "telemetry_unavailable"
        elif not (
            checks["tool_provenance_verified"]
            and checks["broker_provenance_verified"]
        ):
            final_status = "provenance_unverified"
        else:
            final_status = "passed"
    else:
        final_status = status
    run.update(
        {
            "status": final_status,
            "passed": final_status == "passed",
            "checks": checks,
            "usage": usage,
            "tool_provenance": audit,
            "broker_provenance": broker_audit,
            "observed_response": PILOT.reportable_observed_response(response, checks),
            "retrieval_elapsed_ms": round(retrieval_elapsed, 3),
            "provider_elapsed_ms": round(elapsed, 3),
            "elapsed_ms": round(retrieval_elapsed + elapsed, 3),
        }
    )


def build_preflight_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in PILOT.load_cases()}
    selected_cases = [cases_by_id[case_id] for case_id in arguments.case_ids]
    schedule = build_schedule(
        arguments.routes,
        arguments.case_ids,
        arguments.target_corpus_tokens,
        arguments.repeats,
    )
    models, _ = route_models(arguments)
    checks = validate_static_contract(
        routes=arguments.routes,
        case_ids=arguments.case_ids,
        targets=arguments.target_corpus_tokens,
        repeats=arguments.repeats,
    )
    checks["claim_capable_configuration_is_valid"] = (
        claim_capable_configuration_is_valid(arguments, models)
    )
    sizes: list[dict[str, Any]] = []
    prompt_estimates: list[dict[str, Any]] = []
    prompt_cap_passed = True
    with PILOT.disposable_directory("elm-corpus-size-plan-") as scratch:
        for target in arguments.target_corpus_tokens:
            record = prepare_scaled_root(scratch / "memory" / str(target), target)
            sizes.append(
                {
                    key: record[key]
                    for key in (
                        "target_corpus_estimated_tokens",
                        "actual_corpus_estimated_tokens",
                        "active_markdown_documents",
                        "distractor_documents",
                        "corpus_utf8_bytes",
                        "manifest_sha256",
                        "rebuild_errors",
                    )
                }
            )
            for route in arguments.routes:
                for condition in CURVE_CONDITIONS:
                    estimates = []
                    for case in selected_cases:
                        prompt, _, _ = PILOT.prepare_evidence_prompt(
                            route=route,
                            root=scratch / "memory" / str(target),
                            case=case,
                            condition=condition,
                            corpus=record["corpus"],
                            antigravity_adapter=configured_antigravity_adapter(arguments),
                        )
                        estimates.append(PILOT.estimate_tokens(prompt))
                    maximum = max(estimates)
                    prompt_cap_passed = (
                        prompt_cap_passed
                        and maximum <= arguments.max_prompt_estimated_tokens
                    )
                    prompt_estimates.append(
                        {
                            "route": route,
                            "adapter_mode": PILOT.route_adapter_mode(
                                route, configured_antigravity_adapter(arguments)
                            ),
                            "target_corpus_estimated_tokens": target,
                            "condition": condition,
                            "minimum_initial_prompt_estimated_tokens": min(estimates),
                            "maximum_initial_prompt_estimated_tokens": maximum,
                        }
                    )
    checks["preflight_prompt_cap_passed"] = prompt_cap_passed
    return {
        "schema": "elm-corpus-size-curve-plan-v1",
        "passed": all(checks.values()),
        "configuration": {
            "routes": list(arguments.routes),
            "case_ids": list(arguments.case_ids),
            "conditions": list(CURVE_CONDITIONS),
            "target_corpus_estimated_tokens": list(arguments.target_corpus_tokens),
            "repeats": arguments.repeats,
            "planned_provider_runs": len(schedule),
            "planned_pairs": len(schedule) // len(CURVE_CONDITIONS),
            "claim_capable_mode": arguments.claim_capable,
            "claim_scope": configured_claim_scope(arguments),
            "statistical_unit": "case_repeat_pair",
            "population_generalization_supported": False,
            "codex_model_requested": arguments.openai_model,
            "codex_reasoning_effort_requested": arguments.openai_reasoning_effort,
            "antigravity_adapter": configured_antigravity_adapter(arguments),
            "max_prompt_estimated_tokens": arguments.max_prompt_estimated_tokens,
            "max_total_seconds": arguments.max_total_seconds,
            "max_runs": arguments.max_runs,
        },
        "checks": checks,
        "sizes": sizes,
        "prompt_estimates": prompt_estimates,
        "privacy": {
            "provider_calls_executed": False,
            "personal_elm_opened": False,
            "credentials_read_by_harness": False,
        },
    }


def run_curve(arguments: argparse.Namespace) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in PILOT.load_cases()}
    selected_cases = {case_id: cases_by_id[case_id] for case_id in arguments.case_ids}
    schedule = build_schedule(
        arguments.routes,
        arguments.case_ids,
        arguments.target_corpus_tokens,
        arguments.repeats,
    )
    models, _ = route_models(arguments)
    capabilities = {
        "codex": {
            "version": PILOT.command_version("codex"),
            "model_requested": models["codex"],
            "reasoning_effort_requested": arguments.openai_reasoning_effort,
        },
        "gemini-antigravity": {
            "version": PILOT.command_version("agy"),
            "model_requested": models["gemini-antigravity"],
        },
        "claude-antigravity": {
            "version": PILOT.command_version("agy"),
            "model_requested": models["claude-antigravity"],
        },
        "claude-code": {
            "version": PILOT.command_version("claude"),
            "model_requested": models["claude-code"],
        },
    }
    static_checks = validate_static_contract(
        routes=arguments.routes,
        case_ids=arguments.case_ids,
        targets=arguments.target_corpus_tokens,
        repeats=arguments.repeats,
    )
    started = time.perf_counter()
    runs: list[dict[str, Any]] = []
    size_records: dict[int, dict[str, Any]] = {}
    preflight_prompt_cap_passed = True
    aborted_reason = None
    with PILOT.disposable_directory("elm-corpus-size-curve-") as scratch:
        runtime = PILOT.prepare_runtime(scratch)
        for target in arguments.target_corpus_tokens:
            record = prepare_scaled_root(scratch / "memory" / str(target), target)
            record["section_keys"] = {
                case_id: PILOT.expected_section_key(
                    scratch / "memory" / str(target), selected_cases[case_id]
                )
                for case_id in arguments.case_ids
            }
            record["before_hashes"] = record["markdown_hashes"]
            size_records[target] = record
        for cell in schedule:
            record = size_records[cell["target_corpus_estimated_tokens"]]
            prompt, _, _ = PILOT.prepare_evidence_prompt(
                route=cell["route"],
                root=scratch / "memory" / str(cell["target_corpus_estimated_tokens"]),
                case=selected_cases[cell["case_id"]],
                condition=cell["condition"],
                corpus=record["corpus"],
                antigravity_adapter=configured_antigravity_adapter(arguments),
            )
            if PILOT.estimate_tokens(prompt) > arguments.max_prompt_estimated_tokens:
                preflight_prompt_cap_passed = False
                aborted_reason = "prompt_limit_exceeded"
                break
        if preflight_prompt_cap_passed:
            for cell in schedule:
                bounded_timeout = remaining_run_timeout(
                    started=started,
                    now=time.perf_counter(),
                    per_run_timeout=arguments.timeout,
                    max_total_seconds=arguments.max_total_seconds,
                )
                if bounded_timeout is None:
                    aborted_reason = "total_timeout"
                    break
                provider_timeout = provider_timeout_within_budget(
                    cell["route"], bounded_timeout
                )
                if provider_timeout is None:
                    aborted_reason = "total_timeout"
                    break
                target = cell["target_corpus_estimated_tokens"]
                record = size_records[target]
                case = selected_cases[cell["case_id"]]
                prompt, broker_audit, retrieval_elapsed = PILOT.prepare_evidence_prompt(
                    route=cell["route"],
                    root=scratch / "memory" / str(target),
                    case=case,
                    condition=cell["condition"],
                    corpus=record["corpus"],
                    antigravity_adapter=configured_antigravity_adapter(arguments),
                )
                workspace = (
                    scratch
                    / "workspaces"
                    / cell["route"]
                    / f"size-{target}"
                    / cell["case_id"]
                    / f"repeat-{cell['repeat']}"
                    / f"{cell['condition_position']}-{cell['condition']}"
                )
                workspace.mkdir(parents=True)
                run = PILOT.base_run(
                    cell["route"],
                    cell["condition"],
                    cell["case_id"],
                    models[cell["route"]],
                    adapter_mode=PILOT.route_adapter_mode(
                        cell["route"], configured_antigravity_adapter(arguments)
                    ),
                )
                run.update(cell)
                run.update(
                    {
                        "actual_corpus_estimated_tokens": record[
                            "actual_corpus_estimated_tokens"
                        ],
                        "active_markdown_documents": record["active_markdown_documents"],
                        "reasoning_effort_requested": (
                            arguments.openai_reasoning_effort
                            if cell["route"] == "codex"
                            else None
                        ),
                        "initial_prompt_utf8_bytes": len(prompt.encode("utf-8")),
                        "initial_prompt_estimated_tokens": PILOT.estimate_tokens(prompt),
                    }
                )
                status, response, usage, audit, elapsed = execute_provider_cell(
                    cell=cell,
                    workspace=workspace,
                    root=scratch / "memory" / str(target),
                    runtime=runtime,
                    prompt=prompt,
                    model=models[cell["route"]],
                    arguments=arguments,
                    timeout=provider_timeout,
                )
                finalize_run(
                    run=run,
                    status=status,
                    response=response,
                    usage=usage,
                    audit=audit,
                    elapsed=elapsed,
                    case=case,
                    section_key=record["section_keys"][cell["case_id"]],
                    broker_audit=broker_audit,
                    retrieval_elapsed=retrieval_elapsed,
                    prompt=prompt,
                )
                runs.append(run)
                if arguments.fail_fast and not run["passed"]:
                    aborted_reason = "run_failed"
                    break
                if time.perf_counter() - started > arguments.max_total_seconds:
                    aborted_reason = "total_timeout"
                    break
        unchanged = True
        for target, record in size_records.items():
            after_hashes = PILOT.canonical_markdown_hashes(
                scratch / "memory" / str(target)
            )
            unchanged = unchanged and record["before_hashes"] == after_hashes
    total_elapsed_seconds = time.perf_counter() - started
    comparisons = build_pair_comparisons(runs)
    planned_pair_identities = expected_pair_identities(schedule)
    aggregates = aggregate_curve(
        comparisons,
        min_pairs=arguments.min_pairs_for_claim,
        alpha=arguments.claim_alpha,
        expected_pairs=planned_pair_identities,
    )
    expected_pair_count = len(schedule) // len(CURVE_CONDITIONS)
    checks = {
        **static_checks,
        "claim_capable_configuration_is_valid": (
            claim_capable_configuration_is_valid(arguments, models)
        ),
        "preflight_prompt_cap_passed": preflight_prompt_cap_passed,
        "all_scheduled_runs_executed": len(runs) == len(schedule),
        "all_selected_runs_passed": len(runs) == len(schedule)
        and all(run["passed"] for run in runs),
        "all_selected_runs_have_complete_usage": len(runs) == len(schedule)
        and all(run["checks"]["provider_usage_complete"] for run in runs),
        "all_selected_runs_have_verified_tool_provenance": len(runs) == len(schedule)
        and all(run["checks"]["tool_provenance_verified"] for run in runs),
        "all_selected_runs_have_verified_broker_provenance": len(runs) == len(schedule)
        and all(run["checks"]["broker_provenance_verified"] for run in runs),
        "all_required_pairs_are_comparable": (
            len(comparisons) == expected_pair_count
            and all(item["comparable"] for item in comparisons)
            and all(item["all_pairs_comparable"] for item in aggregates)
        ),
        "canonical_markdown_unchanged": unchanged,
        "total_time_cap_respected": (
            aborted_reason != "total_timeout"
            and total_elapsed_seconds <= arguments.max_total_seconds
        ),
    }
    crossover = build_crossover_summary(
        aggregates,
        min_consecutive_sizes=arguments.min_consecutive_sizes,
        global_integrity_passed=all(checks.values()),
        claim_mode_enabled=arguments.claim_capable,
    )
    public_sizes = [
        {
            key: record[key]
            for key in (
                "target_corpus_estimated_tokens",
                "actual_corpus_estimated_tokens",
                "active_markdown_documents",
                "distractor_documents",
                "corpus_utf8_bytes",
                "manifest_sha256",
                "rebuild_errors",
            )
        }
        for record in (size_records[target] for target in arguments.target_corpus_tokens)
    ]
    return {
        "schema": "elm-corpus-size-curve-v1",
        "fixture": "synthetic-orion-with-deterministic-distractors",
        "configuration": {
            "routes": list(arguments.routes),
            "case_ids": list(arguments.case_ids),
            "conditions": list(CURVE_CONDITIONS),
            "target_corpus_estimated_tokens": list(arguments.target_corpus_tokens),
            "repeats": arguments.repeats,
            "claim_capable_mode": arguments.claim_capable,
            "claim_scope": configured_claim_scope(arguments),
            "statistical_unit": "case_repeat_pair",
            "population_generalization_supported": False,
            "fail_fast": arguments.fail_fast,
            "codex_reasoning_effort_requested": arguments.openai_reasoning_effort,
            "antigravity_adapter": configured_antigravity_adapter(arguments),
            "timeout_seconds_per_run": arguments.timeout,
            "max_total_seconds": arguments.max_total_seconds,
            "max_prompt_estimated_tokens": arguments.max_prompt_estimated_tokens,
            "min_pairs_for_claim": arguments.min_pairs_for_claim,
            "claim_alpha": arguments.claim_alpha,
            "min_consecutive_sizes": arguments.min_consecutive_sizes,
        },
        "privacy": {
            "personal_elm_opened": False,
            "credentials_read_by_harness": False,
            "raw_provider_output_retained": False,
            "failed_response_payloads_retained": False,
            "conversation_or_session_ids_retained": False,
            "provider_usage_semantics_normalized": False,
            "oracle_exposed_to_model_schema": False,
        },
        "capabilities": capabilities,
        "sizes": public_sizes,
        "schedule": schedule,
        "checks": checks,
        "passed": all(checks.values()),
        "aborted_reason": aborted_reason,
        "status_counts": dict(sorted(Counter(run["status"] for run in runs).items())),
        "runs": runs,
        "within_route_pairs": comparisons,
        "curve_aggregates": aggregates,
        "crossover_summary": crossover,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Authorize real host CLI runs.")
    parser.add_argument("--validate-only", action="store_true", help="Validate without model calls.")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Build exact synthetic roots and prompt estimates without model calls.",
    )
    parser.add_argument("--assert-pass", action="store_true")
    parser.add_argument(
        "--claim-capable",
        action="store_true",
        help="Enable crossover claims only with an explicit reproducible route configuration.",
    )
    parser.add_argument(
        "--acknowledge-brokered-claim-scope",
        action="store_true",
        help=(
            "Acknowledge that host-brokered Antigravity runs support only a bounded "
            "context-prompt efficiency claim, not an autonomous MCP tool-use claim."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed provider cell.",
    )
    parser.add_argument("--routes", nargs="+", choices=PILOT.ROUTES, default=("codex",))
    parser.add_argument("--case-ids", nargs="+", default=("orion_storage",))
    parser.add_argument("--all-cases", action="store_true")
    parser.add_argument(
        "--target-corpus-tokens", nargs="+", type=int, default=DEFAULT_TARGET_TOKENS
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--codex-model", dest="openai_model")
    parser.add_argument(
        "--codex-reasoning-effort",
        dest="openai_reasoning_effort",
        choices=CODEX_REASONING_EFFORTS,
    )
    parser.add_argument("--gemini-model")
    parser.add_argument("--antigravity-claude-model")
    parser.add_argument(
        "--antigravity-adapter",
        choices=PILOT.ANTIGRAVITY_ADAPTERS,
        default=PILOT.DEFAULT_ANTIGRAVITY_ADAPTER,
    )
    parser.add_argument("--claude-code-model")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-total-seconds", type=float, default=7_200.0)
    parser.add_argument("--max-prompt-estimated-tokens", type=int, default=250_000)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    parser.add_argument("--max-runs", type=int, default=48)
    parser.add_argument("--min-pairs-for-claim", type=int, default=5)
    parser.add_argument("--claim-alpha", type=float, default=0.05)
    parser.add_argument("--min-consecutive-sizes", type=int, default=2)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    all_case_ids = tuple(case["id"] for case in PILOT.load_cases())
    if arguments.all_cases:
        arguments.case_ids = all_case_ids
    arguments.routes = tuple(dict.fromkeys(arguments.routes))
    arguments.case_ids = tuple(dict.fromkeys(arguments.case_ids))
    arguments.target_corpus_tokens = tuple(arguments.target_corpus_tokens)
    if sum((arguments.execute, arguments.validate_only, arguments.plan_only)) > 1:
        parser.error("--execute, --validate-only, and --plan-only are mutually exclusive")
    unknown_cases = sorted(set(arguments.case_ids) - set(all_case_ids))
    if unknown_cases:
        parser.error(f"unknown case ids: {', '.join(unknown_cases)}")
    if not arguments.target_corpus_tokens or any(
        target <= 0 or target > 500_000 for target in arguments.target_corpus_tokens
    ):
        parser.error("--target-corpus-tokens values must be in [1, 500000]")
    if list(arguments.target_corpus_tokens) != sorted(set(arguments.target_corpus_tokens)):
        parser.error("--target-corpus-tokens must be strictly increasing and unique")
    baseline_tokens = PILOT.estimate_tokens(PILOT.active_corpus(PILOT.FIXTURE_ROOT))
    if arguments.target_corpus_tokens[0] < baseline_tokens:
        parser.error(
            f"smallest target must be at least the base corpus estimate ({baseline_tokens})"
        )
    if arguments.repeats < 2 or arguments.repeats > 10 or arguments.repeats % 2:
        parser.error("--repeats must be an even integer in [2, 10]")
    if arguments.timeout <= 0 or arguments.timeout > 900:
        parser.error("--timeout must be in (0, 900]")
    if arguments.max_total_seconds <= 0 or arguments.max_total_seconds > 21_600:
        parser.error("--max-total-seconds must be in (0, 21600]")
    if not 1_000 <= arguments.max_prompt_estimated_tokens <= 1_000_000:
        parser.error("--max-prompt-estimated-tokens must be in [1000, 1000000]")
    if arguments.max_cost_usd <= 0 or arguments.max_cost_usd > 10:
        parser.error("--max-cost-usd must be in (0, 10]")
    if arguments.max_runs <= 0 or arguments.max_runs > 500:
        parser.error("--max-runs must be in [1, 500]")
    if arguments.min_pairs_for_claim < 5 or arguments.min_pairs_for_claim > 100:
        parser.error("--min-pairs-for-claim must be in [5, 100]")
    if not 0 < arguments.claim_alpha <= 0.05:
        parser.error("--claim-alpha must be in (0, 0.05]")
    if arguments.min_consecutive_sizes < 2:
        parser.error("--min-consecutive-sizes must be at least 2")
    planned_runs = (
        len(arguments.routes)
        * len(arguments.case_ids)
        * len(arguments.target_corpus_tokens)
        * arguments.repeats
        * len(CURVE_CONDITIONS)
    )
    if planned_runs > arguments.max_runs:
        parser.error(
            f"planned provider runs ({planned_runs}) exceed --max-runs ({arguments.max_runs})"
        )
    if arguments.claim_capable and not arguments.validate_only:
        models, _ = route_models(arguments)
        if not claim_capable_configuration_is_valid(arguments, models):
            parser.error(
                "--claim-capable requires at least the configured pair/size minima, "
                "an explicit available model for every route, and explicit Codex "
                "reasoning effort when Codex is selected"
            )
    if arguments.validate_only:
        checks = validate_static_contract(
            routes=arguments.routes,
            case_ids=arguments.case_ids,
            targets=arguments.target_corpus_tokens,
            repeats=arguments.repeats,
        )
        result = {
            "schema": "elm-corpus-size-curve-static-validation-v1",
            "passed": all(checks.values()),
            "checks": checks,
            "planned_provider_runs": planned_runs,
        }
    elif arguments.plan_only:
        result = build_preflight_plan(arguments)
    else:
        if not arguments.execute:
            parser.error("real provider runs require the explicit --execute flag")
        result = run_curve(arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
