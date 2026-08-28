from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from _bootstrap import FixtureCopy, run_cli, run_cli_process, run_cli_stdin

try:
    from mcp import Client
except ModuleNotFoundError:  # The MCP adapter is an optional installation extra.
    Client = None

from elm_memory.governance import (
    AgentMemoryLifecyclePolicy,
    AgentMemoryLimits,
    ProposalLimits,
    parse_claim,
    remember_memory_bundle,
    render_claim,
    submit_proposal_bundle,
)
from elm_memory import mcp_server as mcp_module
from elm_memory.mcp_server import (
    AutonomousMemoryPolicy,
    ProposalServerPolicy,
    create_server,
)


READ_TOOLS = {"search", "context", "read", "related", "history", "stats", "status"}
AUTONOMOUS_TOOLS = READ_TOOLS | {"remember_memory"}


def initialize(root: Path) -> None:
    run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
    run_cli(root, "rebuild")


def request(
    *,
    submission_id: str = "submission_11111111-1111-4111-8111-111111111111",
    object_value: str = "PostgreSQL 18",
    valid_from: str = "2026-08-27T00:00:00Z",
    valid_to: str | None = None,
) -> dict:
    value = {
        "submission_id": submission_id,
        "project": "orion",
        "subject": "Aurora",
        "predicate": "uses",
        "object": object_value,
        "valid_from": valid_from,
        "sensitivity": "normal",
        "rationale": "Durable agent observation with bounded provenance.",
        "source_refs": [],
        "evidence": [],
    }
    if valid_to is not None:
        value["valid_to"] = valid_to
    return value


def remember_cli(root: Path, value: dict, *extra: str):
    return run_cli_stdin(
        root,
        json.dumps(value, ensure_ascii=False),
        "remember-submit",
        "--request-stdin",
        "--allow-project",
        "orion",
        *extra,
    )


def autonomous_server(
    root: Path,
    *,
    memory_limits: AgentMemoryLimits = AgentMemoryLimits(),
    max_requests_per_minute: int = 30,
):
    return create_server(
        root,
        mutation_mode="autonomous",
        autonomous_policy=AutonomousMemoryPolicy(
            allowed_projects=frozenset({"orion"}),
            proposal_limits=ProposalLimits(),
            memory_limits=memory_limits,
            max_requests_per_minute=max_requests_per_minute,
        ),
    )


