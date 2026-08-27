from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _bootstrap import FixtureCopy, run_cli, run_cli_process, run_cli_stdin
from elm_memory import governance
from elm_memory.canonical import CanonicalJSONError, canonical_json_bytes, parse_closed_json
from elm_memory.cli import command_proposal_submit, connect
from elm_memory.governance import (
    GovernanceError,
    ProposalLimits,
    normalize_proposal_submission,
    submit_proposal_bundle,
)
from elm_memory.locking import WriterLockError


SUBMISSION_ID = "submission_11111111-1111-4111-8111-111111111111"


def request(*, submission_id: str = SUBMISSION_ID, object_value: str = "PostgreSQL 17") -> dict:
    return {
        "submission_id": submission_id,
        "project": "orion",
        "subject": "Aurora",
        "predicate": "uses",
        "object": object_value,
        "valid_from": "2026-08-26T00:00:00Z",
        "sensitivity": "normal",
        "rationale": "Line 1\r\nLine 2",
        "source_refs": [],
        "evidence": [],
    }


def initialize(root: Path) -> None:
    run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
    run_cli(root, "rebuild")


def submit_cli(root: Path, value: dict, *extra: str):
    return run_cli_stdin(
        root,
        json.dumps(value, ensure_ascii=False),
        "proposal-submit",
        "--request-stdin",
        "--allow-project",
        "orion",
        *extra,
    )


class CanonicalJSONTests(unittest.TestCase):
    def test_rejects_duplicate_keys_floats_and_lone_surrogates(self) -> None:
        with self.assertRaises(CanonicalJSONError):
            parse_closed_json('{"a":1,"a":2}')
        with self.assertRaises(CanonicalJSONError):
            parse_closed_json('{"a":1.5}')
        with self.assertRaises(CanonicalJSONError):
            canonical_json_bytes({"a": "\ud800"})

    def test_submission_digest_has_a_fixed_cross_platform_vector(self) -> None:
        _, payload, _ = normalize_proposal_submission(request())
        expected = (
            b'{"evidence":[],"object":"PostgreSQL 17","predicate":"uses",'
            b'"project":"orion","rationale":"Line 1\\nLine 2",'
            b'"requested_authority":"agent_proposal","sensitivity":"normal",'
            b'"source_refs":[],"subject":"Aurora",'
            b'"valid_from":"2026-08-26T00:00:00.000000+00:00"}'
        )
        canonical = canonical_json_bytes(payload)
        digest = hashlib.sha256(b"ELM-PROPOSAL-SUBMISSION-V1\x00" + canonical).hexdigest()

        self.assertEqual(expected, canonical)
        self.assertEqual("b691bef279c5a2ae248f0b644a158c75eef760b0ee93203c4cdc5b782c5bd19d", digest)

    def test_closed_submission_rejects_scalar_coercion_and_normalized_duplicates(self) -> None:
        invalid_values = {
            "submission_id": True,
            "project": 123,
            "subject": ["Aurora"],
            "predicate": {"value": "uses"},
            "object": 17,
            "valid_from": False,
            "sensitivity": 1,
            "rationale": {"instruction": "persist"},
        }
        for field, invalid in invalid_values.items():
            with self.subTest(field=field):
                value = request()
                value[field] = invalid
                with self.assertRaises(GovernanceError):
                    normalize_proposal_submission(value)

        value = request()
        value["evidence"] = [{
            "kind": "repository_file",
            "source_uri": "repo://src/config.py",
            "content_sha256": int("1" * 64),
            "sensitivity": "normal",
        }]
        with self.assertRaises(GovernanceError):
            normalize_proposal_submission(value)

        value = request()
        value["source_refs"] = [
            "repo://src/config.py@sha256:" + "A" * 64,
            "repo://src/config.py@sha256:" + "a" * 64,
        ]
        with self.assertRaisesRegex(GovernanceError, "Duplicate normalized source_refs"):
            normalize_proposal_submission(value)


