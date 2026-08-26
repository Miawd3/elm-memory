from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from _bootstrap import FixtureCopy, run_cli, run_cli_process
from elm_memory.governance import FileChange, apply_transaction, load_governance, new_id


SOURCE_HASH = "a" * 64
SOURCE_REF = "repo://src/config.py@sha256:" + SOURCE_HASH


def propose(
    root: Path,
    *,
    object_value: str,
    valid_from: str = "2026-08-25T00:00:00Z",
    evidence_id: str | None = None,
) -> dict:
    arguments = [
        "propose",
        "--project", "orion",
        "--subject", "backend",
        "--predicate", "uses_database",
        "--object", object_value,
        "--actor", "agent:test",
        "--requested-authority", "agent_proposal",
        "--valid-from", valid_from,
        "--source-ref", SOURCE_REF,
        "--rationale", "Fixture proposal.",
    ]
    if evidence_id:
        arguments.extend(("--evidence", evidence_id))
    return run_cli(root, *arguments)


def accept(root: Path, proposal_id: str) -> dict:
    return run_cli(
        root,
        "accept",
        proposal_id,
        "--actor", "human:test",
        "--authority", "user_ratified",
    )


class GovernedLifecycleTests(unittest.TestCase):
    def test_reference_proposal_accept_and_rebuild_round_trip(self) -> None:
        with FixtureCopy() as root:
            evidence = run_cli(
                root,
                "evidence", "add",
                "--project", "orion",
                "--kind", "repository_file",
                "--source-uri", "repo://src/config.py",
                "--content-sha256", SOURCE_HASH,
                "--actor", "agent:test",
            )
            proposal = propose(root, object_value="HeliosDB", evidence_id=evidence["evidence_id"])
            pending = run_cli(root, "proposals", "list", "--project", "orion", "--status", "pending")
            accepted = accept(root, proposal["proposal_id"])
            current = run_cli(root, "search", "HeliosDB")
            history = run_cli(root, "history", "--project", "orion")
            outline = run_cli(root, "outline", accepted["claim_id"], "--no-sync")
            related = run_cli(root, "related", accepted["claim_id"], "--no-sync")
            rebuilt = run_cli(root, "rebuild")
            after = run_cli(root, "search", "HeliosDB", "--no-sync")
            doctor = run_cli(root, "doctor", "--no-sync")
            claim_path = root / "20_projects" / "orion" / "CLAIMS" / f"{accepted['claim_id']}.md"
            claim_text = claim_path.read_text(encoding="utf-8")

        self.assertEqual(1, pending["count"])
        self.assertEqual("accepted", accepted["action"])
        self.assertEqual(1, current["count"])
        self.assertEqual(accepted["claim_id"], current["results"][0]["claim_id"])
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual(accepted["claim_id"], outline["document"]["path"].split("/")[-1].removesuffix(".md"))
        self.assertEqual(outline["document"]["id"], related["document"]["id"])
        self.assertEqual([], rebuilt["errors"])
        self.assertEqual(current["results"][0]["section_key"], after["results"][0]["section_key"])
        self.assertEqual(0, doctor["issue_count"])
        self.assertIn("Status: accepted", claim_text)
        self.assertNotIn("Fixture proposal.", json.dumps(evidence))

    def test_supersession_hides_old_claim_but_preserves_valid_history(self) -> None:
        with FixtureCopy() as root:
            first = accept(root, propose(root, object_value="HeliosDB")["proposal_id"])
            first_recorded_at = run_cli(root, "history", "--project", "orion")["claims"][0]["recorded_at"]
            replacement = propose(
                root,
                object_value="NovaDB",
                valid_from="2026-08-26T00:00:00Z",
            )
            superseded = run_cli(
                root,
                "supersede",
                first["claim_id"],
                replacement["proposal_id"],
                "--actor", "human:test",
                "--authority", "ratified_project_decision",
            )
            ordinary_old = run_cli(root, "search", "HeliosDB")
            historical_old = run_cli(root, "search", "HeliosDB", "--include-history")
            current_new = run_cli(root, "search", "NovaDB")
            before_change = run_cli(
                root,
                "history",
                "--project", "orion",
                "--valid-at", "2026-08-25T12:00:00Z",
            )
            recorded_slice = run_cli(
                root,
                "history",
                "--project", "orion",
                "--recorded-at", first_recorded_at,
            )
            old_section = historical_old["results"][0]["section_key"]
            denied = run_cli_process(root, "read", old_section)
            allowed = run_cli(root, "read", old_section, "--include-history")
            doctor = run_cli(root, "doctor", "--no-sync")

        self.assertEqual("superseded", superseded["action"])
        self.assertEqual(0, ordinary_old["count"])
        self.assertGreaterEqual(historical_old["count"], 1)
        self.assertEqual({first["claim_id"]}, {item["claim_id"] for item in historical_old["results"]})
        self.assertGreaterEqual(current_new["count"], 1)
        self.assertEqual({superseded["claim_id"]}, {item["claim_id"] for item in current_new["results"]})
        self.assertEqual(first["claim_id"], before_change["claims"][0]["claim_id"])
        self.assertEqual("accepted", recorded_slice["claims"][0]["status"])
        self.assertIsNone(recorded_slice["claims"][0]["valid_to"])
        self.assertTrue(recorded_slice["claims"][0]["recorded_time_reconstructed"])
        self.assertNotEqual(0, denied.returncode)
        self.assertEqual(first["claim_id"], allowed["path"].split("/")[-1].removesuffix(".md"))
        self.assertEqual(0, doctor["issue_count"])

    def test_dispute_hides_claim_and_history_retains_it(self) -> None:
        with FixtureCopy() as root:
            accepted = accept(root, propose(root, object_value="QuasarDB")["proposal_id"])
            result = run_cli(
                root,
                "dispute",
                accepted["claim_id"],
                "--actor", "reviewer:test",
                "--reason-code", "contradicted",
            )
            ordinary = run_cli(root, "search", "QuasarDB")
            historical = run_cli(root, "search", "QuasarDB", "--include-history")

        self.assertEqual("disputed", result["action"])
        self.assertEqual(0, ordinary["count"])
        self.assertGreaterEqual(historical["count"], 1)
        self.assertEqual({accepted["claim_id"]}, {item["claim_id"] for item in historical["results"]})

    def test_contradictions_are_reported_and_context_marks_conflict(self) -> None:
        with FixtureCopy() as root:
            accept(root, propose(root, object_value="AmberDB")["proposal_id"])
            accept(root, propose(root, object_value="CobaltDB")["proposal_id"])
            history = run_cli(root, "history", "--project", "orion")
            doctor = run_cli(root, "doctor", "--no-sync")
            packet = run_cli(
                root,
                "context",
                "backend uses_database AmberDB",
                "--project", "orion",
                "--budget", "1400",
                "--no-trace",
            )

        self.assertEqual(1, len(history["contradictions"]))
        self.assertTrue(any(issue["kind"] == "claim_contradiction" for issue in doctor["issues"]))
        self.assertTrue(any(source["authority"] == "accepted_conflicting_memory" for source in packet["sources"]))

    def test_delete_removes_active_and_derived_content_but_keeps_tombstone(self) -> None:
        with FixtureCopy() as root:
            accepted = accept(root, propose(root, object_value="ObsidianDB")["proposal_id"])
            deleted = run_cli(
                root,
                "delete",
                accepted["claim_id"],
                "--actor", "human:test",
                "--reason-code", "user_request",
            )
            search = run_cli(root, "search", "ObsidianDB")
            history = run_cli(root, "history", "--project", "orion", "--include-deleted")
            rebuilt = run_cli(root, "rebuild")
            search_after = run_cli(root, "search", "ObsidianDB", "--no-sync")

        self.assertEqual("deleted", deleted["action"])
        self.assertEqual(0, search["count"])
        self.assertEqual(accepted["claim_id"], history["tombstones"][0]["item_id"])
        self.assertEqual([], rebuilt["errors"])
        self.assertEqual(0, search_after["count"])

    def test_two_concurrent_accepts_produce_one_terminal_transition(self) -> None:
        with FixtureCopy() as root:
            proposal_id = propose(root, object_value="RaceDB")["proposal_id"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _: run_cli_process(
                        root,
                        "accept",
                        proposal_id,
                        "--actor", "human:test",
                        "--authority", "user_ratified",
                    ),
                    range(2),
                ))
            history = run_cli(root, "history", "--project", "orion")

        self.assertEqual([0, 2], sorted(item.returncode for item in results))
        self.assertEqual(1, len(history["claims"]))
        self.assertEqual(1, sum(1 for event in history["events"] if event["action"] == "proposal_accepted"))

    def test_reject_and_defer_are_terminal_and_cannot_be_accepted(self) -> None:
        with FixtureCopy() as root:
            rejected_id = propose(root, object_value="RejectDB")["proposal_id"]
            deferred_id = propose(root, object_value="DeferDB")["proposal_id"]
            rejected = run_cli(
                root, "reject", rejected_id,
                "--actor", "human:test", "--reason-code", "duplicate",
            )
            deferred = run_cli(
                root, "defer", deferred_id,
                "--actor", "human:test", "--reason-code", "insufficient_evidence",
            )
            rows = run_cli(root, "proposals", "list", "--project", "orion")
            rejected_accept = run_cli_process(
                root, "accept", rejected_id,
                "--actor", "human:test", "--authority", "user_ratified",
            )
            deferred_accept = run_cli_process(
                root, "accept", deferred_id,
                "--actor", "human:test", "--authority", "user_ratified",
            )

        self.assertEqual("rejected", rejected["action"])
        self.assertEqual("deferred", deferred["action"])
        self.assertEqual({"rejected", "deferred"}, {item["status"] for item in rows["proposals"]})
        self.assertEqual(2, rejected_accept.returncode)
        self.assertEqual(2, deferred_accept.returncode)

    def test_future_claim_is_visible_only_through_explicit_history_policy(self) -> None:
        with FixtureCopy() as root:
            accepted = accept(
                root,
                propose(
                    root,
                    object_value="FutureDB",
                    valid_from="2030-01-01T00:00:00Z",
                )["proposal_id"],
            )
            ordinary = run_cli(root, "search", "FutureDB")
            historical = run_cli(root, "search", "FutureDB", "--include-history")
            denied = run_cli_process(root, "outline", accepted["claim_id"], "--no-sync")
            allowed = run_cli(root, "outline", accepted["claim_id"], "--include-history", "--no-sync")

        self.assertEqual(0, ordinary["count"])
        self.assertGreaterEqual(historical["count"], 1)
        self.assertNotEqual(0, denied.returncode)
        self.assertEqual(accepted["claim_id"], allowed["document"]["path"].split("/")[-1].removesuffix(".md"))

    def test_cross_project_supersession_is_refused_without_changes(self) -> None:
        with FixtureCopy() as root:
            accepted = accept(root, propose(root, object_value="OrionDB")["proposal_id"])
            other = run_cli(
                root,
                "propose",
                "--project", "lighthouse",
                "--subject", "backend",
                "--predicate", "uses_database",
                "--object", "LightDB",
                "--actor", "agent:test",
                "--source-ref", SOURCE_REF,
            )
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("claim_*.md")
            }
            refused = run_cli_process(
                root,
                "supersede", accepted["claim_id"], other["proposal_id"],
                "--actor", "human:test", "--authority", "user_ratified",
            )
            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("claim_*.md")
            }

        self.assertEqual(2, refused.returncode)
        self.assertEqual(before, after)

    def test_proposal_and_evidence_deletion_leave_metadata_only_tombstones(self) -> None:
        with FixtureCopy() as root:
            evidence = run_cli(
                root,
                "evidence", "add",
                "--project", "orion", "--kind", "repository_file",
                "--source-uri", "repo://delete-me", "--content-sha256", SOURCE_HASH,
                "--actor", "agent:test",
            )
            proposal = propose(root, object_value="DeleteQueueDB", evidence_id=evidence["evidence_id"])
            run_cli(
                root, "delete", proposal["proposal_id"],
                "--actor", "human:test", "--reason-code", "user_request",
            )
            run_cli(
                root, "delete", evidence["evidence_id"],
                "--actor", "human:test", "--reason-code", "user_request",
            )
            listing = run_cli(root, "proposals", "list", "--project", "orion")
            history = run_cli(root, "history", "--project", "orion", "--include-deleted")

        self.assertEqual(0, listing["count"])
        self.assertEqual({proposal["proposal_id"], evidence["evidence_id"]}, {item["item_id"] for item in history["tombstones"]})
        self.assertTrue(all("object" not in item for item in history["tombstones"]))

    def test_newer_canonical_format_is_refused_without_partial_claim(self) -> None:
        with FixtureCopy() as root:
            proposal_id = new_id("proposal")
            path = root / "01_inbox" / "elm_proposals" / "orion" / f"{proposal_id}.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "format_version": 99,
                "record_type": "proposal",
                "proposal_id": proposal_id,
            }), encoding="utf-8")
            refused = run_cli_process(
                root, "accept", proposal_id,
                "--actor", "human:test", "--authority", "user_ratified",
            )
            claims = list(root.rglob("claim_*.md"))

        self.assertEqual(2, refused.returncode)
        self.assertIn('"error": "governance_failed"', refused.stderr)
        self.assertEqual([], claims)

    def test_explicit_recovery_repairs_orphaned_transaction_before_sync(self) -> None:
        with FixtureCopy() as root:
            proposal = propose(root, object_value="RecoveryBlockedDB")
            target = root / "20_projects" / "orion" / "DECISIONS.md"
            original = target.read_bytes()
            transaction_id = new_id("transaction")
            backup = root / "backups" / "elm-governance" / transaction_id / "000-DECISIONS.md.bak"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(original)
            changed = original + b"\npartial transaction payload\n"
            target.write_bytes(changed)
            journal = root / "01_inbox" / "elm_transactions" / f"{transaction_id}.json"
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({
                "format_version": 1,
                "record_type": "transaction",
                "transaction_id": transaction_id,
                "operation": "crash-fixture",
                "actor": "test",
                "created_at": "2026-08-26T00:00:00+00:00",
                "changes": [{
                    "target": "20_projects/orion/DECISIONS.md",
                    "action": "write",
                    "before_exists": True,
                    "before_sha256": __import__("hashlib").sha256(original).hexdigest(),
                    "after_sha256": __import__("hashlib").sha256(changed).hexdigest(),
                    "backup": f"backups/elm-governance/{transaction_id}/000-DECISIONS.md.bak",
                }],
            }), encoding="utf-8")
            doctor = run_cli(root, "doctor", "--no-sync")
            blocked_search = run_cli_process(root, "search", "partial", "--no-sync")
            blocked = run_cli_process(
                root,
                "accept",
                proposal["proposal_id"],
                "--actor", "human:test",
                "--authority", "user_ratified",
            )
            claims_before_recovery = list(root.rglob("claim_*.md"))
            preview = run_cli(root, "recover", "--dry-run")
            applied = run_cli(root, "recover", "--apply")
            restored = target.read_bytes()

        self.assertTrue(any(issue["kind"] == "incomplete_transaction" for issue in doctor["issues"]))
        self.assertEqual(2, blocked_search.returncode)
        self.assertIn("Governed reads are unavailable", blocked_search.stderr)
        self.assertEqual(2, blocked.returncode)
        self.assertIn("recover it explicitly", blocked.stderr)
        self.assertEqual([], claims_before_recovery)
        self.assertEqual(1, preview["pending_count"])
        self.assertEqual([transaction_id], applied["recovered"])
        self.assertEqual(original, restored)
        self.assertFalse(journal.exists())
        self.assertFalse(backup.exists())

    def test_recovery_refuses_to_overwrite_an_unexpected_manual_edit(self) -> None:
        with FixtureCopy() as root:
            target = root / "20_projects" / "orion" / "DECISIONS.md"
            original = target.read_bytes()
            expected_partial = original + b"\nexpected partial\n"
            manual_edit = original + b"\nmanual edit after crash\n"
            transaction_id = new_id("transaction")
            backup = root / "backups" / "elm-governance" / transaction_id / "000-DECISIONS.md.bak"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(original)
            target.write_bytes(manual_edit)
            journal = root / "01_inbox" / "elm_transactions" / f"{transaction_id}.json"
            journal.parent.mkdir(parents=True)
            import hashlib
            journal.write_text(json.dumps({
                "format_version": 1,
                "record_type": "transaction",
                "transaction_id": transaction_id,
                "operation": "manual-edit-fixture",
                "actor": "test",
                "created_at": "2026-08-26T00:00:00+00:00",
                "changes": [{
                    "target": "20_projects/orion/DECISIONS.md",
                    "action": "write",
                    "before_exists": True,
                    "before_sha256": hashlib.sha256(original).hexdigest(),
                    "after_sha256": hashlib.sha256(expected_partial).hexdigest(),
                    "backup": f"backups/elm-governance/{transaction_id}/000-DECISIONS.md.bak",
                }],
            }), encoding="utf-8")
            refused = run_cli_process(root, "recover", "--apply")
            current = target.read_bytes()
            journal_remains = journal.exists()

        self.assertEqual(2, refused.returncode)
        self.assertIn("unexpectedly changed transaction target", refused.stderr)
        self.assertEqual(manual_edit, current)
        self.assertTrue(journal_remains)


