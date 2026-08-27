from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "benchmarks" / "run_corpus_size_curve.py"
SPEC = importlib.util.spec_from_file_location("run_corpus_size_curve", MODULE_PATH)
assert SPEC and SPEC.loader
CURVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CURVE)


class CorpusSizeCurveContractTests(unittest.TestCase):
    def test_scaled_corpora_are_deterministic_nested_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-curve-test-") as temporary:
            root = Path(temporary)
            small = CURVE.prepare_scaled_root(root / "small", 2_000)
            repeat = CURVE.prepare_scaled_root(root / "repeat", 2_000)
            large = CURVE.prepare_scaled_root(root / "large", 5_000)

        self.assertGreaterEqual(small["actual_corpus_estimated_tokens"], 2_000)
        self.assertGreaterEqual(large["actual_corpus_estimated_tokens"], 5_000)
        self.assertEqual(small["manifest_sha256"], repeat["manifest_sha256"])
        self.assertTrue(
            CURVE.shared_documents_are_nested(
                small["markdown_hashes"], large["markdown_hashes"]
            )
        )
        self.assertEqual([], small["rebuild_errors"])
        self.assertGreater(large["active_markdown_documents"], small["active_markdown_documents"])

    def test_distractors_do_not_contain_evaluator_oracles(self) -> None:
        body = CURVE.distractor_document(1).casefold()
        for case in CURVE.PILOT.load_cases():
            for field in ("expected_answer", "expected_source_path", "expected_heading"):
                self.assertNotIn(case[field].casefold(), body)
        self.assertFalse(
            CURVE.normalized_lexical_vocabulary(body) & CURVE.case_query_vocabulary()
        )

    def test_schedule_balances_pair_order_and_reverses_size_order(self) -> None:
        schedule = CURVE.build_schedule(
            ("codex",),
            ("orion_storage", "orion_time"),
            (2_000, 8_000, 32_000),
            2,
        )

        self.assertTrue(CURVE.schedule_is_counterbalanced(schedule))
        first_repeat_targets = []
        second_repeat_targets = []
        for repeat, destination in ((1, first_repeat_targets), (2, second_repeat_targets)):
            for cell in schedule:
                if (
                    cell["repeat"] == repeat
                    and cell["case_id"] == "orion_storage"
                    and cell["condition_position"] == 1
                ):
                    destination.append(cell["target_corpus_estimated_tokens"])
        self.assertEqual([2_000, 8_000, 32_000], first_repeat_targets)
        self.assertEqual([32_000, 8_000, 2_000], second_repeat_targets)

    def test_sign_test_requires_repeated_directional_evidence(self) -> None:
        self.assertEqual(0.03125, CURVE.one_sided_sign_test_p(5, 0))
        self.assertEqual(0.1875, CURVE.one_sided_sign_test_p(4, 1))
        self.assertGreater(CURVE.one_sided_sign_test_p(30, 0), 0.0)
        self.assertEqual((1, 32), CURVE.one_sided_sign_test_fraction(5, 0))
        self.assertIsNone(CURVE.one_sided_sign_test_p(0, 0))

    def test_remaining_timeout_is_bounded_by_the_total_budget(self) -> None:
        self.assertEqual(
            2.5,
            CURVE.remaining_run_timeout(
                started=10.0,
                now=17.5,
                per_run_timeout=5.0,
                max_total_seconds=10.0,
            ),
        )
        self.assertIsNone(
            CURVE.remaining_run_timeout(
                started=10.0,
                now=20.0,
                per_run_timeout=5.0,
                max_total_seconds=10.0,
            )
        )

    def test_antigravity_grace_is_subtracted_before_dependency_call(self) -> None:
        self.assertIsNone(CURVE.provider_timeout_within_budget("gemini-antigravity", 10.0))
        self.assertEqual(
            12.0, CURVE.provider_timeout_within_budget("codex", 12.0)
        )
        provider_timeout = CURVE.provider_timeout_within_budget(
            "gemini-antigravity", 12.0
        )
        captured = {}
        original = CURVE.PILOT.run_antigravity

        def fake_antigravity(**kwargs):
            captured.update(kwargs)
            return (
                "unavailable",
                None,
                CURVE.PILOT.sanitized_usage("google-gemini-antigravity", None),
                CURVE.PILOT.empty_tool_audit(),
                0.0,
            )

        try:
            CURVE.PILOT.run_antigravity = fake_antigravity
            CURVE.execute_provider_cell(
                cell={"route": "gemini-antigravity", "condition": "elm"},
                workspace=Path("workspace"),
                root=Path("memory"),
                runtime=Path("runtime"),
                prompt="prompt",
                model="gemini-test",
                arguments=CURVE.argparse.Namespace(max_cost_usd=1.0),
                timeout=provider_timeout,
            )
        finally:
            CURVE.PILOT.run_antigravity = original

        self.assertEqual(2.0, captured["timeout"])

    def test_pair_comparison_never_crosses_routes_cases_or_repeats(self) -> None:
        def run(
            route: str,
            target: int,
            case_id: str,
            repeat: int,
            condition: str,
            tokens: int,
            sequence: int,
        ) -> dict:
            return {
                "route": route,
                "target_corpus_estimated_tokens": target,
                "actual_corpus_estimated_tokens": target + 100,
                "case_id": case_id,
                "repeat": repeat,
                "condition": condition,
                "sequence": sequence,
                "passed": True,
                "usage": {"availability": "reported", "total_tokens": tokens},
            }

        comparisons = CURVE.build_pair_comparisons(
            [
                run("gemini-antigravity", 2_000, "case", 1, "elm", 80, 1),
                run("gemini-antigravity", 2_000, "case", 1, "full_corpus", 100, 2),
                run("gemini-antigravity", 2_000, "case", 2, "elm", 70, 3),
                run("claude-antigravity", 2_000, "case", 1, "full_corpus", 90, 1),
            ]
        )

        comparable = [item for item in comparisons if item["comparable"]]
        self.assertEqual(1, len(comparable))
        self.assertEqual(0.8, comparable[0]["elm_to_full_corpus_ratio"])
        self.assertEqual(["elm", "full_corpus"], comparable[0]["condition_order"])

    def test_crossover_requires_two_qualified_sizes_and_no_larger_reversal(self) -> None:
        comparisons = []
        for target, ratio in ((8_000, 0.9), (32_000, 0.8), (128_000, 0.7)):
            for repeat in range(1, 6):
                comparisons.append(
                    {
                        "route": "codex",
                        "target_corpus_estimated_tokens": target,
                        "actual_corpus_estimated_tokens": target + 100,
                        "case_id": "case",
                        "repeat": repeat,
                        "comparable": True,
                        "elm_value": int(ratio * 1_000),
                        "full_corpus_value": 1_000,
                        "elm_to_full_corpus_ratio": ratio,
                    }
                )
        aggregates = CURVE.aggregate_curve(comparisons, min_pairs=5, alpha=0.05)
        summary = CURVE.build_crossover_summary(aggregates, min_consecutive_sizes=2)

        self.assertTrue(all(item["claim_qualified"] for item in aggregates))
        self.assertTrue(summary[0]["benchmark_qualified_crossover"])
        self.assertEqual(8_000, summary[0]["crossover_target_corpus_estimated_tokens"])

        comparisons[-1]["elm_to_full_corpus_ratio"] = 1.2
        comparisons[-1]["elm_value"] = 1_200
        aggregates = CURVE.aggregate_curve(comparisons, min_pairs=5, alpha=0.05)
        summary = CURVE.build_crossover_summary(aggregates, min_consecutive_sizes=2)
        self.assertFalse(summary[0]["benchmark_qualified_crossover"])

    def test_incomplete_pairs_cannot_qualify_a_curve_cell(self) -> None:
        comparisons = [
            {
                "route": "codex",
                "target_corpus_estimated_tokens": 8_000,
                "actual_corpus_estimated_tokens": 8_100,
                "case_id": "case",
                "repeat": repeat,
                "comparable": repeat != 5,
                "elm_value": 500 if repeat != 5 else None,
                "full_corpus_value": 1_000 if repeat != 5 else None,
                "elm_to_full_corpus_ratio": 0.5 if repeat != 5 else None,
            }
            for repeat in range(1, 6)
        ]

        aggregate = CURVE.aggregate_curve(comparisons, min_pairs=5, alpha=0.05)[0]

        self.assertFalse(aggregate["all_pairs_comparable"])
        self.assertFalse(aggregate["claim_qualified"])

    def test_missing_planned_pairs_and_global_failure_suppress_crossover(self) -> None:
        comparisons = []
        for target in (8_000, 32_000):
            for repeat in range(1, 6):
                comparisons.append(
                    {
                        "route": "codex",
                        "target_corpus_estimated_tokens": target,
                        "actual_corpus_estimated_tokens": target + 100,
                        "case_id": "case",
                        "repeat": repeat,
                        "comparable": True,
                        "elm_value": 500,
                        "full_corpus_value": 1_000,
                        "elm_to_full_corpus_ratio": 0.5,
                    }
                )
        expected = {
            ("codex", target): {("case", repeat) for repeat in range(1, 7)}
            for target in (8_000, 32_000)
        }
        aggregates = CURVE.aggregate_curve(
            comparisons,
            min_pairs=5,
            alpha=0.05,
            expected_pairs=expected,
        )
        self.assertTrue(all(not item["claim_qualified"] for item in aggregates))
        self.assertTrue(
            all(not item["exact_planned_pair_population"] for item in aggregates)
        )

        complete_expected = {
            ("codex", target): {("case", repeat) for repeat in range(1, 6)}
            for target in (8_000, 32_000)
        }
        aggregates = CURVE.aggregate_curve(
            comparisons,
            min_pairs=5,
            alpha=0.05,
            expected_pairs=complete_expected,
        )
        summary = CURVE.build_crossover_summary(
            aggregates,
            min_consecutive_sizes=2,
            global_integrity_passed=False,
        )
        self.assertFalse(summary[0]["benchmark_qualified_crossover"])
        self.assertEqual("global_integrity_gate_failed", summary[0]["interpretation"])

    def test_aggregation_uses_integer_direction_before_display_rounding(self) -> None:
        comparisons = [
            {
                "route": "codex",
                "target_corpus_estimated_tokens": 8_000,
                "actual_corpus_estimated_tokens": 8_100,
                "case_id": "case",
                "repeat": repeat,
                "comparable": True,
                "elm_value": 9_999_996,
                "full_corpus_value": 10_000_000,
                "elm_to_full_corpus_ratio": 1.0,
            }
            for repeat in range(1, 6)
        ]
        expected = {("codex", 8_000): {("case", repeat) for repeat in range(1, 6)}}

        aggregate = CURVE.aggregate_curve(
            comparisons,
            min_pairs=5,
            alpha=0.05,
            expected_pairs=expected,
        )[0]

        self.assertEqual(5, aggregate["elm_below_full_corpus_count"])
        self.assertEqual(1, aggregate["one_sided_exact_sign_numerator"])
        self.assertEqual(32, aggregate["one_sided_exact_sign_denominator"])
        self.assertTrue(aggregate["claim_qualified"])

    def test_static_contract_passes(self) -> None:
        checks = CURVE.validate_static_contract(
            routes=("codex",),
            case_ids=("orion_storage",),
            targets=(2_000, 8_000),
            repeats=2,
        )

        self.assertTrue(all(checks.values()), checks)


if __name__ == "__main__":
    unittest.main()