class RootIdentityTests(unittest.TestCase):
    def test_bootstrap_is_explicit_immutable_and_idempotent(self) -> None:
        with FixtureCopy() as root:
            preview = run_cli(root, "root-id", "init", "--dry-run", "--creator", "operator:test")
            applied = run_cli(root, "root-id", "init", "--apply", "--creator", "operator:test")
            original = (root / "00_registry" / "ELM_ROOT_ID.json").read_bytes()
            backup = (root / applied["backup"]).read_bytes()
            repeated = run_cli(root, "root-id", "init", "--apply", "--creator", "operator:other")

        self.assertEqual("00_registry/ELM_ROOT_ID.json", preview["would_create"])
        self.assertTrue(applied["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(applied["root_id"], repeated["root_id"])
        self.assertEqual(original, json.dumps({
            "created_at": applied["created_at"],
            "creator": "operator:test",
            "format_version": 1,
            "record_type": "root_identity",
            "root_id": applied["root_id"],
        }, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        self.assertEqual(original, backup)


class ProposalBundleTests(unittest.TestCase):
    def test_submission_is_idempotent_conflicts_fail_and_rebuild_preserves_v2(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            first = submit_cli(root, request())
            replay = submit_cli(root, request())
            conflict = submit_cli(root, request(object_value="DifferentDB"))
            rebuilt = run_cli(root, "rebuild")
            listed = run_cli(root, "proposals", "list", "--project", "orion", "--no-sync")

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, replay.returncode, replay.stderr)
        self.assertTrue(json.loads(replay.stdout)["idempotent_replay"])
        self.assertEqual(2, conflict.returncode)
        self.assertIn("different normalized payload", conflict.stderr)
        self.assertEqual([], rebuilt["errors"])
        self.assertEqual(1, listed["count"])
        self.assertEqual(2, listed["proposals"][0]["format_version"])
        self.assertEqual(SUBMISSION_ID, listed["proposals"][0]["submission_id"])

    def test_v2_proposal_remains_eligible_for_explicit_cli_ratification(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            value["evidence"] = [{
                "kind": "repository_file",
                "source_uri": "repo://src/config.py",
                "content_sha256": "b" * 64,
                "sensitivity": "normal",
            }]
            submitted = submit_cli(root, value)
            proposal_id = json.loads(submitted.stdout)["proposal_id"]
            accepted = run_cli(
                root,
                "accept",
                proposal_id,
                "--actor",
                "human:test",
                "--authority",
                "user_ratified",
            )
            history = run_cli(root, "history", "--project", "orion", "--no-sync")

        self.assertEqual("accepted", accepted["action"])
        self.assertEqual(proposal_id, history["claims"][0]["proposal_id"])
        self.assertEqual(1, history["evidence_count"])

    def test_compound_failure_leaves_no_orphan_evidence_or_submission(self) -> None:
        value = request()
        value["evidence"] = [{
            "kind": "repository_file",
            "source_uri": "repo://src/config.py",
            "content_sha256": "a" * 64,
            "excerpt_sha256": None,
            "sensitivity": "normal",
        }]
        with FixtureCopy() as root:
            initialize(root)
            real_write = governance.atomic_write_bytes
            target_writes = 0

            def injected(path: Path, payload: bytes) -> None:
                nonlocal target_writes
                if "elm_evidence" in path.parts or "elm_proposals" in path.parts:
                    target_writes += 1
                    if target_writes == 2:
                        raise OSError("injected proposal write failure")
                real_write(path, payload)

            with patch("elm_memory.governance.atomic_write_bytes", side_effect=injected):
                with self.assertRaises(OSError):
                    submit_proposal_bundle(
                        root,
                        request=value,
                        request_bytes=len(json.dumps(value).encode("utf-8")),
                        allowed_projects={"orion"},
                        limits=ProposalLimits(),
                        lock_timeout=2,
                        recover_stale=False,
                    )
            proposals = list(root.rglob("proposal_*.json"))
            evidence = list(root.rglob("evidence_*.json"))
            journals = list((root / "01_inbox" / "elm_transactions").glob("*.json"))

        self.assertEqual([], proposals)
        self.assertEqual([], evidence)
        self.assertEqual([], journals)

    def test_concurrent_retry_creates_one_bundle_and_quota_is_durable(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()

            def submit():
                return submit_proposal_bundle(
                    root,
                    request=value,
                    request_bytes=len(json.dumps(value).encode("utf-8")),
                    allowed_projects={"orion"},
                    limits=ProposalLimits(max_pending_per_project=1),
                    lock_timeout=5,
                    recover_stale=False,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = [future.result() for future in [executor.submit(submit), executor.submit(submit)]]
            second = request(
                submission_id="submission_22222222-2222-4222-8222-222222222222",
                object_value="SecondDB",
            )
            with self.assertRaises(GovernanceError):
                submit_proposal_bundle(
                    root,
                    request=second,
                    request_bytes=len(json.dumps(second).encode("utf-8")),
                    allowed_projects={"orion"},
                    limits=ProposalLimits(max_pending_per_project=1),
                    lock_timeout=2,
                    recover_stale=False,
                )
            proposal_files = list(root.rglob("proposal_*.json"))

        self.assertEqual(1, len(proposal_files))
        self.assertEqual({False, True}, {item["idempotent_replay"] for item in results})

    def test_root_quota_cannot_be_bypassed_with_another_allowed_project(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            limits = ProposalLimits(max_pending_records_root=1)
            first = request()
            submit_proposal_bundle(
                root,
                request=first,
                request_bytes=len(json.dumps(first).encode("utf-8")),
                allowed_projects={"orion", "lighthouse"},
                limits=limits,
                lock_timeout=2,
                recover_stale=False,
            )
            second = request(
                submission_id="submission_66666666-6666-4666-8666-666666666666",
                object_value="ChurnDB",
            )
            second["project"] = "lighthouse"
            with self.assertRaises(GovernanceError) as refused:
                submit_proposal_bundle(
                    root,
                    request=second,
                    request_bytes=len(json.dumps(second).encode("utf-8")),
                    allowed_projects={"orion", "lighthouse"},
                    limits=limits,
                    lock_timeout=2,
                    recover_stale=False,
                )

        self.assertIn("root pending record quota", str(refused.exception))

    def test_closed_schema_rejects_raw_evidence_and_project_bypass(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            raw = request()
            raw["raw_evidence"] = "secret"
            unknown = submit_cli(root, raw)
            denied = request()
            denied["project"] = "lighthouse"
            denied_result = submit_cli(root, denied)
            credentialed = request(
                submission_id="submission_77777777-7777-4777-8777-777777777777"
            )
            credentialed["source_refs"] = [
                "https://user:password@example.test/source@sha256:" + "c" * 64
            ]
            credentialed_result = submit_cli(root, credentialed)

        self.assertEqual(2, unknown.returncode)
        self.assertIn("unknown fields", unknown.stderr)
        self.assertEqual(2, denied_result.returncode)
        self.assertIn("not enabled", denied_result.stderr)
        self.assertEqual(2, credentialed_result.returncode)
        self.assertIn("embedded credentials", credentialed_result.stderr)

    def test_status_detects_canonical_proposal_when_projection_has_not_synced(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            submit_proposal_bundle(
                root,
                request=value,
                request_bytes=len(json.dumps(value).encode("utf-8")),
                allowed_projects={"orion"},
                limits=ProposalLimits(),
                lock_timeout=2,
                recover_stale=False,
            )
            status = run_cli(root, "status")
            refused_list = run_cli_process(
                root, "proposals", "list", "--project", "orion", "--no-sync"
            )

        self.assertFalse(status["healthy"])
        self.assertFalse(status["governance_projection_current"])
        self.assertIn("governance_projection_stale", status["errors"])
        self.assertEqual(2, refused_list.returncode)
        self.assertIn("projection is not current and healthy", refused_list.stderr)

    def test_deleted_v2_submission_identity_is_retired_without_body_retention(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            proposal = json.loads(submit_cli(root, request()).stdout)
            deleted = run_cli(
                root,
                "delete",
                proposal["proposal_id"],
                "--actor",
                "human:test",
                "--reason-code",
                "user_request",
            )
            tombstone = json.loads(
                (root / "30_agent_logs" / "elm_tombstones" / f"{proposal['proposal_id']}.json")
                .read_text(encoding="utf-8")
            )
            replay = submit_cli(root, request())

        self.assertEqual("deleted", deleted["action"])
        self.assertEqual(64, len(tombstone["submission_replay_key"]))
        self.assertNotIn("submission_id", tombstone)
        self.assertNotIn("payload_digest", tombstone)
        self.assertNotIn("subject", tombstone)
        self.assertEqual(2, replay.returncode)
        self.assertIn("retired by explicit proposal deletion", replay.stderr)

    def test_projection_lock_failure_reports_committed_truth_separately(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            raw = json.dumps(value).encode("utf-8")
            arguments = SimpleNamespace(
                max_request_bytes=65_536,
                max_reference_count=16,
                max_pending_per_project=256,
                max_pending_records_root=2_048,
                max_pending_bytes_per_project=4 * 1024 * 1024,
                max_pending_bytes_root=32 * 1024 * 1024,
                allow_project=["orion"],
                lock_timeout=2.0,
                recover_stale_lock=False,
                json=True,
            )
            stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
            with closing(connect(root)) as connection, patch.object(sys, "stdin", stdin), patch(
                "elm_memory.cli._sync_from_args",
                side_effect=WriterLockError("injected projection lock"),
            ), patch("elm_memory.cli.emit") as emitted:
                command_proposal_submit(arguments, connection, root)
            result = emitted.call_args.args[0]
            status = run_cli(root, "status")

        self.assertTrue(result["canonical_committed"])
        self.assertFalse(result["projection"]["healthy"])
        self.assertEqual("writer_lock_unavailable", result["projection"]["errors"][0]["kind"])
        self.assertFalse(status["healthy"])

    def test_any_projection_exception_preserves_committed_receipt(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            value = request()
            raw = json.dumps(value).encode("utf-8")
            arguments = SimpleNamespace(
                max_request_bytes=65_536,
                max_reference_count=16,
                max_pending_per_project=256,
                max_pending_records_root=2_048,
                max_pending_bytes_per_project=4 * 1024 * 1024,
                max_pending_bytes_root=32 * 1024 * 1024,
                allow_project=["orion"],
                lock_timeout=2.0,
                recover_stale_lock=False,
                json=True,
            )
            stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
            with closing(connect(root)) as connection, patch.object(sys, "stdin", stdin), patch(
                "elm_memory.cli._sync_from_args",
                side_effect=RuntimeError("injected sensitive projection detail"),
            ), patch("elm_memory.cli.emit") as emitted:
                command_proposal_submit(arguments, connection, root)
            result = emitted.call_args.args[0]

        self.assertTrue(result["canonical_committed"])
        self.assertFalse(result["projection"]["healthy"])
        self.assertEqual("projection_refresh_failed", result["projection"]["errors"][0]["kind"])
        self.assertNotIn("sensitive", result["projection"]["errors"][0]["message"])

    def test_tampered_v2_records_and_boolean_versions_fail_closed(self) -> None:
        with FixtureCopy() as root:
            initialize(root)
            submitted = submit_cli(root, request())
            proposal = json.loads(submitted.stdout)
            target = root / proposal["path"]
            record = json.loads(target.read_text(encoding="utf-8"))
            record["subject"] = "Tampered"
            target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            synced = run_cli(root, "sync")
            replay = submit_cli(root, request())

        self.assertTrue(any("payload digest mismatch" in item["error"] for item in synced["errors"]))
        self.assertEqual(2, replay.returncode)
        self.assertIn("payload digest mismatch", replay.stderr)

        with FixtureCopy() as root:
            initialize(root)
            submitted = submit_cli(root, request())
            target = root / json.loads(submitted.stdout)["path"]
            record = json.loads(target.read_text(encoding="utf-8"))
            record["format_version"] = True
            target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            rebuilt = run_cli(root, "rebuild")

        self.assertTrue(any("Unsupported canonical proposal format" in item["error"] for item in rebuilt["errors"]))

    def test_evidence_digest_tombstone_and_replay_rules_are_explicit(self) -> None:
        value = request()
        value["evidence"] = [{
            "kind": "repository_file",
            "source_uri": "repo://src/config.py",
            "content_sha256": "d" * 64,
            "sensitivity": "normal",
        }]
        with FixtureCopy() as root:
            initialize(root)
            submitted = submit_cli(root, value)
            proposal = json.loads(submitted.stdout)
            evidence_id = proposal["evidence_ids"][0]
            evidence_target = next(root.rglob(f"{evidence_id}.json"))
            record = json.loads(evidence_target.read_text(encoding="utf-8"))
            record["content_sha256"] = "e" * 64
            evidence_target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            synced = run_cli(root, "sync")

        self.assertTrue(any("payload digest mismatch" in item["error"] for item in synced["errors"]))

        with FixtureCopy() as root:
            initialize(root)
            proposal = json.loads(submit_cli(root, value).stdout)
            evidence_id = proposal["evidence_ids"][0]
            denied = run_cli_process(
                root, "delete", evidence_id, "--actor", "human:test", "--reason-code", "user_request"
            )
            run_cli(
                root, "accept", proposal["proposal_id"], "--actor", "human:test",
                "--authority", "user_ratified",
            )
            deleted = run_cli(
                root, "delete", evidence_id, "--actor", "human:test", "--reason-code", "user_request"
            )
            replay = submit_cli(root, value)

        self.assertEqual(2, denied.returncode)
        self.assertIn("pending proposal", denied.stderr)
        self.assertEqual("deleted", deleted["action"])
        self.assertEqual(2, replay.returncode)
        self.assertIn("tombstoned evidence prevents digest verification", replay.stderr)

    def test_configured_reference_request_and_symlink_boundaries(self) -> None:
        one_ref = request()
        one_ref["source_refs"] = ["repo://src/config.py@sha256:" + "a" * 64]
        raw_size = len(json.dumps(one_ref).encode("utf-8"))
        with FixtureCopy() as root:
            initialize(root)
            accepted = submit_proposal_bundle(
                root,
                request=one_ref,
                request_bytes=raw_size,
                allowed_projects={"orion"},
                limits=ProposalLimits(max_request_bytes=raw_size, max_reference_count=1),
                lock_timeout=2,
                recover_stale=False,
            )
        self.assertTrue(accepted["canonical_committed"])

        two_refs = request()
        two_refs["source_refs"] = [
            "repo://src/a.py@sha256:" + "a" * 64,
            "repo://src/b.py@sha256:" + "b" * 64,
        ]
        with FixtureCopy() as root:
            initialize(root)
            with self.assertRaisesRegex(GovernanceError, "references exceed"):
                submit_proposal_bundle(
                    root,
                    request=two_refs,
                    request_bytes=len(json.dumps(two_refs).encode("utf-8")),
                    allowed_projects={"orion"},
                    limits=ProposalLimits(max_reference_count=1),
                    lock_timeout=2,
                    recover_stale=False,
                )
            with self.assertRaisesRegex(GovernanceError, "request exceeds"):
                submit_proposal_bundle(
                    root,
                    request=one_ref,
                    request_bytes=raw_size,
                    allowed_projects={"orion"},
                    limits=ProposalLimits(max_request_bytes=raw_size - 1),
                    lock_timeout=2,
                    recover_stale=False,
                )

        with FixtureCopy() as root:
            initialize(root)
            target = root / "20_projects" / "orion"
            outside = root.parent / "outside-orion"
            target.rename(outside)
            try:
                target.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable on this host")
            with self.assertRaises(GovernanceError):
                submit_proposal_bundle(
                    root,
                    request=request(),
                    request_bytes=len(json.dumps(request()).encode("utf-8")),
                    allowed_projects={"orion"},
                    limits=ProposalLimits(),
                    lock_timeout=2,
                    recover_stale=False,
                )


if __name__ == "__main__":
    unittest.main()