class CanonicalTransactionTests(unittest.TestCase):
    def test_failed_multi_file_transaction_rolls_back_new_targets(self) -> None:
        with FixtureCopy() as root:
            first = root / "20_projects" / "orion" / "FIRST.txt"
            second = root / "20_projects" / "orion" / "SECOND.txt"
            transaction_id = new_id("transaction")
            from elm_memory import governance

            real_write = governance.atomic_write_bytes
            target_writes = 0

            def injected(path: Path, payload: bytes) -> None:
                nonlocal target_writes
                if path in {first, second}:
                    target_writes += 1
                    if target_writes == 2:
                        raise OSError("injected second-target failure")
                real_write(path, payload)

            with patch("elm_memory.governance.atomic_write_bytes", side_effect=injected):
                with self.assertRaises(OSError):
                    apply_transaction(
                        root,
                        transaction_id=transaction_id,
                        operation="test-rollback",
                        actor="test",
                        changes=[FileChange(first, b"first\n"), FileChange(second, b"second\n")],
                    )
            journals = list((root / "01_inbox" / "elm_transactions").glob("*.json"))
            first_exists = first.exists()
            second_exists = second.exists()
            recovery_backups = list((root / "backups" / "elm-governance").rglob("*.bak"))

        self.assertFalse(first_exists)
        self.assertFalse(second_exists)
        self.assertEqual([], journals)
        self.assertEqual([], recovery_backups)

    def test_committed_orphan_transaction_finishes_cleanup_without_rollback(self) -> None:
        with FixtureCopy() as root:
            target = root / "20_projects" / "orion" / "DECISIONS.md"
            original = target.read_bytes()
            committed = original + b"\ncommitted transaction payload\n"
            target.write_bytes(committed)
            transaction_id = new_id("transaction")
            backup = root / "backups" / "elm-governance" / transaction_id / "000-DECISIONS.md.bak"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(original)
            journal = root / "01_inbox" / "elm_transactions" / f"{transaction_id}.json"
            journal.parent.mkdir(parents=True)
            import hashlib
            journal.write_text(json.dumps({
                "format_version": 1,
                "record_type": "transaction",
                "transaction_id": transaction_id,
                "operation": "committed-crash-fixture",
                "actor": "test",
                "created_at": "2026-08-26T00:00:00+00:00",
                "state": "committed",
                "changes": [{
                    "target": "20_projects/orion/DECISIONS.md",
                    "action": "write",
                    "before_exists": True,
                    "before_sha256": hashlib.sha256(original).hexdigest(),
                    "after_sha256": hashlib.sha256(committed).hexdigest(),
                    "backup": f"backups/elm-governance/{transaction_id}/000-DECISIONS.md.bak",
                }],
            }), encoding="utf-8")
            preview = run_cli(root, "recover", "--dry-run")
            applied = run_cli(root, "recover", "--apply")
            current = target.read_bytes()

        self.assertEqual("finish_commit_cleanup", preview["transactions"][0]["recovery_action"])
        self.assertEqual([transaction_id], applied["recovered"])
        self.assertEqual(committed, current)
        self.assertFalse(journal.exists())
        self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
