from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "benchmarks" / "run_heterogeneous_pilot.py"
SPEC = importlib.util.spec_from_file_location("run_heterogeneous_pilot", MODULE_PATH)
assert SPEC and SPEC.loader
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)
LEGACY_PATH = REPOSITORY_ROOT / "examples" / "two-agent-handoff" / "run_hosts.py"
LEGACY_SPEC = importlib.util.spec_from_file_location("legacy_run_hosts", LEGACY_PATH)
assert LEGACY_SPEC and LEGACY_SPEC.loader
LEGACY = importlib.util.module_from_spec(LEGACY_SPEC)
LEGACY_SPEC.loader.exec_module(LEGACY)


class HeterogeneousPilotContractTests(unittest.TestCase):
    def test_static_contract_is_closed_and_oracle_free(self) -> None:
        checks = PILOT.validate_static_contract()

        self.assertTrue(all(checks.values()), checks)

    def test_expected_answers_are_not_in_elm_or_no_memory_prompts(self) -> None:
        corpus = PILOT.active_corpus(PILOT.FIXTURE_ROOT)
        for case in PILOT.load_cases():
            for condition in ("elm", "no_memory"):
                prompt = PILOT.build_prompt(case, condition, corpus).casefold()
                self.assertNotIn(case["expected_answer"].casefold(), prompt)
                self.assertNotIn(case["expected_source_path"].casefold(), prompt)
                self.assertNotIn(case["expected_heading"].casefold(), prompt)

    def test_elm_prompt_uses_an_explicit_case_project_when_present(self) -> None:
        case = dict(PILOT.load_cases()[0], project="lighthouse")

        prompt = PILOT.build_prompt(case, "elm", "")

        self.assertIn("project='lighthouse'", prompt)
        self.assertNotIn("project='orion'", prompt)

    def test_case_project_rejects_prompt_shaped_slugs(self) -> None:
        case = dict(PILOT.load_cases()[0], project="orion' inject=true")

        with self.assertRaises(ValueError):
            PILOT.build_prompt(case, "elm", "")

    def test_full_corpus_excludes_backups_and_archives(self) -> None:
        corpus = PILOT.active_corpus(PILOT.FIXTURE_ROOT)

        self.assertIn("20_projects/orion/DECISIONS.md", corpus)
        self.assertNotIn("backups/", corpus)
        self.assertNotIn("99_archive/", corpus)

    def test_legacy_demo_schema_no_longer_leaks_answer_or_source(self) -> None:
        schema = json.loads(
            (REPOSITORY_ROOT / "examples" / "two-agent-handoff" / "result.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertNotIn("const", schema["properties"]["database"])
        self.assertNotIn("const", schema["properties"]["source_path"])
        self.assertNotIn("const", schema["properties"]["project"])

    def test_observed_response_is_reduced_to_the_closed_public_shape(self) -> None:
        observed = PILOT.sanitized_observed_response(
            {
                "answer": "UTC",
                "source_path": "20_projects/orion/DECISIONS.md",
                "section_key": None,
                "evidence_status": "provided",
                "conversation_id": "must-not-survive",
                "raw_transcript": "must-not-survive",
            }
        )

        self.assertEqual(
            {"answer", "source_path", "section_key", "evidence_status"},
            set(observed),
        )
        self.assertNotIn("must-not-survive", json.dumps(observed))

    def test_observed_response_rejects_non_relative_or_traversing_paths(self) -> None:
        for unsafe in (r"C:\private\memory.md", "../private/memory.md", "/private/memory.md"):
            with self.subTest(path=unsafe):
                observed = PILOT.sanitized_observed_response(
                    {
                        "answer": "UTC",
                        "source_path": unsafe,
                        "section_key": None,
                        "evidence_status": "provided",
                    }
                )
                self.assertIsNone(observed)

    def test_failed_quality_response_is_not_reportable(self) -> None:
        response = {
            "answer": "unexpected payload",
            "source_path": None,
            "section_key": None,
            "evidence_status": "insufficient",
        }

        self.assertIsNone(
            PILOT.reportable_observed_response(
                response,
                {
                    "schema_response_present": True,
                    "answer_correct": False,
                    "evidence_correct": True,
                },
            )
        )

    def test_codex_usage_parser_keeps_only_allowlisted_counters(self) -> None:
        stdout = "\n".join(
            (
                '{"type":"thread.started","thread_id":"secret-session"}',
                '{"type":"item.completed","item":{"type":"mcp_tool_call","server":"elm_benchmark","tool":"status"}}',
                '{"type":"turn.completed","usage":{"input_tokens":120,"cached_input_tokens":80,"output_tokens":9,"unknown":44}}',
            )
        )
        response, usage, audit = PILOT.parse_codex_output(
            stdout,
            '{"answer":"UTC","source_path":null,"section_key":null,"evidence_status":"provided"}',
        )

        self.assertEqual("UTC", response["answer"])
        self.assertEqual(120, usage["input_tokens"])
        self.assertEqual(80, usage["cached_input_tokens"])
        self.assertEqual(9, usage["output_tokens"])
        self.assertNotIn("unknown", usage)
        self.assertNotIn("secret-session", json.dumps(usage))
        self.assertEqual(["status"], audit["elm_tools"])

    def test_codex_error_items_are_not_misclassified_as_tool_calls(self) -> None:
        stdout = "\n".join(
            (
                '{"type":"item.completed","item":{"type":"error","message":"bounded diagnostic"}}',
                '{"type":"item.completed","item":{"type":"command_execution","command":"blocked"}}',
                '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
            )
        )

        _, _, audit = PILOT.parse_codex_output(
            stdout,
            '{"answer":"UTC","source_path":null,"section_key":null,"evidence_status":"provided"}',
        )

        self.assertEqual(1, audit["tool_call_count"])
        self.assertEqual(1, audit["non_mcp_tool_call_count"])
        self.assertEqual(["command_execution"], audit["unapproved_tool_names"])

    def test_codex_run_pins_requested_reasoning_effort(self) -> None:
        captured = {}
        original_which = PILOT.shutil.which
        original_run_process = PILOT.run_process

        def fake_run_process(command, **kwargs):
            captured["command"] = command
            output_index = command.index("--output-last-message") + 1
            Path(command[output_index]).write_text(
                '{"answer":"UTC","source_path":null,"section_key":null,"evidence_status":"provided"}',
                encoding="utf-8",
            )
            return PILOT.subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":12,\"output_tokens\":3}}\n',
                stderr="",
            )

        try:
            PILOT.shutil.which = lambda name: "codex" if name == "codex" else None
            PILOT.run_process = fake_run_process
            with PILOT.disposable_directory("elm-codex-reasoning-test-") as workspace:
                status, _, _, _, _ = PILOT.run_codex(
                    workspace=workspace,
                    root=workspace,
                    runtime=workspace,
                    prompt="prompt",
                    condition="full_corpus",
                    model="gpt-test",
                    reasoning_effort="low",
                    timeout=5.0,
                )
        finally:
            PILOT.shutil.which = original_which
            PILOT.run_process = original_run_process

        self.assertEqual("completed", status)
        config_index = captured["command"].index("--config") + 1
        self.assertEqual(
            'model_reasoning_effort="low"', captured["command"][config_index]
        )

    def test_antigravity_and_claude_usage_remain_provider_native(self) -> None:
        antigravity = "\n".join(
            (
                json.dumps(
                    {
                        "event": "step_update",
                        "step_update": {
                            "step_index": 1,
                            "state": "DONE",
                            "step_type": "agent_response",
                        },
                    }
                ),
                json.dumps(
                    {
                        "event": "result",
                        "result": {
                            "conversation_id": "not-retained",
                            "structured_output": {
                                "answer": "UTC",
                                "source_path": None,
                                "section_key": None,
                                "evidence_status": "provided",
                            },
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 30,
                                "thinking_tokens": 20,
                                "cache_read_tokens": 80,
                                "total_tokens": 130,
                            },
                        },
                    }
                ),
            )
        )
        _, agy_usage, agy_audit = PILOT.parse_antigravity_output(
            antigravity, "google-gemini-antigravity"
        )
        claude = json.dumps(
            {
                "session_id": "not-retained",
                "structured_output": {
                    "answer": "UTC",
                    "source_path": None,
                    "section_key": None,
                    "evidence_status": "provided",
                },
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 7,
                    "output_tokens": 4,
                },
                "total_cost_usd": 0.0123,
            }
        )
        _, claude_usage, claude_audit = PILOT.parse_claude_output(claude)

        self.assertEqual(130, agy_usage["total_tokens"])
        self.assertFalse(agy_usage["cross_provider_comparable"])
        self.assertEqual(0.0123, claude_usage["cost_usd"])
        self.assertFalse(claude_usage["cross_provider_comparable"])
        self.assertNotIn("conversation_id", agy_usage)
        self.assertNotIn("session_id", claude_usage)
        self.assertTrue(PILOT.tool_audit_passes("no_memory", agy_audit))
        self.assertFalse(PILOT.tool_audit_passes("no_memory", claude_audit))

    def test_usage_requires_exact_integers_and_route_complete_counters(self) -> None:
        usage = PILOT.sanitized_usage(
            "provider",
            {"input_tokens": 12.9, "output_tokens": 3, "total_tokens": 15},
        )

        self.assertNotIn("input_tokens", usage)
        self.assertFalse(PILOT.usage_complete_for_route("gemini-antigravity", usage))

    def test_antigravity_broker_reads_and_exact_mcp_calls_are_auditable(self) -> None:
        internal_path = (
            Path.home()
            / ".gemini"
            / "antigravity-cli"
            / "mcp"
            / PILOT.MCP_SERVER_NAME
            / "status.json"
        )
        events = []
        for index, tool in enumerate(("status", "context"), start=1):
            events.append(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": index,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "call_mcp_tool",
                        "tool_info": {
                            "name": "call_mcp_tool",
                            "parameters": {
                                "ServerName": PILOT.MCP_SERVER_NAME,
                                "ToolName": tool,
                            },
                        },
                    },
                }
            )
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 3,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "view_file",
                    "tool_info": {
                        "name": "view_file",
                        "parameters": {"AbsolutePath": str(internal_path)},
                    },
                },
            }
        )
        events.append(
            {
                "event": "result",
                "result": {
                    "structured_output": {
                        "answer": "UTC",
                        "source_path": "20_projects/orion/DECISIONS.md",
                        "section_key": None,
                        "evidence_status": "retrieved",
                    },
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                },
            }
        )

        _, _, audit = PILOT.parse_antigravity_output(
            "\n".join(json.dumps(event) for event in events),
            "google-gemini-antigravity",
        )

        self.assertTrue(PILOT.tool_audit_passes("elm", audit))
        self.assertEqual(1, audit["broker_internal_read_count"])
        self.assertEqual([], audit["unapproved_tool_names"])

    def test_legacy_provider_errors_collapse_to_bounded_categories(self) -> None:
        raw = "conversation_id=secret Individual quota reached"

        category = LEGACY.provider_failure_category(raw, "provider diagnostic")
        error = LEGACY.HostRunError(category)

        self.assertEqual("quota_exhausted", str(error))
        self.assertNotIn("secret", str(error))

    def test_version_output_must_be_one_bounded_safe_line(self) -> None:
        raw = "version 1.2.3\nconversation_id=secret"
        completed = PILOT.subprocess.CompletedProcess(
            args=["tool", "--version"],
            returncode=0,
            stdout=raw,
            stderr="",
        )
        pilot_original = PILOT.run_process
        legacy_original = LEGACY.run_process
        which_original = PILOT.shutil.which
        try:
            PILOT.shutil.which = lambda command: command
            PILOT.run_process = lambda *args, **kwargs: completed
            LEGACY.run_process = lambda *args, **kwargs: completed
            self.assertIsNone(PILOT.command_version("tool"))
            self.assertIsNone(LEGACY.command_version("tool"))
        finally:
            PILOT.run_process = pilot_original
            LEGACY.run_process = legacy_original
            PILOT.shutil.which = which_original

    def test_auth_and_permission_failures_are_classified_without_returning_raw_text(self) -> None:
        self.assertEqual(
            "auth_failed",
            PILOT.failure_category(
                executable_found=True,
                stdout="Failed to authenticate: OAuth session expired",
            ),
        )
        self.assertEqual(
            "permission_denied",
            PILOT.failure_category(
                executable_found=True,
                stderr='a tool required the "mcp" permission that headless mode cannot prompt for',
            ),
        )
        self.assertEqual(
            "quota_exhausted",
            PILOT.failure_category(
                executable_found=True,
                stdout="Individual quota reached. Resets later.",
            ),
        )
        self.assertEqual("unavailable", PILOT.failure_category(executable_found=False))

    def test_evaluation_has_condition_specific_evidence_gates(self) -> None:
        case = PILOT.load_cases()[0]
        section_key = "section_11111111-1111-4111-8111-111111111111"
        elm = PILOT.evaluate_response(
            {
                "answer": case["expected_answer"],
                "source_path": case["expected_source_path"],
                "section_key": section_key,
                "evidence_status": "retrieved",
            },
            case=case,
            condition="elm",
            section_key=section_key,
        )
        no_memory = PILOT.evaluate_response(
            {
                "answer": PILOT.INSUFFICIENT,
                "source_path": None,
                "section_key": None,
                "evidence_status": "insufficient",
            },
            case=case,
            condition="no_memory",
            section_key=section_key,
        )

        self.assertTrue(all(elm.values()))
        self.assertTrue(all(no_memory.values()))

    def test_evaluation_normalizes_windows_source_separators(self) -> None:
        case = PILOT.load_cases()[0]
        checks = PILOT.evaluate_response(
            {
                "answer": case["expected_answer"],
                "source_path": case["expected_source_path"].replace("/", "\\"),
                "section_key": None,
                "evidence_status": "provided",
            },
            case=case,
            condition="full_corpus",
            section_key="section_11111111-1111-4111-8111-111111111111",
        )

        self.assertTrue(all(checks.values()))

    def test_evaluation_requires_the_verbatim_supporting_sentence(self) -> None:
        case = next(item for item in PILOT.load_cases() if item["id"] == "orion_logs")
        section_key = "section_11111111-1111-4111-8111-111111111111"

        def answer_passes(answer: str) -> bool:
            return PILOT.evaluate_response(
                {
                    "answer": answer,
                    "source_path": case["expected_source_path"],
                    "section_key": section_key,
                    "evidence_status": "retrieved",
                },
                case=case,
                condition="elm",
                section_key=section_key,
            )["answer_correct"]

        self.assertTrue(answer_passes(case["expected_answer"]))
        self.assertFalse(
            answer_passes("newline-delimited JSON with stable event names")
        )
        self.assertFalse(answer_passes(case["expected_answer"] + " Use this format."))

    def test_model_selection_is_explicit_and_prefix_bounded(self) -> None:
        available = ["gemini-3.7-flash-high", "claude-sonnet-4-6"]

        self.assertEqual(
            "claude-sonnet-4-6",
            PILOT.select_model(available, None, ("claude-sonnet-", "claude-opus-")),
        )
        self.assertIsNone(PILOT.select_model(available, "claude-opus-missing", ("claude-",)))

    def test_comparisons_are_paired_within_one_route_and_case(self) -> None:
        def run(route: str, condition: str, tokens: int) -> dict:
            return {
                "route": route,
                "case_id": "case",
                "condition": condition,
                "passed": True,
                "usage": {"availability": "reported", "total_tokens": tokens},
            }

        comparisons = PILOT.build_comparisons(
            [
                run("gemini-antigravity", "elm", 120),
                run("gemini-antigravity", "full_corpus", 100),
                run("claude-antigravity", "elm", 90),
            ]
        )

        gemini = next(item for item in comparisons if item["route"] == "gemini-antigravity")
        claude = next(item for item in comparisons if item["route"] == "claude-antigravity")
        self.assertTrue(gemini["comparable"])
        self.assertEqual(20, gemini["elm_minus_full_corpus"])
        self.assertFalse(claude["comparable"])


if __name__ == "__main__":
    unittest.main()
