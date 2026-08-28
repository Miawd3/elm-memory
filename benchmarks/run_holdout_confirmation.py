#!/usr/bin/env python3
"""Run the frozen, case-level ELM holdout confirmation panel."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import sys
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PILOT_PATH = Path(__file__).with_name("run_heterogeneous_pilot.py")
CURVE_PATH = Path(__file__).with_name("run_corpus_size_curve.py")
CASES_PATH = Path(__file__).with_name("holdout_cases.json")
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "holdout_elm"
TARGET_CORPUS_TOKENS = (128_000, 192_000, 208_000)
PRIMARY_CONFIRMATION_TARGETS = (192_000, 208_000)
REPEATS = 2
CONDITIONS = ("elm", "full_corpus")
CONTROL_CONDITION = "no_memory"
EXPECTED_CASE_COUNT = 6
EXPECTED_PROJECT_COUNT = 3
EXPECTED_CODEX_MODEL = "gpt-5.6-sol"
EXPECTED_CODEX_REASONING_EFFORT = "low"
EXPECTED_CODEX_CLI_VERSION = "codex-cli 0.149.0"
EFFECTIVE_CONTEXT_TOKENS = 258_400
MIN_CONTEXT_HEADROOM_TOKENS = 45_000
MAX_INITIAL_PROMPT_TOKENS = EFFECTIVE_CONTEXT_TOKENS - MIN_CONTEXT_HEADROOM_TOKENS
FROZEN_MAX_PROMPT_TOKENS = 210_000
FROZEN_MAX_RUNS = 78
FROZEN_MAX_TOTAL_SECONDS = 10_800.0
FROZEN_PER_RUN_TIMEOUT_SECONDS = 300.0
MATERIAL_MEDIAN_RATIO = 0.90
CLAIM_ALPHA = 0.05
PANEL_PROTOCOL = "elm-holdout-confirmation-v1"
DISTRACTOR_PAYLOAD_CHARS = 3_600
FILLER_PHRASE = (
    "birch granite sailcloth tundra prism kiln feather estuary walnut "
    "topaz grove parchment thimble basalt lagoon saffron trellis "
)
DISTRACTOR_BANDS: tuple[tuple[Path, str | None, str], ...] = (
    (
        Path("10_shared") / "holdout_pre",
        None,
        "neutral catalog routing and maintenance vocabulary",
    ),
    (
        Path("20_projects") / "amber" / "HISTORY" / "near_miss",
        "amber",
        "Amber recovery envelope serialization review and sealing-key rotation vocabulary",
    ),
    (
        Path("20_projects") / "citrine_noise" / "HISTORY",
        None,
        "synthetic transfer catalog and bounded maintenance vocabulary",
    ),
    (
        Path("20_projects") / "mosaic" / "HISTORY" / "near_miss",
        "mosaic",
        "Mosaic delivery attempt interval retry checksum chunk and assembly vocabulary",
    ),
    (
        Path("20_projects") / "rutile_noise" / "HISTORY",
        None,
        "synthetic integrity catalog and offline routing vocabulary",
    ),
    (
        Path("20_projects") / "zephyr" / "HISTORY" / "near_miss",
        "zephyr",
        "Zephyr UDP loopback health probe port snapshot timestamp and digest vocabulary",
    ),
)
PROJECT_OVERFLOW_BANDS = {
    "amber": Path("20_projects") / "burnished_noise" / "HISTORY",
    "mosaic": Path("20_projects") / "pearl_noise" / "HISTORY",
    "zephyr": Path("20_projects") / "yarrow_noise" / "HISTORY",
}
CASE_FIELDS = {
    "id",
    "project",
    "query_stratum",
    "placement_stratum",
    "question",
    "lookup_query",
    "expected_answer",
    "expected_source_path",
    "expected_heading",
}


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if not specification or not specification.loader:
        raise RuntimeError(f"Could not load benchmark module: {path.name}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


PILOT = load_module("elm_holdout_pilot", PILOT_PATH)
CURVE = load_module("elm_holdout_curve", CURVE_PATH)


def load_cases() -> list[dict[str, str]]:
    document = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if document.get("schema") != "elm-holdout-confirmation-cases-v1":
        raise ValueError("Unsupported holdout case schema")
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(f"Holdout panel must contain exactly {EXPECTED_CASE_COUNT} cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            raise ValueError("Every holdout case must use the closed v1 shape")
        if not all(isinstance(case[field], str) and case[field].strip() for field in CASE_FIELDS):
            raise ValueError("Holdout case fields must be non-empty strings")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", case["project"]):
            raise ValueError(f"Unsafe holdout project slug: {case['project']!r}")
        if case["id"] in seen:
            raise ValueError(f"Duplicate holdout case id: {case['id']}")
        if not case["expected_source_path"].startswith(
            f"20_projects/{case['project']}/"
        ):
            raise ValueError(f"Case {case['id']} source is outside its project")
        seen.add(case["id"])
    return cases


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def panel_manifest() -> dict[str, Any]:
    fixture_hashes = PILOT.canonical_markdown_hashes(FIXTURE_ROOT)
    cases_sha256 = sha256_bytes(CASES_PATH.read_bytes())
    material = {
        "protocol": PANEL_PROTOCOL,
        "protocol_source_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "pilot_source_sha256": sha256_bytes(PILOT_PATH.read_bytes()),
        "response_schema_sha256": sha256_bytes(PILOT.RESPONSE_SCHEMA_PATH.read_bytes()),
        "cases_sha256": cases_sha256,
        "fixture_manifest_sha256": CURVE.markdown_manifest(fixture_hashes),
        "case_ids": [case["id"] for case in load_cases()],
        "targets": list(TARGET_CORPUS_TOKENS),
        "primary_confirmation_targets": list(PRIMARY_CONFIRMATION_TARGETS),
        "repeats": REPEATS,
        "conditions": list(CONDITIONS),
        "no_memory_controls_per_case": 1,
        "material_median_ratio": MATERIAL_MEDIAN_RATIO,
        "claim_alpha": CLAIM_ALPHA,
        "distractor_bands": [path.as_posix() for path, _, _ in DISTRACTOR_BANDS],
        "project_overflow_bands": {
            project: path.as_posix()
            for project, path in sorted(PROJECT_OVERFLOW_BANDS.items())
        },
        "near_miss_occurrence_modulus": 5,
        "distractor_payload_chars": DISTRACTOR_PAYLOAD_CHARS,
        "filler_phrase_sha256": sha256_bytes(FILLER_PHRASE.encode("utf-8")),
        "expected_codex_model": EXPECTED_CODEX_MODEL,
        "expected_codex_reasoning_effort": EXPECTED_CODEX_REASONING_EFFORT,
        "expected_codex_cli_version": EXPECTED_CODEX_CLI_VERSION,
        "effective_context_tokens": EFFECTIVE_CONTEXT_TOKENS,
        "minimum_context_headroom_tokens": MIN_CONTEXT_HEADROOM_TOKENS,
        "max_prompt_estimated_tokens": FROZEN_MAX_PROMPT_TOKENS,
        "max_provider_runs": FROZEN_MAX_RUNS,
        "max_total_seconds": FROZEN_MAX_TOTAL_SECONDS,
        "per_run_timeout_seconds": FROZEN_PER_RUN_TIMEOUT_SECONDS,
    }
    serialized = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {**material, "panel_manifest_sha256": sha256_bytes(serialized)}


def distractor_document(index: int) -> tuple[Path, str, str | None]:
    directory, project, topic = DISTRACTOR_BANDS[(index - 1) % len(DISTRACTOR_BANDS)]
    occurrence = (index - 1) // len(DISTRACTOR_BANDS) + 1
    if project and occurrence % 5 != 1:
        directory = PROJECT_OVERFLOW_BANDS[project]
        project = None
        topic = "synthetic catalog routing and maintenance vocabulary"
    prefix = FILLER_PHRASE + f"holdoutitem{index:04d} "
    payload = (prefix * (DISTRACTOR_PAYLOAD_CHARS // len(prefix) + 2))[
        :DISTRACTOR_PAYLOAD_CHARS
    ]
    document = (
        f"Title: Synthetic Holdout Distractor {index:04d}\n"
        "Scope: Supplies deterministic near-miss or irrelevant text for a synthetic holdout benchmark.\n"
        "Tags: synthetic, benchmark, holdout, distractor\n"
        "Last updated: 2026-08-27\n"
        "Status: active\n"
        f"Summary: {topic}; no accepted value is stated.\n\n"
        f"# Synthetic holdout item {index:04d}\n\n"
        f"Topic vocabulary: {topic}.\n\n"
        "This item is non-authoritative filler and deliberately omits every ratified benchmark value.\n\n"
        f"{payload}\n"
    )
    return directory / f"DISTRACTOR_{index:04d}.md", document, project


def target_positions(corpus: str, cases: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    corpus_tokens = PILOT.estimate_tokens(corpus)
    for case in cases:
        marker = f"FILE: {case['expected_source_path']}\n"
        offset = corpus.find(marker)
        if offset < 0:
            raise RuntimeError(f"Target file missing from corpus: {case['id']}")
        token_offset = PILOT.estimate_tokens(corpus[:offset])
        positions[case["id"]] = {
            "source_path": case["expected_source_path"],
            "estimated_token_offset": token_offset,
            "fraction_of_corpus": round(token_offset / corpus_tokens, 6),
            "placement_stratum": case["placement_stratum"],
        }
    return positions


def last_active_document(corpus: str) -> str | None:
    paths = [line.removeprefix("FILE: ") for line in corpus.splitlines() if line.startswith("FILE: ")]
    return paths[-1] if paths else None


def prepare_scaled_root(target: Path, target_tokens: int) -> dict[str, Any]:
    if target.exists():
        raise FileExistsError(f"Holdout target already exists: {target}")
    shutil.copytree(FIXTURE_ROOT, target)
    distractor_count = 0
    project_near_miss_counts: Counter[str] = Counter()
    corpus = PILOT.active_corpus(target)
    while PILOT.estimate_tokens(corpus) < target_tokens:
        distractor_count += 1
        relative, body, project = distractor_document(distractor_count)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
        if project:
            project_near_miss_counts[project] += 1
        corpus = PILOT.active_corpus(target)
    rebuilt = PILOT.run_cli(target, "rebuild")
    if rebuilt.get("errors"):
        raise RuntimeError("Scaled holdout fixture rebuild was not clean")
    hashes = PILOT.canonical_markdown_hashes(target)
    cases = load_cases()
    return {
        "target_corpus_estimated_tokens": target_tokens,
        "actual_corpus_estimated_tokens": PILOT.estimate_tokens(corpus),
        "active_markdown_documents": corpus.count("FILE: "),
        "distractor_documents": distractor_count,
        "project_near_miss_documents": dict(sorted(project_near_miss_counts.items())),
        "corpus_utf8_bytes": len(corpus.encode("utf-8")),
        "manifest_sha256": CURVE.markdown_manifest(hashes),
        "markdown_hashes": hashes,
        "rebuild_errors": rebuilt.get("errors", []),
        "target_positions": target_positions(corpus, cases),
        "last_active_document": last_active_document(corpus),
        "corpus": corpus,
    }


def build_schedule(routes: tuple[str, ...] = ("codex",)) -> list[dict[str, Any]]:
    case_ids = tuple(case["id"] for case in load_cases())
    schedule: list[dict[str, Any]] = []
    for route in routes:
        sequence = 0
        for case_id in case_ids:
            sequence += 1
            schedule.append(
                {
                    "sequence": sequence,
                    "route": route,
                    "target_corpus_estimated_tokens": None,
                    "case_id": case_id,
                    "repeat": 0,
                    "pair_id": None,
                    "condition": CONTROL_CONDITION,
                    "condition_position": 0,
                    "is_control": True,
                }
            )
        paired = CURVE.build_schedule(
            (route,), case_ids, TARGET_CORPUS_TOKENS, REPEATS
        )
        for cell in paired:
            sequence += 1
            schedule.append({**cell, "sequence": sequence, "is_control": False})
    return schedule


def paired_schedule(schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [cell for cell in schedule if not cell["is_control"]]


def case_level_summaries(
    comparisons: list[dict[str, Any]],
    *,
    routes: tuple[str, ...] = ("codex",),
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for comparison in comparisons:
        grouped.setdefault(
            (
                comparison["route"],
                comparison["target_corpus_estimated_tokens"],
                comparison["case_id"],
            ),
            [],
        ).append(comparison)
    expected_repeats = set(range(1, REPEATS + 1))
    summaries: list[dict[str, Any]] = []
    for route in routes:
        for target in TARGET_CORPUS_TOKENS:
            for case in load_cases():
                items = grouped.get((route, target, case["id"]), [])
                observed_repeats = {int(item["repeat"]) for item in items}
                complete = (
                    observed_repeats == expected_repeats
                    and len(items) == REPEATS
                    and all(item.get("comparable") for item in items)
                )
                log_ratios = (
                    [
                        math.log(int(item["elm_value"]) / int(item["full_corpus_value"]))
                        for item in items
                    ]
                    if complete
                    else []
                )
                median_log_ratio = statistics.median(log_ratios) if log_ratios else None
                ratio = math.exp(median_log_ratio) if median_log_ratio is not None else None
                summaries.append(
                    {
                        "route": route,
                        "target_corpus_estimated_tokens": target,
                        "case_id": case["id"],
                        "project": case["project"],
                        "query_stratum": case["query_stratum"],
                        "placement_stratum": case["placement_stratum"],
                        "planned_repeat_count": REPEATS,
                        "observed_repeat_count": len(items),
                        "exact_repeat_population": observed_repeats == expected_repeats,
                        "all_repeats_comparable": complete,
                        "median_log_elm_to_full_corpus_ratio": (
                            median_log_ratio if median_log_ratio is not None else None
                        ),
                        "case_level_elm_to_full_corpus_ratio": (
                            ratio if ratio is not None else None
                        ),
                        "elm_below_full_corpus": ratio < 1.0 if ratio is not None else None,
                    }
                )
    return summaries


def aggregate_case_level(
    summaries: list[dict[str, Any]],
    *,
    routes: tuple[str, ...] = ("codex",),
) -> list[dict[str, Any]]:
    cases = load_cases()
    expected_ids = {case["id"] for case in cases}
    aggregates: list[dict[str, Any]] = []
    for route in routes:
        for target in TARGET_CORPUS_TOKENS:
            items = [
                item
                for item in summaries
                if item["route"] == route
                and item["target_corpus_estimated_tokens"] == target
            ]
            complete_items = [item for item in items if item["all_repeats_comparable"]]
            exact_cases = {item["case_id"] for item in items} == expected_ids and len(items) == len(expected_ids)
            ratios = [float(item["case_level_elm_to_full_corpus_ratio"]) for item in complete_items]
            below = sum(ratio < 1.0 for ratio in ratios)
            above = sum(ratio > 1.0 for ratio in ratios)
            ties = len(ratios) - below - above
            sign_fraction = CURVE.one_sided_sign_test_fraction(below, above)
            sign_p = sign_fraction[0] / sign_fraction[1] if sign_fraction else None
            median_ratio = statistics.median(ratios) if ratios else None
            all_complete = exact_cases and len(complete_items) == len(expected_ids)
            qualified = (
                all_complete
                and below == len(expected_ids)
                and sign_p is not None
                and sign_p <= CLAIM_ALPHA
                and median_ratio is not None
                and median_ratio <= MATERIAL_MEDIAN_RATIO
            )
            aggregates.append(
                {
                    "route": route,
                    "target_corpus_estimated_tokens": target,
                    "planned_independent_case_count": len(expected_ids),
                    "observed_independent_case_count": len(items),
                    "exact_case_population": exact_cases,
                    "all_case_summaries_complete": all_complete,
                    "median_case_level_elm_to_full_corpus_ratio": (
                        round(median_ratio, 6) if median_ratio is not None else None
                    ),
                    "elm_below_full_corpus_case_count": below,
                    "elm_above_full_corpus_case_count": above,
                    "tie_case_count": ties,
                    "one_sided_exact_sign_numerator": sign_fraction[0] if sign_fraction else None,
                    "one_sided_exact_sign_denominator": sign_fraction[1] if sign_fraction else None,
                    "one_sided_exact_sign_p": sign_p,
                    "material_median_ratio_threshold": MATERIAL_MEDIAN_RATIO,
                    "confirmation_cell_qualified": qualified,
                }
            )
    return aggregates


def confirmation_summary(
    aggregates: list[dict[str, Any]],
    *,
    global_integrity_passed: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for route in sorted({item["route"] for item in aggregates}):
        by_target = {
            item["target_corpus_estimated_tokens"]: item
            for item in aggregates
            if item["route"] == route
        }
        primary_qualified = all(
            by_target.get(target, {}).get("confirmation_cell_qualified")
            for target in PRIMARY_CONFIRMATION_TARGETS
        )
        confirmed = global_integrity_passed and primary_qualified
        lower_qualified = bool(
            by_target.get(TARGET_CORPUS_TOKENS[0], {}).get("confirmation_cell_qualified")
        )
        if not global_integrity_passed:
            interpretation = "global_integrity_gate_failed"
        elif not confirmed:
            interpretation = "sustained_large_corpus_advantage_not_confirmed"
        elif lower_qualified:
            interpretation = "sustained_advantage_confirmed_onset_at_or_below_128k"
        else:
            interpretation = "sustained_advantage_confirmed_between_128k_and_192k_test_points"
        results.append(
            {
                "route": route,
                "sustained_large_corpus_advantage_confirmed": confirmed,
                "required_targets": list(PRIMARY_CONFIRMATION_TARGETS),
                "lower_anchor_target": TARGET_CORPUS_TOKENS[0],
                "interpretation": interpretation,
                "claim_scope": "independent_synthetic_holdout_panel_only",
            }
        )
    return results


def generator_validation() -> dict[str, bool]:
    cases = load_cases()
    oracle_values = {
        case[field].casefold()
        for case in cases
        for field in ("expected_answer", "expected_source_path", "expected_heading")
    }
    generated = [distractor_document(index) for index in range(1, 13)]
    serialized = "\n".join(body for _, body, _ in generated).casefold()
    covered_projects = {project for _, _, project in generated if project}
    baseline = PILOT.estimate_tokens(PILOT.active_corpus(FIXTURE_ROOT))
    small_target = max(4_000, baseline + 1_000)
    large_target = small_target + 3_000
    with PILOT.disposable_directory("elm-holdout-static-") as scratch:
        small = prepare_scaled_root(scratch / "small", small_target)
        large = prepare_scaled_root(scratch / "large", large_target)
        repeat = prepare_scaled_root(scratch / "repeat", small_target)
    return {
        "generated_corpora_reach_targets": (
            small["actual_corpus_estimated_tokens"] >= small_target
            and large["actual_corpus_estimated_tokens"] >= large_target
        ),
        "generated_corpora_are_deterministic": small["manifest_sha256"] == repeat["manifest_sha256"],
        "generated_corpora_are_nested": CURVE.shared_documents_are_nested(
            small["markdown_hashes"], large["markdown_hashes"]
        ),
        "generated_distractors_are_oracle_free": not any(value in serialized for value in oracle_values),
        "project_near_misses_cover_every_target_project": covered_projects == {case["project"] for case in cases},
        "generated_fixture_rebuilds_are_clean": (
            not small["rebuild_errors"] and not large["rebuild_errors"] and not repeat["rebuild_errors"]
        ),
    }


def validate_static_contract() -> dict[str, bool]:
    cases = load_cases()
    case_ids = tuple(case["id"] for case in cases)
    schedule = build_schedule()
    pairs = paired_schedule(schedule)
    corpus = PILOT.active_corpus(FIXTURE_ROOT)
    schema = PILOT.response_schema()
    serialized_schema = json.dumps(schema, ensure_ascii=False, sort_keys=True).casefold()
    oracle_values = {
        case[field].casefold()
        for case in cases
        for field in ("expected_answer", "expected_source_path", "expected_heading")
    }
    prompts_are_oracle_free = True
    prompts_use_case_projects = True
    for case in cases:
        for condition in ("elm", CONTROL_CONDITION):
            prompt = PILOT.build_prompt(case, condition, corpus).casefold()
            prompts_are_oracle_free = prompts_are_oracle_free and all(
                case[field].casefold() not in prompt
                for field in ("expected_answer", "expected_source_path", "expected_heading")
            )
        prompts_use_case_projects = prompts_use_case_projects and (
            f"project={case['project']!r}" in PILOT.build_prompt(case, "elm", corpus)
        )
    controls = [cell for cell in schedule if cell["is_control"]]
    active_paths = [line.removeprefix("FILE: ") for line in corpus.splitlines() if line.startswith("FILE: ")]
    return {
        "case_count_is_frozen": len(cases) == EXPECTED_CASE_COUNT,
        "case_ids_are_unique": len(set(case_ids)) == len(case_ids),
        "cases_span_three_projects": len({case["project"] for case in cases}) == EXPECTED_PROJECT_COUNT,
        "cases_span_six_source_files": len({case["expected_source_path"] for case in cases}) == EXPECTED_CASE_COUNT,
        "cases_span_multiple_query_strata": len({case["query_stratum"] for case in cases}) >= 5,
        "response_schema_is_closed": schema.get("additionalProperties") is False,
        "response_schema_has_no_oracle_values": not any(value in serialized_schema for value in oracle_values),
        "elm_and_no_memory_prompts_are_oracle_free": prompts_are_oracle_free,
        "elm_prompts_use_case_projects": prompts_use_case_projects,
        "archives_and_backups_are_excluded": not any(
            PILOT.is_archive_path(path) for path in active_paths
        ),
        "tail_canary_is_last_in_base_corpus": last_active_document(corpus) == "20_projects/zephyr/ZZZ_TAIL_CANARY.md",
        "schedule_has_one_control_per_case": len(controls) == EXPECTED_CASE_COUNT
        and {cell["case_id"] for cell in controls} == set(case_ids),
        "paired_schedule_is_counterbalanced": CURVE.schedule_is_counterbalanced(pairs),
        "planned_run_count_is_frozen": len(schedule) == FROZEN_MAX_RUNS,
        "frozen_prompt_cap_preserves_required_headroom": (
            FROZEN_MAX_PROMPT_TOKENS <= MAX_INITIAL_PROMPT_TOKENS
        ),
        **generator_validation(),
    }


def route_configuration_is_frozen(arguments: argparse.Namespace, version: str | None) -> bool:
    return (
        arguments.openai_model == EXPECTED_CODEX_MODEL
        and arguments.openai_reasoning_effort == EXPECTED_CODEX_REASONING_EFFORT
        and version == EXPECTED_CODEX_CLI_VERSION
    )


def public_size(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "target_corpus_estimated_tokens",
            "actual_corpus_estimated_tokens",
            "active_markdown_documents",
            "distractor_documents",
            "project_near_miss_documents",
            "corpus_utf8_bytes",
            "manifest_sha256",
            "rebuild_errors",
            "target_positions",
            "last_active_document",
        )
    }


def placement_checks(sizes: list[dict[str, Any]]) -> dict[str, bool]:
    all_positions = [
        position["fraction_of_corpus"]
        for size in sizes
        for position in size["target_positions"].values()
    ]
    return {
        "target_positions_cover_early_middle_and_late": (
            any(value <= 0.33 for value in all_positions)
            and any(0.33 < value < 0.67 for value in all_positions)
            and any(value >= 0.67 for value in all_positions)
        ),
        "tail_canary_remains_last_at_every_size": all(
            size["last_active_document"] == "20_projects/zephyr/ZZZ_TAIL_CANARY.md"
            for size in sizes
        ),
    }


def build_preflight_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases()
    schedule = build_schedule()
    checks = validate_static_contract()
    version = PILOT.command_version("codex")
    checks["route_configuration_matches_development_panel"] = route_configuration_is_frozen(arguments, version)
    sizes: list[dict[str, Any]] = []
    prompt_estimates: list[dict[str, Any]] = []
    all_expected_sections_resolve = True
    all_context_packets_select_expected_section = True
    with PILOT.disposable_directory("elm-holdout-plan-") as scratch:
        for target in TARGET_CORPUS_TOKENS:
            record = prepare_scaled_root(scratch / "memory" / str(target), target)
            sizes.append(public_size(record))
            for case in cases:
                try:
                    section_key = PILOT.expected_section_key(
                        scratch / "memory" / str(target), case
                    )
                    packet = PILOT.run_cli(
                        scratch / "memory" / str(target),
                        "context",
                        case["question"],
                        "--project",
                        case["project"],
                        "--budget",
                        "700",
                        "--no-sync",
                        "--no-trace",
                    )
                    all_context_packets_select_expected_section = (
                        all_context_packets_select_expected_section
                        and section_key in packet.get("selected_section_keys", [])
                        and packet.get("estimated_tokens", 701) <= 700
                    )
                except RuntimeError:
                    all_expected_sections_resolve = False
                    all_context_packets_select_expected_section = False
            for condition in CONDITIONS:
                estimates = [
                    PILOT.estimate_tokens(PILOT.build_prompt(case, condition, record["corpus"]))
                    for case in cases
                ]
                prompt_estimates.append(
                    {
                        "target_corpus_estimated_tokens": target,
                        "condition": condition,
                        "minimum_initial_prompt_estimated_tokens": min(estimates),
                        "maximum_initial_prompt_estimated_tokens": max(estimates),
                    }
                )
        control_estimates = [
            PILOT.estimate_tokens(PILOT.build_prompt(case, CONTROL_CONDITION, ""))
            for case in cases
        ]
        prompt_estimates.append(
            {
                "target_corpus_estimated_tokens": None,
                "condition": CONTROL_CONDITION,
                "minimum_initial_prompt_estimated_tokens": min(control_estimates),
                "maximum_initial_prompt_estimated_tokens": max(control_estimates),
            }
        )
    maximum_prompt = max(item["maximum_initial_prompt_estimated_tokens"] for item in prompt_estimates)
    headroom = EFFECTIVE_CONTEXT_TOKENS - maximum_prompt
    checks["all_expected_sections_resolve"] = all_expected_sections_resolve
    checks["all_context_packets_select_expected_section"] = (
        all_context_packets_select_expected_section
    )
    checks["preflight_prompt_cap_passed"] = maximum_prompt <= arguments.max_prompt_estimated_tokens
    checks["context_headroom_gate_passed"] = headroom >= MIN_CONTEXT_HEADROOM_TOKENS
    checks.update(placement_checks(sizes))
    return {
        "schema": "elm-holdout-confirmation-plan-v1",
        "passed": all(checks.values()),
        "panel": panel_manifest(),
        "configuration": {
            "routes": ["codex"],
            "case_ids": [case["id"] for case in cases],
            "target_corpus_estimated_tokens": list(TARGET_CORPUS_TOKENS),
            "primary_confirmation_targets": list(PRIMARY_CONFIRMATION_TARGETS),
            "repeats_per_case": REPEATS,
            "planned_provider_runs": len(schedule),
            "planned_paired_runs": len(paired_schedule(schedule)),
            "planned_no_memory_controls": len(schedule) - len(paired_schedule(schedule)),
            "statistical_unit": "holdout_case",
            "repeats_are_independent_units": False,
            "estimand": "median_within_case_log_elm_to_full_corpus_token_ratio",
            "claim_scope": "independent_synthetic_holdout_panel_only",
            "population_generalization_supported": False,
            "codex_model_requested": arguments.openai_model,
            "codex_reasoning_effort_requested": arguments.openai_reasoning_effort,
            "codex_cli_version": version,
            "effective_context_tokens": EFFECTIVE_CONTEXT_TOKENS,
            "minimum_context_headroom_tokens": MIN_CONTEXT_HEADROOM_TOKENS,
            "observed_preflight_headroom_tokens": headroom,
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


def run_confirmation(arguments: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases()
    cases_by_id = {case["id"]: case for case in cases}
    schedule = build_schedule()
    version = PILOT.command_version("codex")
    static_checks = validate_static_contract()
    started = time.perf_counter()
    runs: list[dict[str, Any]] = []
    size_records: dict[int, dict[str, Any]] = {}
    aborted_reason = None
    preflight_prompt_cap_passed = True
    with PILOT.disposable_directory("elm-holdout-confirmation-") as scratch:
        runtime = PILOT.prepare_runtime(scratch)
        for target in TARGET_CORPUS_TOKENS:
            root = scratch / "memory" / str(target)
            record = prepare_scaled_root(root, target)
            record["section_keys"] = {
                case["id"]: PILOT.expected_section_key(root, case) for case in cases
            }
            record["before_hashes"] = record["markdown_hashes"]
            size_records[target] = record
        for cell in schedule:
            target = cell["target_corpus_estimated_tokens"] or TARGET_CORPUS_TOKENS[0]
            record = size_records[target]
            case = cases_by_id[cell["case_id"]]
            prompt = PILOT.build_prompt(case, cell["condition"], record["corpus"])
            if PILOT.estimate_tokens(prompt) > arguments.max_prompt_estimated_tokens:
                preflight_prompt_cap_passed = False
                aborted_reason = "prompt_limit_exceeded"
                break
        if preflight_prompt_cap_passed:
            for cell in schedule:
                bounded_timeout = CURVE.remaining_run_timeout(
                    started=started,
                    now=time.perf_counter(),
                    per_run_timeout=arguments.timeout,
                    max_total_seconds=arguments.max_total_seconds,
                )
                if bounded_timeout is None:
                    aborted_reason = "total_timeout"
                    break
                target = cell["target_corpus_estimated_tokens"] or TARGET_CORPUS_TOKENS[0]
                record = size_records[target]
                case = cases_by_id[cell["case_id"]]
                prompt = PILOT.build_prompt(case, cell["condition"], record["corpus"])
                workspace = (
                    scratch
                    / "workspaces"
                    / cell["route"]
                    / (f"size-{target}" if not cell["is_control"] else "controls")
                    / cell["case_id"]
                    / (f"repeat-{cell['repeat']}" if not cell["is_control"] else "no-memory")
                    / f"{cell['condition_position']}-{cell['condition']}"
                )
                workspace.mkdir(parents=True)
                run = PILOT.base_run(
                    cell["route"], cell["condition"], cell["case_id"], arguments.openai_model
                )
                run.update(cell)
                run.update(
                    {
                        "actual_corpus_estimated_tokens": (
                            record["actual_corpus_estimated_tokens"] if not cell["is_control"] else None
                        ),
                        "active_markdown_documents": (
                            record["active_markdown_documents"] if not cell["is_control"] else None
                        ),
                        "project": case["project"],
                        "query_stratum": case["query_stratum"],
                        "placement_stratum": case["placement_stratum"],
                        "reasoning_effort_requested": arguments.openai_reasoning_effort,
                        "initial_prompt_utf8_bytes": len(prompt.encode("utf-8")),
                        "initial_prompt_estimated_tokens": PILOT.estimate_tokens(prompt),
                    }
                )
                status, response, usage, audit, elapsed = CURVE.execute_provider_cell(
                    cell=cell,
                    workspace=workspace,
                    root=scratch / "memory" / str(target),
                    runtime=runtime,
                    prompt=prompt,
                    model=arguments.openai_model,
                    arguments=arguments,
                    timeout=bounded_timeout,
                )
                CURVE.finalize_run(
                    run=run,
                    status=status,
                    response=response,
                    usage=usage,
                    audit=audit,
                    elapsed=elapsed,
                    case=case,
                    section_key=record["section_keys"][case["id"]],
                )
                runs.append(run)
                if arguments.fail_fast and not run["passed"]:
                    aborted_reason = "run_failed"
                    break
                if time.perf_counter() - started > arguments.max_total_seconds:
                    aborted_reason = "total_timeout"
                    break
        unchanged = all(
            record["before_hashes"]
            == PILOT.canonical_markdown_hashes(scratch / "memory" / str(target))
            for target, record in size_records.items()
        )
    pair_runs = [run for run in runs if not run["is_control"]]
    controls = [run for run in runs if run["is_control"]]
    comparisons = CURVE.build_pair_comparisons(pair_runs)
    summaries = case_level_summaries(comparisons)
    aggregates = aggregate_case_level(summaries)
    expected_pairs = len(paired_schedule(schedule)) // len(CONDITIONS)
    checks = {
        **static_checks,
        "route_configuration_matches_development_panel": route_configuration_is_frozen(arguments, version),
        "preflight_prompt_cap_passed": preflight_prompt_cap_passed,
        "all_scheduled_runs_executed": len(runs) == len(schedule),
        "all_selected_runs_passed": len(runs) == len(schedule) and all(run["passed"] for run in runs),
        "all_no_memory_controls_passed": len(controls) == EXPECTED_CASE_COUNT and all(run["passed"] for run in controls),
        "all_selected_runs_have_complete_usage": len(runs) == len(schedule)
        and all(run["checks"]["provider_usage_complete"] for run in runs),
        "all_selected_runs_have_verified_tool_provenance": len(runs) == len(schedule)
        and all(run["checks"]["tool_provenance_verified"] for run in runs),
        "all_required_pairs_are_comparable": len(comparisons) == expected_pairs
        and all(item["comparable"] for item in comparisons),
        "all_case_level_summaries_are_complete": all(item["all_repeats_comparable"] for item in summaries),
        "canonical_markdown_unchanged": unchanged,
        "total_time_cap_respected": aborted_reason != "total_timeout"
        and time.perf_counter() - started <= arguments.max_total_seconds,
    }
    checks.update(placement_checks([public_size(size_records[target]) for target in TARGET_CORPUS_TOKENS]))
    global_integrity = all(checks.values())
    return {
        "schema": "elm-holdout-confirmation-v1",
        "passed": global_integrity,
        "panel": panel_manifest(),
        "configuration": {
            "route": "codex",
            "model_requested": arguments.openai_model,
            "reasoning_effort_requested": arguments.openai_reasoning_effort,
            "codex_cli_version": version,
            "target_corpus_estimated_tokens": list(TARGET_CORPUS_TOKENS),
            "primary_confirmation_targets": list(PRIMARY_CONFIRMATION_TARGETS),
            "repeats_per_case": REPEATS,
            "statistical_unit": "holdout_case",
            "repeats_are_independent_units": False,
            "estimand": "median_within_case_log_elm_to_full_corpus_token_ratio",
            "material_median_ratio_threshold": MATERIAL_MEDIAN_RATIO,
            "claim_alpha": CLAIM_ALPHA,
            "fail_fast": arguments.fail_fast,
            "timeout_seconds_per_run": arguments.timeout,
            "max_total_seconds": arguments.max_total_seconds,
            "max_prompt_estimated_tokens": arguments.max_prompt_estimated_tokens,
        },
        "privacy": {
            "personal_elm_opened": False,
            "credentials_read_by_harness": False,
            "raw_provider_output_retained": False,
            "failed_response_payloads_retained": False,
            "conversation_or_session_ids_retained": False,
            "oracle_exposed_to_model_schema": False,
        },
        "sizes": [public_size(size_records[target]) for target in TARGET_CORPUS_TOKENS],
        "schedule": schedule,
        "checks": checks,
        "aborted_reason": aborted_reason,
        "status_counts": dict(sorted(Counter(run["status"] for run in runs).items())),
        "runs": runs,
        "within_repeat_pairs": comparisons,
        "case_level_summaries": summaries,
        "case_level_aggregates": aggregates,
        "confirmation_summary": confirmation_summary(
            aggregates, global_integrity_passed=global_integrity
        ),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Authorize the frozen real Codex run.")
    parser.add_argument("--validate-only", action="store_true", help="Run static checks without provider calls.")
    parser.add_argument("--plan-only", action="store_true", help="Build exact roots and prompt estimates without provider calls.")
    parser.add_argument("--assert-pass", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--codex-model", dest="openai_model")
    parser.add_argument(
        "--codex-reasoning-effort",
        dest="openai_reasoning_effort",
        choices=CURVE.CODEX_REASONING_EFFORTS,
    )
    parser.add_argument("--timeout", type=float, default=FROZEN_PER_RUN_TIMEOUT_SECONDS)
    parser.add_argument("--max-total-seconds", type=float, default=FROZEN_MAX_TOTAL_SECONDS)
    parser.add_argument("--max-prompt-estimated-tokens", type=int, default=FROZEN_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-runs", type=int, default=FROZEN_MAX_RUNS)
    parser.set_defaults(max_cost_usd=1.0)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if sum((arguments.execute, arguments.validate_only, arguments.plan_only)) != 1:
        parser.error("choose exactly one of --execute, --validate-only, or --plan-only")
    if arguments.timeout != FROZEN_PER_RUN_TIMEOUT_SECONDS:
        parser.error(f"the frozen panel requires --timeout {FROZEN_PER_RUN_TIMEOUT_SECONDS:g}")
    if arguments.max_total_seconds != FROZEN_MAX_TOTAL_SECONDS:
        parser.error(
            f"the frozen panel requires --max-total-seconds {FROZEN_MAX_TOTAL_SECONDS:g}"
        )
    if arguments.max_prompt_estimated_tokens != FROZEN_MAX_PROMPT_TOKENS:
        parser.error(
            "the frozen panel requires --max-prompt-estimated-tokens "
            f"{FROZEN_MAX_PROMPT_TOKENS}"
        )
    planned_runs = len(build_schedule())
    if planned_runs != FROZEN_MAX_RUNS or arguments.max_runs != FROZEN_MAX_RUNS:
        parser.error(f"the frozen panel requires --max-runs {FROZEN_MAX_RUNS}")
    if arguments.plan_only or arguments.execute:
        if (
            arguments.openai_model != EXPECTED_CODEX_MODEL
            or arguments.openai_reasoning_effort != EXPECTED_CODEX_REASONING_EFFORT
        ):
            parser.error(
                f"the frozen panel requires --codex-model {EXPECTED_CODEX_MODEL} "
                f"and --codex-reasoning-effort {EXPECTED_CODEX_REASONING_EFFORT}"
            )
    if arguments.execute and not arguments.fail_fast:
        parser.error("the frozen provider run requires --fail-fast")
    if arguments.validate_only:
        checks = validate_static_contract()
        result = {
            "schema": "elm-holdout-confirmation-static-validation-v1",
            "passed": all(checks.values()),
            "panel": panel_manifest(),
            "checks": checks,
            "planned_provider_runs": planned_runs,
        }
    elif arguments.plan_only:
        result = build_preflight_plan(arguments)
    else:
        result = run_confirmation(arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_pass and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
