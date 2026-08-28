from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "benchmarks" / "run_holdout_confirmation.py"
SPEC = importlib.util.spec_from_file_location("run_holdout_confirmation", MODULE_PATH)
assert SPEC and SPEC.loader
HOLDOUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOLDOUT)


class HoldoutConfirmationContractTests(unittest.TestCase):
    def test_cases_are_closed_multi_project_and_oracle_free(self) -> None:
        cases = HOLDOUT.load_cases()
        corpus = HOLDOUT.PILOT.active_corpus(HOLDOUT.FIXTURE_ROOT)

        self.assertEqual(6, len(cases))
        self.assertEqual(3, len({case["project"] for case in cases}))
        self.assertEqual(6, len({case["expected_source_path"] for case in cases}))
        for case in cases:
            for condition in ("elm", "no_memory"):
                prompt = HOLDOUT.PILOT.build_prompt(case, condition, corpus).casefold()
                self.assertNotIn(case["expected_answer"].casefold(), prompt)
                self.assertNotIn(case["expected_source_path"].casefold(), prompt)
                self.assertNotIn(case["expected_heading"].casefold(), prompt)
            self.assertIn(
                f"project={case['project']!r}",
                HOLDOUT.PILOT.build_prompt(case, "elm", corpus),
            )

    def test_scaled_holdout_is_nested_and_grows_project_near_misses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-holdout-test-") as temporary:
            root = Path(temporary)
            small = HOLDOUT.prepare_scaled_root(root / "small", 8_000)
            repeat = HOLDOUT.prepare_scaled_root(root / "repeat", 8_000)
            large = HOLDOUT.prepare_scaled_root(root / "large", 32_000)

        self.assertEqual(small["manifest_sha256"], repeat["manifest_sha256"])
        self.assertTrue(
            HOLDOUT.CURVE.shared_documents_are_nested(
                small["markdown_hashes"], large["markdown_hashes"]
            )
        )
        self.assertTrue(
            all(
                large["project_near_miss_documents"][project]
                >= small["project_near_miss_documents"].get(project, 0)
                for project in ("amber", "mosaic", "zephyr")
            )
        )
        self.assertEqual(
            "20_projects/zephyr/ZZZ_TAIL_CANARY.md",
            large["last_active_document"],
        )

    def test_schedule_has_six_controls_and_counterbalanced_pairs(self) -> None:
        schedule = HOLDOUT.build_schedule()
        controls = [cell for cell in schedule if cell["is_control"]]
        pairs = HOLDOUT.paired_schedule(schedule)

        self.assertEqual(78, len(schedule))
        self.assertEqual(6, len(controls))
        self.assertEqual(72, len(pairs))
        self.assertTrue(HOLDOUT.CURVE.schedule_is_counterbalanced(pairs))

    def test_case_is_the_independence_unit_not_each_repeat(self) -> None:
        comparisons = []
        for target in HOLDOUT.TARGET_CORPUS_TOKENS:
            for case in HOLDOUT.load_cases():
                for repeat in range(1, HOLDOUT.REPEATS + 1):
                    comparisons.append(
                        {
                            "route": "codex",
                            "target_corpus_estimated_tokens": target,
                            "case_id": case["id"],
                            "repeat": repeat,
                            "comparable": True,
                            "elm_value": 800,
                            "full_corpus_value": 1_000,
                        }
                    )

        summaries = HOLDOUT.case_level_summaries(comparisons)
        aggregates = HOLDOUT.aggregate_case_level(summaries)

        self.assertEqual(18, len(summaries))
        self.assertTrue(all(item["all_repeats_comparable"] for item in summaries))
        self.assertTrue(all(item["planned_independent_case_count"] == 6 for item in aggregates))
        self.assertTrue(all(item["elm_below_full_corpus_case_count"] == 6 for item in aggregates))
        self.assertTrue(all(item["one_sided_exact_sign_denominator"] == 64 for item in aggregates))
        self.assertTrue(all(item["confirmation_cell_qualified"] for item in aggregates))

    def test_one_reversed_case_prevents_a_confirmation_cell(self) -> None:
        comparisons = []
        reversed_case = HOLDOUT.load_cases()[0]["id"]
        for target in HOLDOUT.TARGET_CORPUS_TOKENS:
            for case in HOLDOUT.load_cases():
                for repeat in range(1, HOLDOUT.REPEATS + 1):
                    comparisons.append(
                        {
                            "route": "codex",
                            "target_corpus_estimated_tokens": target,
                            "case_id": case["id"],
                            "repeat": repeat,
                            "comparable": True,
                            "elm_value": 1_100 if case["id"] == reversed_case else 800,
                            "full_corpus_value": 1_000,
                        }
                    )

        aggregates = HOLDOUT.aggregate_case_level(
            HOLDOUT.case_level_summaries(comparisons)
        )

        self.assertTrue(all(item["elm_below_full_corpus_case_count"] == 5 for item in aggregates))
        self.assertTrue(all(not item["confirmation_cell_qualified"] for item in aggregates))

    def test_confirmation_requires_both_192k_and_208k(self) -> None:
        aggregates = [
            {
                "route": "codex",
                "target_corpus_estimated_tokens": target,
                "confirmation_cell_qualified": target != 208_000,
            }
            for target in HOLDOUT.TARGET_CORPUS_TOKENS
        ]

        summary = HOLDOUT.confirmation_summary(
            aggregates, global_integrity_passed=True
        )[0]

        self.assertFalse(summary["sustained_large_corpus_advantage_confirmed"])
        self.assertEqual(
            "sustained_large_corpus_advantage_not_confirmed",
            summary["interpretation"],
        )

    def test_preflight_builds_frozen_roots_without_provider_calls(self) -> None:
        arguments = argparse.Namespace(
            openai_model=HOLDOUT.EXPECTED_CODEX_MODEL,
            openai_reasoning_effort=HOLDOUT.EXPECTED_CODEX_REASONING_EFFORT,
            max_prompt_estimated_tokens=210_000,
            max_total_seconds=10_800.0,
            max_runs=78,
        )
        original_version = HOLDOUT.PILOT.command_version
        original_provider = HOLDOUT.CURVE.PILOT.run_codex
        try:
            HOLDOUT.PILOT.command_version = lambda _: HOLDOUT.EXPECTED_CODEX_CLI_VERSION
            HOLDOUT.CURVE.PILOT.run_codex = lambda **_: self.fail("provider call attempted")
            plan = HOLDOUT.build_preflight_plan(arguments)
        finally:
            HOLDOUT.PILOT.command_version = original_version
            HOLDOUT.CURVE.PILOT.run_codex = original_provider

        self.assertTrue(plan["passed"], plan["checks"])
        self.assertEqual("holdout_case", plan["configuration"]["statistical_unit"])
        self.assertFalse(plan["configuration"]["repeats_are_independent_units"])
        self.assertEqual(78, plan["configuration"]["planned_provider_runs"])
        self.assertGreaterEqual(
            plan["configuration"]["observed_preflight_headroom_tokens"], 45_000
        )
        self.assertFalse(plan["privacy"]["provider_calls_executed"])


if __name__ == "__main__":
    unittest.main()