class AutonomousMemoryCLITests(unittest.TestCase):
    def test_remember_is_idempotent_active_and_rebuildable(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first_process = remember_cli(root, request())
            replay_process = remember_cli(root, request())
            first = json.loads(first_process.stdout)
            replay = json.loads(replay_process.stdout)
            context = run_cli(
                root,
                "context",
                "Aurora uses PostgreSQL 18",
                "--project",
                "orion",
                "--budget",
                "900",
                "--no-sync",
                "--no-trace",
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            rebuilt = run_cli(root, "rebuild")
            strict = run_cli(
                root,
                "search",
                "Aurora PostgreSQL 18",
                "--project",
                "orion",
                "--no-sync",
            )
            exact = run_cli(root, "read", strict["results"][0]["section_key"])
            outline = run_cli(root, "outline", first["claim_id"], "--no-sync")
            related = run_cli(root, "related", first["claim_id"], "--no-sync")

        self.assertEqual(0, first_process.returncode, first_process.stderr)
        self.assertEqual(0, replay_process.returncode, replay_process.stderr)
        self.assertEqual("remembered", first["action"])
        self.assertTrue(first["candidate_activated"])
        self.assertEqual("agent_curated", first["authority"])
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(first["projection"]["healthy"])
        self.assertTrue(first["projection"]["governance_projection_current"])
        self.assertEqual(
            first["projection"]["governance_projection_sha256"],
            first["projection"]["canonical_governance_sha256"],
        )
        self.assertFalse(first["projection"]["linearizable_postcondition"])
        self.assertEqual(first["claim_id"], replay["claim_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual(1, len(history["proposals"]))
        self.assertEqual(1, len(history["events"]))
        self.assertEqual("agent_curated_memory", context["sources"][0]["authority"])
        self.assertEqual([], rebuilt["errors"])
        self.assertEqual("agent_curated", strict["results"][0]["claim_authority"])
        self.assertEqual(first["claim_id"], exact["claim_id"])
        self.assertEqual("agent_curated", exact["claim_authority"])
        self.assertEqual("agent_curated_memory", exact["authority"])
        self.assertEqual("untrusted_memory_data", exact["content_role"])
        self.assertEqual("agent_curated", outline["document"]["claim_authority"])
        self.assertEqual("agent_curated_memory", outline["document"]["authority"])
        self.assertEqual("agent_curated", related["document"]["claim_authority"])
        self.assertEqual("agent_curated_memory", related["document"]["authority"])

    def test_conflict_is_deferred_without_changing_active_memory(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = json.loads(remember_cli(root, request()).stdout)
            conflicting = json.loads(remember_cli(
                root,
                request(
                    submission_id="submission_22222222-2222-4222-8222-222222222222",
                    object_value="SQLite 4",
                ),
            ).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            proposals = run_cli(root, "proposals", "list", "--project", "orion", "--no-sync")
            doctor = run_cli(root, "doctor", "--no-sync")

        self.assertEqual("remembered", first["action"])
        self.assertEqual("conflict_deferred", conflicting["action"])
        self.assertFalse(conflicting["candidate_activated"])
        self.assertTrue(conflicting["conflict_detected"])
        self.assertEqual(first["claim_id"], conflicting["existing_claim_id"])
        self.assertEqual(1, len(history["claims"]))
        deferred_event = next(
            item for item in history["events"] if item["reason_code"] == "contradicted"
        )
        self.assertEqual(first["claim_id"], deferred_event["target_id"])
        self.assertEqual(["accepted", "deferred"], sorted(item["status"] for item in proposals["proposals"]))
        self.assertEqual(0, doctor["issue_count"])

    def test_replay_after_dispute_reports_inactive_history(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            remembered = json.loads(remember_cli(root, request()).stdout)
            run_cli(
                root,
                "dispute",
                remembered["claim_id"],
                "--actor",
                "reviewer:test",
                "--reason-code",
                "contradicted",
            )
            replay = json.loads(remember_cli(root, request()).stdout)

        self.assertEqual("inactive_terminal", replay["action"])
        self.assertFalse(replay["candidate_activated"])
        self.assertEqual("disputed", replay["terminal_status"])
        self.assertEqual("untrusted_memory_history", replay["content_role"])
        self.assertIn("not current active memory", replay["authority_warning"])

    def test_manual_acceptance_of_same_proposal_is_not_attributed_to_autonomy(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            proposed = json.loads(run_cli_stdin(
                root,
                json.dumps(value),
                "proposal-submit",
                "--request-stdin",
                "--allow-project",
                "orion",
            ).stdout)
            accepted = run_cli(
                root,
                "accept",
                proposed["proposal_id"],
                "--actor",
                "human:test",
                "--authority",
                "verified_repository_state",
            )
            replay = json.loads(remember_cli(root, value).stdout)

        self.assertEqual("existing_governed_memory", replay["action"])
        self.assertFalse(replay["candidate_activated"])
        self.assertEqual(accepted["claim_id"], replay["existing_claim_id"])
        self.assertEqual("verified_repository_state", replay["existing_authority"])
        self.assertEqual("governed_memory_reference", replay["content_role"])

    def test_future_candidate_is_deferred_and_replays_stably(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = {**request(), "valid_from": "2030-01-01T00:00:00Z"}
            first = json.loads(remember_cli(root, value).stdout)
            replay = json.loads(remember_cli(root, value).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual("future_deferred", first["action"])
        self.assertFalse(first["candidate_activated"])
        self.assertEqual("future_deferred", replay["action"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual([], history["claims"])

    def test_manual_reject_and_defer_replay_as_nonactive_terminal_outcomes(self) -> None:
        cases = (
            ("reject", "duplicate", "terminal_rejected"),
            ("defer", "insufficient_evidence", "terminal_deferred"),
        )
        for ordinal, (transition, reason, expected_action) in enumerate(cases, start=1):
            with self.subTest(transition=transition), FixtureCopy() as root:
                initialize(root)
                value = request(
                    submission_id=(
                        f"submission_bbbbbbbb-bbbb-4bbb-8bb{ordinal}-bbbbbbbbbbb{ordinal}"
                    )
                )
                proposed = json.loads(run_cli_stdin(
                    root,
                    json.dumps(value),
                    "proposal-submit",
                    "--request-stdin",
                    "--allow-project",
                    "orion",
                ).stdout)
                run_cli(
                    root,
                    transition,
                    proposed["proposal_id"],
                    "--actor",
                    "human:test",
                    "--reason-code",
                    reason,
                )
                replay = json.loads(remember_cli(root, value).stdout)

            self.assertEqual(expected_action, replay["action"])
            self.assertFalse(replay["candidate_activated"])
            self.assertEqual("untrusted_memory_candidate", replay["content_role"])

    def test_replay_after_supersession_does_not_report_old_claim_active(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            remembered = json.loads(remember_cli(root, request()).stdout)
            replacement = run_cli(
                root,
                "propose",
                "--project",
                "orion",
                "--subject",
                "Aurora",
                "--predicate",
                "uses",
                "--object",
                "PostgreSQL 19",
                "--actor",
                "human:test",
            )
            run_cli(
                root,
                "supersede",
                remembered["claim_id"],
                replacement["proposal_id"],
                "--actor",
                "human:test",
                "--authority",
                "verified_repository_state",
            )
            replay = json.loads(remember_cli(root, request()).stdout)

        self.assertEqual("inactive_terminal", replay["action"])
        self.assertFalse(replay["candidate_activated"])
        self.assertEqual("superseded", replay["terminal_status"])

    def test_replay_after_expiry_does_not_report_active_success(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            remembered = json.loads(remember_cli(root, request()).stdout)
            claim_path = (
                root
                / "20_projects"
                / "orion"
                / "CLAIMS"
                / f"{remembered['claim_id']}.md"
            )
            claim = parse_claim(claim_path)
            claim["valid_to"] = "2026-08-27T01:00:00.000000+00:00"
            claim_path.write_bytes(render_claim(claim))
            replay = json.loads(remember_cli(root, request()).stdout)

        self.assertEqual("inactive_terminal", replay["action"])
        self.assertFalse(replay["candidate_activated"])
        self.assertEqual("expired", replay["terminal_status"])

    def test_exact_duplicate_reuses_stronger_existing_claim(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            proposal = run_cli(
                root,
                "propose",
                "--project",
                "orion",
                "--subject",
                "Aurora",
                "--predicate",
                "uses",
                "--object",
                "PostgreSQL 18",
                "--actor",
                "human:test",
            )
            accepted = run_cli(
                root,
                "accept",
                proposal["proposal_id"],
                "--actor",
                "human:test",
                "--authority",
                "user_ratified",
            )
            duplicate = json.loads(remember_cli(root, request()).stdout)
            duplicate_replay = json.loads(remember_cli(root, request()).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            stronger_search = run_cli(root, "search", "PostgreSQL 18", "--no-sync")
            stronger_read = run_cli(
                root,
                "read",
                stronger_search["results"][0]["section_key"],
            )

        self.assertEqual("duplicate", duplicate["action"])
        self.assertFalse(duplicate["candidate_activated"])
        self.assertEqual(accepted["claim_id"], duplicate["existing_claim_id"])
        self.assertEqual("user_ratified", duplicate["existing_authority"])
        self.assertTrue(duplicate_replay["idempotent_replay"])
        self.assertEqual(accepted["claim_id"], duplicate_replay["existing_claim_id"])
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual("user_ratified", stronger_read["claim_authority"])
        self.assertEqual("user_ratified_memory", stronger_read["authority"])
        self.assertEqual("untrusted_memory_data", stronger_read["content_role"])
        duplicate_event = next(
            item for item in history["events"] if item["reason_code"] == "duplicate"
        )
        self.assertEqual(accepted["claim_id"], duplicate_event["target_id"])

    def test_mixed_stronger_and_agent_memory_preserves_precedence_and_labels(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            proposal = run_cli(
                root,
                "propose",
                "--project",
                "orion",
                "--subject",
                "AuroraStrong",
                "--predicate",
                "uses",
                "--object",
                "PostgreSQL",
                "--actor",
                "human:test",
            )
            accepted = run_cli(
                root,
                "accept",
                proposal["proposal_id"],
                "--actor",
                "human:test",
                "--authority",
                "verified_repository_state",
            )
            agent_value = {
                **request(
                    submission_id="submission_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    object_value="PostgreSQL",
                ),
                "subject": "AuroraAgent",
            }
            agent = json.loads(remember_cli(root, agent_value).stdout)
            searched = run_cli(root, "search", "PostgreSQL", "--project", "orion", "--no-sync")
            packet = run_cli(
                root,
                "context",
                "PostgreSQL",
                "--project",
                "orion",
                "--budget",
                "1400",
                "--no-sync",
                "--no-trace",
            )

        claim_results = [item for item in searched["results"] if item.get("claim_id")]
        self.assertEqual(accepted["claim_id"], claim_results[0]["claim_id"])
        self.assertEqual(agent["claim_id"], claim_results[1]["claim_id"])
        self.assertEqual("verified_repository_memory", claim_results[0]["authority"])
        self.assertEqual("agent_curated_memory", claim_results[1]["authority"])
        packet_authorities = {item["authority"] for item in packet["sources"]}
        self.assertIn("accepted_project_memory", packet_authorities)
        self.assertIn("agent_curated_memory", packet_authorities)

    def test_related_link_rows_preserve_governed_source_and_target_provenance(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = json.loads(remember_cli(root, request()).stdout)
            second_value = {
                **request(
                    submission_id="submission_ffffffff-ffff-4fff-8fff-ffffffffffff",
                    object_value="TLS 1.3",
                ),
                "subject": "Gateway",
                "predicate": "requires",
                "rationale": f"[Prior governed claim]({first['claim_id']}.md)",
            }
            second = json.loads(remember_cli(root, second_value).stdout)
            from_second = run_cli(root, "related", second["claim_id"], "--no-sync")
            to_first = run_cli(root, "related", first["claim_id"], "--no-sync")

        governed_target = next(
            item for item in from_second["outgoing"] if item.get("target_claim_id")
        )
        governed_source = next(
            item for item in to_first["incoming"] if item.get("source_claim_id")
        )
        self.assertEqual(first["claim_id"], governed_target["target_claim_id"])
        self.assertEqual("agent_curated", governed_target["target_claim_authority"])
        self.assertEqual("agent_curated_memory", governed_target["target_authority"])
        self.assertEqual("untrusted_memory_data", governed_target["target_content_role"])
        self.assertEqual(second["claim_id"], governed_source["source_claim_id"])
        self.assertEqual("agent_curated", governed_source["source_claim_authority"])
        self.assertEqual("agent_curated_memory", governed_source["source_authority"])
        self.assertEqual("untrusted_memory_data", governed_source["source_content_role"])

    def test_active_memory_quota_fails_before_second_activation(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = remember_cli(root, request(), "--max-active-per-project", "1")
            second = remember_cli(
                root,
                {
                    **request(
                        submission_id="submission_33333333-3333-4333-8333-333333333333",
                        object_value="TLS 1.3",
                    ),
                    "subject": "Gateway",
                    "predicate": "requires",
                },
                "--max-active-per-project",
                "1",
            )
            second_result = json.loads(second.stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual("quota_deferred", second_result["action"])
        self.assertTrue(second_result["quota_exceeded"])
        self.assertIn("active agent-memory project quota exceeded", second_result["quota_message"])
        self.assertEqual(1, len(history["claims"]))

    def test_autonomous_submit_rejects_restricted_memory(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            value["sensitivity"] = "restricted"
            denied = remember_cli(root, value)
            proposals = list(root.rglob("proposal_*.json"))

        self.assertEqual(2, denied.returncode)
        self.assertIn("normal-sensitivity", denied.stderr)
        self.assertEqual([], proposals)

    def test_changed_payload_replay_is_rejected_without_a_second_claim(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = remember_cli(root, request())
            changed = remember_cli(root, request(object_value="SQLite 4"))
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(2, changed.returncode)
        self.assertIn("different normalized payload", changed.stderr)
        self.assertEqual(1, len(history["claims"]))

    def test_manual_accept_cannot_impersonate_agent_curated_authority(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            proposal = run_cli(
                root,
                "propose",
                "--project",
                "orion",
                "--subject",
                "Aurora",
                "--predicate",
                "uses",
                "--object",
                "PostgreSQL 18",
                "--actor",
                "agent:test",
            )
            denied = run_cli_process(
                root,
                "accept",
                proposal["proposal_id"],
                "--actor",
                "agent:test",
                "--authority",
                "agent_curated",
            )

        self.assertEqual(2, denied.returncode)
        self.assertIn("invalid choice", denied.stderr)

    def test_retry_resumes_a_pending_proposal_after_an_interrupted_two_stage_write(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            proposed = run_cli_stdin(
                root,
                json.dumps(value),
                "proposal-submit",
                "--request-stdin",
                "--allow-project",
                "orion",
            )
            resumed = json.loads(remember_cli(root, value).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual(0, proposed.returncode, proposed.stderr)
        self.assertEqual("remembered", resumed["action"])
        self.assertTrue(resumed["idempotent_replay"])
        self.assertEqual(json.loads(proposed.stdout)["proposal_id"], resumed["proposal_id"])
        self.assertEqual(1, len(history["claims"]))

    def test_concurrent_same_submission_activates_once(self) -> None:
        with FixtureCopy() as root:
            initialize(root)

            def submit() -> dict:
                completed = remember_cli(root, request())
                self.assertEqual(0, completed.returncode, completed.stderr)
                return json.loads(completed.stdout)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: submit(), range(2)))
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            doctor = run_cli(root, "doctor", "--no-sync")

        self.assertEqual(1, len({item["claim_id"] for item in results}))
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual(1, len(history["events"]))
        self.assertEqual(0, doctor["issue_count"])

    def test_concurrent_one_slot_quota_activates_exactly_one_claim(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            values = [
                request(),
                {
                    **request(
                        submission_id="submission_77777777-7777-4777-8777-777777777777",
                        object_value="TLS 1.3",
                    ),
                    "subject": "Gateway",
                    "predicate": "requires",
                },
            ]

            def submit(value: dict) -> dict:
                completed = remember_cli(
                    root,
                    value,
                    "--max-active-per-project",
                    "1",
                    "--max-active-root",
                    "1",
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                return json.loads(completed.stdout)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit, values))
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual({"quota_deferred", "remembered"}, {item["action"] for item in results})
        self.assertEqual(1, len(history["claims"]))

    def test_root_quota_applies_across_projects(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = remember_cli(
                root,
                request(),
                "--max-active-per-project",
                "1",
                "--max-active-root",
                "1",
            )
            second_value = {
                **request(
                    submission_id="submission_88888888-8888-4888-8888-888888888888",
                    object_value="BeaconDB",
                ),
                "project": "lighthouse",
                "subject": "Beacon",
            }
            second = run_cli_stdin(
                root,
                json.dumps(second_value),
                "remember-submit",
                "--request-stdin",
                "--allow-project",
                "orion",
                "--allow-project",
                "lighthouse",
                "--max-active-per-project",
                "1",
                "--max-active-root",
                "1",
            )

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual("quota_deferred", json.loads(second.stdout)["action"])

    def test_concurrent_conflicting_submissions_leave_one_active_claim(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            values = [
                request(),
                request(
                    submission_id="submission_66666666-6666-4666-8666-666666666666",
                    object_value="SQLite 4",
                ),
            ]

            def submit(value: dict) -> dict:
                completed = remember_cli(root, value)
                self.assertEqual(0, completed.returncode, completed.stderr)
                return json.loads(completed.stdout)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit, values))
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            doctor = run_cli(root, "doctor", "--no-sync")

        self.assertEqual({"conflict_deferred", "remembered"}, {item["action"] for item in results})
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual(0, doctor["issue_count"])


    def test_default_validity_lease_is_digest_bound_and_rebuildable(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            remembered = json.loads(remember_cli(root, request()).stdout)
            changed_replay = remember_cli(
                root,
                request(valid_to="2026-12-01T00:00:00Z"),
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            rebuilt = run_cli(root, "rebuild")
            after = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual("2026-11-25T00:00:00.000000+00:00", remembered["valid_to"])
        self.assertEqual(2, changed_replay.returncode)
        self.assertIn("different normalized payload", changed_replay.stderr)
        self.assertEqual(3, history["proposals"][0]["format_version"])
        self.assertEqual(remembered["valid_to"], history["proposals"][0]["valid_to"])
        self.assertEqual(remembered["valid_to"], history["claims"][0]["valid_to"])
        self.assertEqual([], rebuilt["errors"])
        self.assertEqual(remembered["valid_to"], after["claims"][0]["valid_to"])

    def test_tampered_v3_validity_fails_closed(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            remembered = json.loads(remember_cli(root, request()).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")
            proposal = history["proposals"][0]
            path = root / proposal["path"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["valid_to"] = "2026-12-01T00:00:00.000000+00:00"
            path.write_text(
                json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            refused = run_cli_process(
                root,
                "history",
                "--project",
                "orion",
                "--no-sync",
            )

        self.assertTrue(remembered["candidate_activated"])
        self.assertEqual(2, refused.returncode)
        self.assertIn("Proposal-v3 payload digest mismatch", refused.stderr)

    def test_explicit_validity_is_bounded_before_canonical_mutation(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            accepted = json.loads(remember_cli(
                root,
                request(valid_to="2026-08-29T00:00:00Z"),
            ).stdout)

        self.assertEqual("2026-08-29T00:00:00.000000+00:00", accepted["valid_to"])

        with FixtureCopy() as root:
            initialize(root)
            refused = remember_cli(
                root,
                request(valid_to="2027-08-28T00:00:00Z"),
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual(2, refused.returncode)
        self.assertIn("validity cannot exceed 365 days", refused.stderr)
        self.assertEqual([], history["proposals"])
        self.assertEqual([], history["claims"])

    def test_invalid_or_overflowing_validity_fails_before_canonical_mutation(self) -> None:
        invalid_values = (
            request(valid_to=""),
            request(valid_from="9999-12-31T00:00:00Z"),
        )
        expected_errors = (
            "valid_to must be a non-empty timezone-aware JSON string or null",
            "default agent-memory validity exceeds the ISO-8601 timestamp range",
        )
        for value, expected_error in zip(invalid_values, expected_errors, strict=True):
            with self.subTest(expected_error=expected_error), FixtureCopy() as root:
                initialize(root)
                refused = remember_cli(root, value)
                history = run_cli(root, "history", "--project", "orion", "--no-sync")

            self.assertEqual(2, refused.returncode)
            self.assertIn(expected_error, refused.stderr)
            self.assertEqual([], history["proposals"])
            self.assertEqual([], history["claims"])

    def test_already_expired_candidate_defers_and_replays_stably(self) -> None:
        value = request(valid_to="2026-08-27T00:01:00Z")
        with FixtureCopy() as root:
            initialize(root)
            first = json.loads(remember_cli(root, value).stdout)
            replay = json.loads(remember_cli(root, value).stdout)
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual("expired_deferred", first["action"])
        self.assertFalse(first["candidate_activated"])
        self.assertEqual("expired_deferred", replay["action"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual([], history["claims"])
        self.assertEqual(["deferred"], [item["status"] for item in history["proposals"]])

    def test_expired_lease_frees_quota_and_allows_same_key_renewal(self) -> None:
        limits = AgentMemoryLimits(max_active_per_project=1, max_active_root=1)
        lifecycle = AgentMemoryLifecyclePolicy(default_ttl_days=1, max_ttl_days=7)
        first_request = request(valid_to="2026-08-27T00:30:00Z")
        renewed_request = request(
            submission_id="submission_22222222-2222-4222-8222-222222222222",
            valid_from="2026-08-27T01:00:00Z",
            valid_to="2026-08-27T02:00:00Z",
        )
        with FixtureCopy() as root:
            initialize(root)
            with patch(
                "elm_memory.governance.utc_now",
                return_value="2026-08-27T00:00:00.000000+00:00",
            ):
                first = remember_memory_bundle(
                    root,
                    request=first_request,
                    request_bytes=len(json.dumps(first_request).encode("utf-8")),
                    allowed_projects={"orion"},
                    proposal_limits=ProposalLimits(),
                    memory_limits=limits,
                    lifecycle_policy=lifecycle,
                    lock_timeout=10.0,
                    recover_stale=False,
                )
            with patch(
                "elm_memory.governance.utc_now",
                return_value="2026-08-27T01:00:00.000000+00:00",
            ):
                renewed = remember_memory_bundle(
                    root,
                    request=renewed_request,
                    request_bytes=len(json.dumps(renewed_request).encode("utf-8")),
                    allowed_projects={"orion"},
                    proposal_limits=ProposalLimits(),
                    memory_limits=limits,
                    lifecycle_policy=lifecycle,
                    lock_timeout=10.0,
                    recover_stale=False,
                )
            run_cli(root, "sync")
            ordinary = run_cli(
                root,
                "search",
                "Aurora PostgreSQL 18",
                "--project",
                "orion",
                "--no-sync",
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertTrue(first["candidate_activated"])
        self.assertTrue(renewed["candidate_activated"])
        self.assertNotEqual(first["claim_id"], renewed["claim_id"])
        self.assertFalse(renewed.get("quota_exceeded", False))
        self.assertEqual(0, ordinary["count"])
        self.assertEqual(2, len(history["claims"]))


@unittest.skipUnless(Client is not None, "install elm-memory[mcp] to run MCP tests")
class AutonomousMemoryMCPTests(unittest.TestCase):
    def test_exact_eight_tool_surface_remembers_without_human_gate(self) -> None:
        async def run(root: Path):
            async with Client(autonomous_server(root), raise_exceptions=True) as client:
                tools = await client.list_tools()
                remembered = await client.call_tool("remember_memory", {
                    "submission_id": "submission_44444444-4444-4444-8444-444444444444",
                    "project": "orion",
                    "subject": "Aurora",
                    "predicate": "uses",
                    "object": "PostgreSQL 18",
                    "valid_from": "2026-08-27T00:00:00Z",
                    "rationale": "Autonomously curated local memory.",
                    "source_refs": [],
                    "evidence": [],
                })
                status = await client.call_tool("status", {})
                denied = await client.call_tool("remember_memory", {
                    "submission_id": "submission_55555555-5555-4555-8555-555555555555",
                    "project": "lighthouse",
                    "subject": "Beacon",
                    "predicate": "uses",
                    "object": "OtherDB",
                    "valid_from": "2026-08-27T00:00:00Z",
                    "source_refs": [],
                    "evidence": [],
                })
                return tools, remembered, status, denied

        with FixtureCopy() as root:
            initialize(root)
            tools, remembered, status, denied = asyncio.run(run(root))

        self.assertEqual(AUTONOMOUS_TOOLS, {tool.name for tool in tools.tools})
        remember_tool = next(tool for tool in tools.tools if tool.name == "remember_memory")
        self.assertFalse(remember_tool.annotations.read_only_hint)
        self.assertFalse(remember_tool.annotations.destructive_hint)
        self.assertTrue(remember_tool.annotations.idempotent_hint)
        self.assertFalse(remembered.is_error)
        self.assertTrue(remembered.structured_content["candidate_activated"])
        self.assertEqual("agent_curated", remembered.structured_content["authority"])
        self.assertEqual(
            "2026-11-25T00:00:00.000000+00:00",
            remembered.structured_content["valid_to"],
        )
        self.assertEqual("autonomous", status.structured_content["mutation_mode"])
        self.assertTrue(status.structured_content["active_agent_memory_write_available"])
        self.assertTrue(status.structured_content["accepted_state_mutation_available"])
        self.assertEqual(90, status.structured_content["agent_memory_limits"]["default_ttl_days"])
        self.assertEqual(365, status.structured_content["agent_memory_limits"]["max_ttl_days"])
        self.assertTrue(denied.is_error)

    def test_mutation_profile_reads_fail_closed_until_projection_is_repaired(self) -> None:
        async def run(root: Path):
            async with Client(autonomous_server(root), raise_exceptions=True) as client:
                status_before = await client.call_tool("status", {})
                stale_search = await client.call_tool("search", {
                    "query": "Aurora PostgreSQL 18",
                    "project": "orion",
                })
                canonical_history = await client.call_tool("history", {"project": "orion"})
                run_cli(root, "sync")
                repaired_search = await client.call_tool("search", {
                    "query": "Aurora PostgreSQL 18",
                    "project": "orion",
                })
                return status_before, stale_search, canonical_history, repaired_search

        with FixtureCopy() as root:
            initialize(root)
            value = request()
            raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
            remember_memory_bundle(
                root,
                request=value,
                request_bytes=len(raw),
                allowed_projects={"orion"},
                proposal_limits=ProposalLimits(),
                memory_limits=AgentMemoryLimits(),
                lock_timeout=10.0,
                recover_stale=False,
            )
            status_before, stale_search, history, repaired_search = asyncio.run(run(root))

        self.assertFalse(status_before.structured_content["healthy"])
        self.assertTrue(stale_search.is_error)
        self.assertFalse(history.is_error)
        self.assertEqual(1, len(history.structured_content["claims"]))
        self.assertFalse(repaired_search.is_error)
        self.assertEqual(1, repaired_search.structured_content["count"])

    def test_both_mutation_profiles_atomically_guard_every_indexed_read(self) -> None:
        async def run(root: Path, server, section_key: str):
            async with Client(server, raise_exceptions=True) as client:
                indexed = [
                    await client.call_tool("search", {
                        "query": "Aurora PostgreSQL",
                        "project": "orion",
                    }),
                    await client.call_tool("context", {
                        "task": "Aurora PostgreSQL",
                        "budget": 500,
                        "project": "orion",
                    }),
                    await client.call_tool("read", {
                        "section": section_key,
                        "project": "orion",
                    }),
                    await client.call_tool("related", {
                        "document": "20_projects/orion/PROJECT_HUB.md",
                        "project": "orion",
                    }),
                    await client.call_tool("stats", {}),
                ]
                status = await client.call_tool("status", {})
                history = await client.call_tool("history", {"project": "orion"})
                return indexed, status, history

        for ordinal, mutation_mode in enumerate(("proposal-only", "autonomous"), start=1):
            with self.subTest(mutation_mode=mutation_mode), FixtureCopy() as root:
                initialize(root)
                section_key = run_cli(
                    root,
                    "search",
                    "Aurora PostgreSQL",
                    "--project",
                    "orion",
                    "--no-sync",
                )["results"][0]["section_key"]
                if mutation_mode == "proposal-only":
                    server = create_server(
                        root,
                        mutation_mode="proposal-only",
                        proposal_policy=ProposalServerPolicy(
                            allowed_projects=frozenset({"orion"}),
                            limits=ProposalLimits(),
                            max_requests_per_minute=30,
                        ),
                    )
                else:
                    server = autonomous_server(root)
                healthy_indexed, healthy_status, healthy_history = asyncio.run(
                    run(root, server, section_key)
                )
                self.assertTrue(all(not result.is_error for result in healthy_indexed))
                self.assertTrue(healthy_status.structured_content["healthy"])
                self.assertFalse(healthy_history.is_error)
                if mutation_mode == "proposal-only":
                    server = create_server(
                        root,
                        mutation_mode="proposal-only",
                        proposal_policy=ProposalServerPolicy(
                            allowed_projects=frozenset({"orion"}),
                            limits=ProposalLimits(),
                            max_requests_per_minute=30,
                        ),
                    )
                else:
                    server = autonomous_server(root)
                original_invoke = mcp_module._invoke_cli
                injected = False

                def inject_between_old_preflight_and_read(
                    bound_root: Path,
                    *arguments: str,
                    stdin_text: str | None = None,
                ) -> dict:
                    nonlocal injected
                    if not injected and "--require-current-projection" in arguments:
                        value = request(
                            submission_id=(
                                f"submission_eeeeeeee-eeee-4eee-8ee{ordinal}-eeeeeeeeeee{ordinal}"
                            )
                        )
                        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
                        submit_proposal_bundle(
                            root,
                            request=value,
                            request_bytes=len(raw),
                            allowed_projects={"orion"},
                            limits=ProposalLimits(),
                            lock_timeout=10.0,
                            recover_stale=False,
                        )
                        injected = True
                    return original_invoke(bound_root, *arguments, stdin_text=stdin_text)

                with patch.object(
                    mcp_module,
                    "_invoke_cli",
                    side_effect=inject_between_old_preflight_and_read,
                ):
                    indexed, status, history = asyncio.run(run(root, server, section_key))

            self.assertTrue(all(result.is_error for result in indexed))
            self.assertFalse(status.structured_content["healthy"])
            self.assertFalse(history.is_error)
            self.assertEqual(1, len(history.structured_content["proposals"]))

    def test_autonomous_rate_limit_fails_before_second_canonical_write(self) -> None:
        async def run(root: Path):
            async with Client(
                autonomous_server(root, max_requests_per_minute=1),
                raise_exceptions=True,
            ) as client:
                base = {
                    "project": "orion",
                    "subject": "Aurora",
                    "predicate": "uses",
                    "object": "PostgreSQL 18",
                    "valid_from": "2026-08-27T00:00:00Z",
                    "source_refs": [],
                    "evidence": [],
                }
                first = await client.call_tool("remember_memory", {
                    **base,
                    "submission_id": "submission_99999999-9999-4999-8999-999999999999",
                })
                second = await client.call_tool("remember_memory", {
                    **base,
                    "subject": "Gateway",
                    "submission_id": "submission_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                })
                return first, second

        with FixtureCopy() as root:
            initialize(root)
            first, second = asyncio.run(run(root))
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertFalse(first.is_error)
        self.assertTrue(second.is_error)
        self.assertEqual(1, len(history["claims"]))

    def test_autonomous_server_refuses_writes_after_root_identity_change(self) -> None:
        async def run(root: Path):
            server = autonomous_server(root)
            identity_path = root / "00_registry" / "ELM_ROOT_ID.json"
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["creator"] = "operator:changed-after-startup"
            identity_path.write_text(
                json.dumps(identity, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            async with Client(server, raise_exceptions=True) as client:
                status = await client.call_tool("status", {})
                denied = await client.call_tool("remember_memory", {
                    "submission_id": "submission_dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    "project": "orion",
                    "subject": "Aurora",
                    "predicate": "uses",
                    "object": "PostgreSQL 18",
                    "valid_from": "2026-08-27T00:00:00Z",
                    "source_refs": [],
                    "evidence": [],
                })
                return status, denied

        with FixtureCopy() as root:
            initialize(root)
            status, denied = asyncio.run(run(root))
            proposals = list(root.rglob("proposal_*.json"))

        self.assertFalse(status.structured_content["healthy"])
        self.assertIn(
            "root_identity_changed_after_startup",
            status.structured_content["errors"],
        )
        self.assertTrue(denied.is_error)
        self.assertEqual([], proposals)

    def test_autonomous_profile_requires_explicit_policy(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            with self.assertRaisesRegex(Exception, "autonomous mode requires"):
                create_server(root, mutation_mode="autonomous")


if __name__ == "__main__":
    unittest.main()
