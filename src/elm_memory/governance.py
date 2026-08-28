"""Canonical governed-memory records and lifecycle operations for ELM Phase Three.

JSON and Markdown files under the ELM root are the durable source of truth.
SQLite only projects these records for retrieval.  Multi-file mutations use a
canonical recovery journal so deleting ``.elm`` never removes recovery state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

from .atomic import atomic_create_bytes, atomic_write_bytes
from .canonical import CanonicalJSONError, canonical_json_bytes, parse_closed_json
from .locking import WriterLock
from .tokens import estimate_tokens


CANONICAL_FORMAT_VERSION = 1
PROPOSAL_V2_FORMAT_VERSION = 2
PROPOSAL_V3_FORMAT_VERSION = 3
PROPOSAL_FORMAT_VERSION = 4
SUBMISSION_DIGEST_DOMAIN = b"ELM-PROPOSAL-SUBMISSION-V1\x00"
MEMORY_SUBMISSION_DIGEST_DOMAIN = b"ELM-AUTONOMOUS-SUBMISSION-V1\x00"
MEMORY_CAS_SUBMISSION_DIGEST_DOMAIN = b"ELM-AUTONOMOUS-CAS-SUBMISSION-V1\x00"
SUBMISSION_RETIREMENT_DOMAIN = b"ELM-PROPOSAL-SUBMISSION-RETIREMENT-V1\x00"
ID_PREFIXES = {
    "proposal": "proposal_",
    "evidence": "evidence_",
    "claim": "claim_",
    "event": "event_",
    "tombstone": "tombstone_",
    "transaction": "transaction_",
    "root": "root_",
    "submission": "submission_",
}
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SOURCE_ROOT_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REF_RE = re.compile(r"^.+@sha256:([0-9a-fA-F]{64})$")
ACCEPTED_AUTHORITIES = {
    "ratified_project_decision",
    "user_ratified",
    "verified_repository_state",
}
AGENT_MEMORY_AUTHORITY = "agent_curated"
CLAIM_AUTHORITIES = ACCEPTED_AUTHORITIES | {AGENT_MEMORY_AUTHORITY}
PROPOSAL_AUTHORITIES = {
    "agent_proposal",
    "candidate_inference",
    "verified_repository_state",
}
SENSITIVITIES = {"normal", "restricted"}
EVIDENCE_KINDS = {"repository_file", "document_section", "external_uri"}
SOURCE_CHANNELS = {"mcp"}
TERMINAL_PROPOSAL_ACTIONS = {
    "proposal_accepted": "accepted",
    "proposal_rejected": "rejected",
    "proposal_deferred": "deferred",
    "claim_superseded": "accepted",
}
CLAIM_STATUSES = {"accepted", "disputed", "superseded"}
EVENT_ACTIONS = {
    "proposal_accepted",
    "proposal_rejected",
    "proposal_deferred",
    "claim_disputed",
    "claim_superseded",
    "item_deleted",
}
REASON_CODES = {
    "contradicted",
    "duplicate",
    "insufficient_evidence",
    "obsolete",
    "out_of_scope",
    "quota_exceeded",
    "source_verification_failed",
    "stale_cas_target",
    "user_request",
    "other",
}
LOGICAL_COMPACTION_SCHEMA_VERSION = 1
DEFAULT_COMPACTION_BUDGET = 1_200
MIN_COMPACTION_BUDGET = 512
MAX_COMPACTION_BUDGET = 32_768


class GovernanceError(RuntimeError):
    """A safe, user-correctable governed-memory failure."""


@dataclass(frozen=True)
class ProposalLimits:
    """Durable Phase 5A queue limits supplied by the trusted server profile."""

    max_request_bytes: int = 65_536
    max_reference_count: int = 16
    max_pending_per_project: int = 256
    max_pending_records_root: int = 2_048
    max_pending_bytes_per_project: int = 4 * 1024 * 1024
    max_pending_bytes_root: int = 32 * 1024 * 1024

    def validate(self) -> "ProposalLimits":
        for field, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise GovernanceError(f"{field} must be a positive integer.")
        if self.max_reference_count > 256:
            raise GovernanceError("max_reference_count cannot exceed 256.")
        if self.max_request_bytes > 4 * 1024 * 1024:
            raise GovernanceError("max_request_bytes cannot exceed 4 MiB.")
        return self


@dataclass(frozen=True)
class AgentMemoryLimits:
    """Durable active-memory limits for an operator-enabled autonomous profile."""

    max_active_per_project: int = 512
    max_active_root: int = 4_096

    def validate(self) -> "AgentMemoryLimits":
        for field, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise GovernanceError(f"{field} must be a positive integer.")
        if self.max_active_root < self.max_active_per_project:
            raise GovernanceError(
                "max_active_root cannot be smaller than max_active_per_project."
            )
        return self


@dataclass(frozen=True)
class AgentMemoryLifecyclePolicy:
    """Deterministic validity bounds for newly activated agent memory."""

    default_ttl_days: int = 90
    max_ttl_days: int = 365

    def validate(self) -> "AgentMemoryLifecyclePolicy":
        for field, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise GovernanceError(f"{field} must be a positive integer.")
            if value > 3_650:
                raise GovernanceError(f"{field} cannot exceed 3650 days.")
        if self.default_ttl_days > self.max_ttl_days:
            raise GovernanceError(
                "default_ttl_days cannot exceed max_ttl_days."
            )
        return self


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _parse_time(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GovernanceError(f"{field} must be an ISO-8601 timestamp with a timezone.") from exc
    if parsed.tzinfo is None:
        raise GovernanceError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _line(value: str, field: str, *, maximum: int = 1000) -> str:
    candidate = str(value).strip()
    if not candidate:
        raise GovernanceError(f"{field} cannot be empty.")
    if "\n" in candidate or "\r" in candidate or "\x00" in candidate:
        raise GovernanceError(f"{field} must be a single safe text line.")
    if len(candidate) > maximum:
        raise GovernanceError(f"{field} exceeds the {maximum}-character limit.")
    return candidate


def _project(value: str) -> str:
    candidate = _line(value, "project", maximum=64)
    if not PROJECT_RE.fullmatch(candidate):
        raise GovernanceError("project must contain only letters, digits, underscore, or hyphen.")
    return candidate


def _sha256(value: str | None, field: str, *, optional: bool = False) -> str | None:
    if value is None or not str(value).strip():
        if optional:
            return None
        raise GovernanceError(f"{field} is required.")
    candidate = str(value).strip().lower()
    if not SHA256_RE.fullmatch(candidate):
        raise GovernanceError(f"{field} must be a 64-character SHA-256 hex digest.")
    return candidate


def new_id(kind: str) -> str:
    return ID_PREFIXES[kind] + str(uuid.uuid4())


def validate_id(value: str, kind: str) -> str:
    candidate = str(value).strip().lower()
    prefix = ID_PREFIXES[kind]
    if not candidate.startswith(prefix):
        raise GovernanceError(f"{kind} ID must use the form {prefix}<uuid4>.")
    try:
        parsed = uuid.UUID(candidate[len(prefix):])
    except ValueError as exc:
        raise GovernanceError(f"{kind} ID must contain a UUIDv4 value.") from exc
    if parsed.version != 4:
        raise GovernanceError(f"{kind} ID must contain a UUIDv4 value.")
    return prefix + str(parsed)


def _json_bytes(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(root: Path, path: Path) -> str:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise GovernanceError(f"Governed path escapes the ELM root: {path}") from exc


def _target(root: Path, relative: str) -> Path:
    normalized = PurePosixPath(relative.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise GovernanceError(f"Unsafe governed relative path: {relative}")
    target = root.joinpath(*normalized.parts)
    _relative(root, target)
    return target


def proposal_path(root: Path, project: str, proposal_id: str) -> Path:
    return root / "01_inbox" / "elm_proposals" / _project(project) / f"{validate_id(proposal_id, 'proposal')}.json"


def evidence_path(root: Path, evidence_id: str) -> Path:
    return root / "40_sources" / "elm_evidence" / "metadata" / f"{validate_id(evidence_id, 'evidence')}.json"


def claim_path(root: Path, project: str, claim_id: str) -> Path:
    return root / "20_projects" / _project(project) / "CLAIMS" / f"{validate_id(claim_id, 'claim')}.md"


def event_path(root: Path, occurred_at: str, event_id: str) -> Path:
    moment = datetime.fromisoformat(_parse_time(occurred_at, "occurred_at"))
    return (
        root
        / "30_agent_logs"
        / "elm_events"
        / f"{moment.year:04d}"
        / f"{moment.month:02d}"
        / f"{moment.day:02d}"
        / f"{validate_id(event_id, 'event')}.json"
    )


def tombstone_path(root: Path, item_id: str) -> Path:
    return root / "30_agent_logs" / "elm_tombstones" / f"{item_id}.json"


def _load_json(path: Path, record_type: str) -> dict:
    try:
        value = parse_closed_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, CanonicalJSONError) as exc:
        raise GovernanceError(f"Invalid {record_type} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceError(f"{record_type} record must be a JSON object: {path}")
    allowed_versions = {1, 2, 3, 4} if record_type == "proposal" else {1}
    format_version = value.get("format_version")
    if type(format_version) is not int or format_version not in allowed_versions:
        raise GovernanceError(
            f"Unsupported canonical {record_type} format at {path}; mutation was refused."
        )
    if value.get("record_type") != record_type:
        raise GovernanceError(f"Expected record_type={record_type} at {path}.")
    return value


def _required(record: dict, fields: set[str], path: Path, record_type: str) -> None:
    missing = sorted(field for field in fields if record.get(field) in (None, ""))
    if missing:
        raise GovernanceError(
            f"{record_type} is missing required fields at {path}: {', '.join(missing)}"
        )


def _require_string_fields(record: dict, fields: set[str], path: Path, record_type: str) -> None:
    invalid = sorted(field for field in fields if not isinstance(record.get(field), str))
    if invalid:
        raise GovernanceError(
            f"{record_type} fields must be JSON strings at {path}: {', '.join(invalid)}"
        )


def _validate_proposal_record(record: dict, path: Path) -> dict:
    version = record.get("format_version")
    is_versioned = version in {
        PROPOSAL_V2_FORMAT_VERSION,
        PROPOSAL_V3_FORMAT_VERSION,
        PROPOSAL_FORMAT_VERSION,
    }
    has_lease = version in {PROPOSAL_V3_FORMAT_VERSION, PROPOSAL_FORMAT_VERSION}
    is_v4 = version == PROPOSAL_FORMAT_VERSION
    if is_versioned:
        allowed = {
            "format_version", "record_type", "proposal_id", "project", "subject",
            "predicate", "object", "proposed_at", "valid_from", "actor",
            "requested_authority", "sensitivity", "evidence_ids", "source_refs",
            "rationale", "submission_id", "payload_digest", "source_channel",
        }
        if has_lease:
            allowed.add("valid_to")
        if is_v4:
            allowed.update({"supersedes_claim_id", "expected_claim_sha256"})
        label = f"Proposal-v{version}"
        unknown = sorted(set(record) - allowed)
        missing = sorted(allowed - set(record))
        if unknown:
            raise GovernanceError(
                f"{label} contains unknown fields at {path}: {', '.join(unknown)}"
            )
        if missing:
            raise GovernanceError(
                f"{label} is missing fields at {path}: {', '.join(missing)}"
            )
        _require_string_fields(
            record,
            allowed - {"format_version", "evidence_ids", "source_refs"},
            path,
            label,
        )
        if not isinstance(record["evidence_ids"], list) or not isinstance(record["source_refs"], list):
            raise GovernanceError(f"{label} evidence/source refs must be JSON arrays at {path}.")
        if any(not isinstance(item, str) for item in (*record["evidence_ids"], *record["source_refs"])):
            raise GovernanceError(f"{label} evidence/source refs must contain only strings at {path}.")
        if len(set(record["evidence_ids"])) != len(record["evidence_ids"]):
            raise GovernanceError(f"{label} contains duplicate evidence IDs at {path}.")
        if len(set(record["source_refs"])) != len(record["source_refs"]):
            raise GovernanceError(f"{label} contains duplicate source refs at {path}.")
        original_fields = [
            "proposal_id", "project", "subject", "predicate", "object", "proposed_at",
            "valid_from", "actor", "rationale", "submission_id", "payload_digest",
        ]
        if has_lease:
            original_fields.append("valid_to")
        if is_v4:
            original_fields.extend(("supersedes_claim_id", "expected_claim_sha256"))
        original = {
            field: record[field]
            for field in original_fields
        }
        original_evidence_ids = list(record["evidence_ids"])
        original_source_refs = list(record["source_refs"])

    _required(record, {
        "proposal_id", "project", "subject", "predicate", "object", "proposed_at",
        "valid_from", "actor", "requested_authority", "sensitivity",
    }, path, "proposal")
    record["proposal_id"] = validate_id(record["proposal_id"], "proposal")
    record["project"] = _project(record["project"])
    if path.stem.lower() != record["proposal_id"] or path.parent.name != record["project"]:
        raise GovernanceError(f"Proposal path does not match its ID/project: {path}")
    for field in ("subject", "predicate", "object"):
        record[field] = _line(record[field], field)
    record["proposed_at"] = _parse_time(record["proposed_at"], "proposed_at")
    record["valid_from"] = _parse_time(record["valid_from"], "valid_from")
    if has_lease:
        record["valid_to"] = _parse_time(record["valid_to"], "valid_to")
        if record["valid_to"] <= record["valid_from"]:
            raise GovernanceError(f"Proposal-v{version} valid_to must be later than valid_from at {path}.")
    if is_v4:
        record["supersedes_claim_id"] = validate_id(
            record["supersedes_claim_id"], "claim"
        )
        record["expected_claim_sha256"] = _sha256(
            record["expected_claim_sha256"], "expected_claim_sha256"
        )
    record["actor"] = _line(record["actor"], "actor", maximum=200)
    if record["requested_authority"] not in PROPOSAL_AUTHORITIES:
        raise GovernanceError(f"Invalid proposal authority at {path}.")
    if record["sensitivity"] not in SENSITIVITIES:
        raise GovernanceError(f"Invalid proposal sensitivity at {path}.")
    if not isinstance(record.get("evidence_ids", []), list) or not isinstance(record.get("source_refs", []), list):
        raise GovernanceError(f"Proposal evidence/source refs must be arrays at {path}.")
    record["evidence_ids"] = _validate_evidence_ids(record.get("evidence_ids"))
    record["source_refs"] = _validate_source_refs(record.get("source_refs"))
    record["rationale"] = str(record.get("rationale") or "")[:4000]
    if is_versioned:
        record["submission_id"] = validate_id(record["submission_id"], "submission")
        record["payload_digest"] = _sha256(record["payload_digest"], "payload_digest")
        if record["source_channel"] not in SOURCE_CHANNELS:
            raise GovernanceError(f"Invalid proposal source_channel at {path}.")
        if record["actor"] != "mcp:unverified" or record["requested_authority"] != "agent_proposal":
            raise GovernanceError(f"Proposal-v{version} has invalid server-stamped provenance at {path}.")
        normalized = {field: record[field] for field in original}
        if (
            normalized != original
            or record["evidence_ids"] != original_evidence_ids
            or record["source_refs"] != original_source_refs
        ):
            raise GovernanceError(f"Proposal-v{version} contains non-canonical field encodings at {path}.")
        normalized_sources = []
        for source_ref in record["source_refs"]:
            match = SOURCE_REF_RE.fullmatch(source_ref)
            assert match is not None
            locator = source_ref[: source_ref.lower().rfind("@sha256:")]
            normalized_sources.append(
                _submission_source_uri(locator) + "@sha256:" + match.group(1).lower()
            )
        if record["source_refs"] != normalized_sources:
            raise GovernanceError(f"Proposal-v{version} source refs are not canonically normalized at {path}.")
        canonical_sources = sorted(record["source_refs"], key=canonical_json_bytes)
        if record["source_refs"] != canonical_sources:
            raise GovernanceError(f"Proposal-v{version} source refs are not in canonical order at {path}.")
    return record


def _validate_evidence_record(record: dict, path: Path) -> dict:
    string_fields = {
        "evidence_id", "project", "kind", "source_uri", "captured_at",
        "content_sha256", "sensitivity", "retention", "actor",
    }
    _require_string_fields(record, string_fields, path, "evidence")
    if record.get("excerpt_sha256") is not None and not isinstance(record.get("excerpt_sha256"), str):
        raise GovernanceError(f"evidence excerpt_sha256 must be a JSON string or null at {path}.")
    original = {
        field: record.get(field)
        for field in string_fields | {"excerpt_sha256"}
    }
    _required(record, {
        "evidence_id", "project", "kind", "source_uri", "captured_at",
        "content_sha256", "sensitivity", "retention", "actor",
    }, path, "evidence")
    record["evidence_id"] = validate_id(record["evidence_id"], "evidence")
    if path.stem.lower() != record["evidence_id"]:
        raise GovernanceError(f"Evidence path does not match its ID: {path}")
    record["project"] = _project(record["project"])
    if record["kind"] not in EVIDENCE_KINDS:
        raise GovernanceError(f"Invalid evidence kind at {path}.")
    record["source_uri"] = _line(record["source_uri"], "source_uri", maximum=2000)
    if not record["source_uri"].startswith(("repo://", "elm://", "https://", "http://", "urn:")):
        raise GovernanceError(f"Invalid evidence source URI at {path}.")
    record["captured_at"] = _parse_time(record["captured_at"], "captured_at")
    record["content_sha256"] = _sha256(record["content_sha256"], "content_sha256")
    record["excerpt_sha256"] = _sha256(record.get("excerpt_sha256"), "excerpt_sha256", optional=True)
    if record["sensitivity"] not in SENSITIVITIES or record["retention"] != "reference_only":
        raise GovernanceError(f"Invalid evidence sensitivity/retention at {path}.")
    record["actor"] = _line(record["actor"], "actor", maximum=200)
    if {field: record.get(field) for field in original} != original:
        raise GovernanceError(f"Evidence contains non-canonical field encodings at {path}.")
    return record


def _validate_event_record(record: dict, path: Path) -> dict:
    _required(record, {
        "event_id", "action", "occurred_at", "actor", "transaction_id", "project",
    }, path, "event")
    record["event_id"] = validate_id(record["event_id"], "event")
    if path.stem.lower() != record["event_id"]:
        raise GovernanceError(f"Event path does not match its ID: {path}")
    if record["action"] not in EVENT_ACTIONS:
        raise GovernanceError(f"Unknown governance event action at {path}: {record['action']}")
    record["occurred_at"] = _parse_time(record["occurred_at"], "occurred_at")
    record["actor"] = _line(record["actor"], "actor", maximum=200)
    record["transaction_id"] = validate_id(record["transaction_id"], "transaction")
    record["project"] = _project(record["project"])
    for field, kind in (("proposal_id", "proposal"), ("claim_id", "claim"), ("previous_claim_id", "claim")):
        if record.get(field):
            record[field] = validate_id(record[field], kind)
    if record.get("target_id"):
        record["target_id"] = _line(record["target_id"], "target_id")
    if record.get("authority") and record["authority"] not in CLAIM_AUTHORITIES:
        raise GovernanceError(f"Invalid event authority at {path}.")
    if record.get("reason_code") and record["reason_code"] not in REASON_CODES:
        raise GovernanceError(f"Invalid event reason code at {path}.")
    for field in ("previous_sha256", "current_sha256"):
        record[field] = _sha256(record.get(field), field, optional=True)
    return record


def _validate_tombstone_record(record: dict, path: Path) -> dict:
    _required(record, {
        "tombstone_id", "item_id", "item_type", "deleted_at", "actor",
        "reason_code", "prior_sha256",
    }, path, "tombstone")
    record["tombstone_id"] = validate_id(record["tombstone_id"], "tombstone")
    if record["item_type"] not in {"claim", "proposal", "evidence"}:
        raise GovernanceError(f"Invalid tombstone item type at {path}.")
    record["item_id"] = validate_id(record["item_id"], record["item_type"])
    if path.stem.lower() != record["item_id"]:
        raise GovernanceError(f"Tombstone path does not match its item ID: {path}")
    if record.get("project"):
        record["project"] = _project(record["project"])
    record["deleted_at"] = _parse_time(record["deleted_at"], "deleted_at")
    record["actor"] = _line(record["actor"], "actor", maximum=200)
    record["reason_code"] = _assert_reason(record["reason_code"])
    record["prior_sha256"] = _sha256(record["prior_sha256"], "prior_sha256")
    replay_key = record.get("submission_replay_key")
    if replay_key is not None:
        if record["item_type"] != "proposal":
            raise GovernanceError(f"Only proposal tombstones may retain a submission replay key at {path}.")
        record["submission_replay_key"] = _sha256(
            replay_key,
            "submission_replay_key",
        )
    return record


def _validate_transaction_record(record: dict, path: Path) -> dict:
    _required(record, {
        "transaction_id", "operation", "actor", "created_at", "changes",
    }, path, "transaction")
    record["transaction_id"] = validate_id(record["transaction_id"], "transaction")
    if path.stem.lower() != record["transaction_id"]:
        raise GovernanceError(f"Transaction path does not match its ID: {path}")
    record["operation"] = _line(record["operation"], "operation", maximum=100)
    record["actor"] = _line(record["actor"], "actor", maximum=200)
    record["created_at"] = _parse_time(record["created_at"], "created_at")
    record["state"] = record.get("state", "prepared")
    if record["state"] not in {"prepared", "committed"}:
        raise GovernanceError(f"Invalid transaction state at {path}.")
    if not isinstance(record["changes"], list) or not record["changes"]:
        raise GovernanceError(f"Transaction changes must be a non-empty array at {path}.")
    for item in record["changes"]:
        if not isinstance(item, dict) or item.get("action") not in {"write", "delete"}:
            raise GovernanceError(f"Invalid transaction change at {path}.")
        normalized = PurePosixPath(str(item.get("target", "")).replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
            raise GovernanceError(f"Unsafe transaction target at {path}.")
        if not isinstance(item.get("before_exists"), bool):
            raise GovernanceError(f"Transaction before_exists must be boolean at {path}.")
        item["before_sha256"] = _sha256(item.get("before_sha256"), "before_sha256", optional=True)
        item["after_sha256"] = _sha256(item.get("after_sha256"), "after_sha256", optional=item["action"] == "delete")
        if item["before_exists"] and not item.get("backup"):
            raise GovernanceError(f"Transaction backup is required for an existing target at {path}.")
        if item["before_exists"] and not item.get("before_sha256"):
            raise GovernanceError(f"Transaction before_sha256 is required at {path}.")
    return record


def _atomic_new(path: Path, payload: bytes) -> None:
    try:
        atomic_create_bytes(path, payload)
    except FileExistsError as exc:
        raise GovernanceError(f"Canonical record already exists: {path}") from exc


def _validate_source_refs(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        candidate = _line(value, "source_ref", maximum=2000)
        if not SOURCE_REF_RE.fullmatch(candidate):
            raise GovernanceError("source_ref must end with @sha256:<64-hex-digest>.")
        if candidate not in result:
            result.append(candidate)
    return result


def _validate_evidence_ids(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        candidate = validate_id(value, "evidence")
        if candidate not in result:
            result.append(candidate)
    return result


def create_evidence_reference(
    root: Path,
    *,
    project: str,
    kind: str,
    source_uri: str,
    content_sha256: str,
    excerpt_sha256: str | None,
    sensitivity: str,
    actor: str,
    captured_at: str | None = None,
) -> dict:
    project = _project(project)
    if kind not in EVIDENCE_KINDS:
        raise GovernanceError(f"kind must be one of: {', '.join(sorted(EVIDENCE_KINDS))}.")
    source_uri = _line(source_uri, "source_uri", maximum=2000)
    if not source_uri.startswith(("repo://", "elm://", "https://", "http://", "urn:")):
        raise GovernanceError("source_uri must use repo://, elm://, http(s)://, or urn:.")
    if sensitivity not in SENSITIVITIES:
        raise GovernanceError(f"sensitivity must be one of: {', '.join(sorted(SENSITIVITIES))}.")
    evidence_id = new_id("evidence")
    record = {
        "format_version": CANONICAL_FORMAT_VERSION,
        "record_type": "evidence",
        "evidence_id": evidence_id,
        "project": project,
        "kind": kind,
        "source_uri": source_uri,
        "captured_at": _parse_time(captured_at or utc_now(), "captured_at"),
        "content_sha256": _sha256(content_sha256, "content_sha256"),
        "excerpt_sha256": _sha256(excerpt_sha256, "excerpt_sha256", optional=True),
        "sensitivity": sensitivity,
        "retention": "reference_only",
        "actor": _line(actor, "actor", maximum=200),
    }
    path = evidence_path(root, evidence_id)
    _atomic_new(path, _json_bytes(record))
    return {**record, "path": _relative(root, path)}


def create_proposal(
    root: Path,
    *,
    project: str,
    subject: str,
    predicate: str,
    object_value: str,
    actor: str,
    requested_authority: str,
    valid_from: str | None,
    sensitivity: str,
    evidence_ids: list[str] | None,
    source_refs: list[str] | None,
    rationale: str,
) -> dict:
    project = _project(project)
    if requested_authority not in PROPOSAL_AUTHORITIES:
        raise GovernanceError(
            f"requested_authority must be one of: {', '.join(sorted(PROPOSAL_AUTHORITIES))}."
        )
    if sensitivity not in SENSITIVITIES:
        raise GovernanceError(f"sensitivity must be one of: {', '.join(sorted(SENSITIVITIES))}.")
    normalized_evidence = _validate_evidence_ids(evidence_ids)
    for evidence_id in normalized_evidence:
        path = evidence_path(root, evidence_id)
        if not path.is_file():
            raise GovernanceError(f"Evidence reference does not exist: {evidence_id}")
        evidence = _validate_evidence_record(_load_json(path, "evidence"), path)
        if evidence.get("project") != project:
            raise GovernanceError(f"Evidence belongs to another project: {evidence_id}")
    proposed_at = utc_now()
    proposal_id = new_id("proposal")
    record = {
        "format_version": CANONICAL_FORMAT_VERSION,
        "record_type": "proposal",
        "proposal_id": proposal_id,
        "project": project,
        "subject": _line(subject, "subject"),
        "predicate": _line(predicate, "predicate"),
        "object": _line(object_value, "object"),
        "proposed_at": proposed_at,
        "valid_from": _parse_time(valid_from or proposed_at, "valid_from"),
        "actor": _line(actor, "actor", maximum=200),
        "requested_authority": requested_authority,
        "sensitivity": sensitivity,
        "evidence_ids": normalized_evidence,
        "source_refs": _validate_source_refs(source_refs),
        "rationale": str(rationale).strip()[:4000],
    }
    path = proposal_path(root, project, proposal_id)
    _atomic_new(path, _json_bytes(record))
    return {**record, "path": _relative(root, path), "status": "pending"}


ROOT_IDENTITY_RELATIVE = "00_registry/ELM_ROOT_ID.json"
SUBMISSION_FIELDS = {
    "submission_id",
    "project",
    "subject",
    "predicate",
    "object",
    "valid_from",
    "sensitivity",
    "rationale",
    "source_refs",
    "evidence",
}
MEMORY_SUBMISSION_FIELDS = SUBMISSION_FIELDS | {
    "valid_to",
    "supersedes_claim_id",
    "expected_claim_sha256",
}
EVIDENCE_DESCRIPTOR_FIELDS = {
    "kind",
    "source_uri",
    "content_sha256",
    "excerpt_sha256",
    "sensitivity",
}


def root_identity_path(root: Path) -> Path:
    return _target(root, ROOT_IDENTITY_RELATIVE)


def _validate_root_identity(record: dict, path: Path) -> dict:
    allowed = {"format_version", "record_type", "root_id", "created_at", "creator"}
    unknown = sorted(set(record) - allowed)
    missing = sorted(allowed - set(record))
    if unknown or missing:
        detail = "unknown=" + ",".join(unknown) if unknown else "missing=" + ",".join(missing)
        raise GovernanceError(f"root_identity has an invalid closed schema at {path}: {detail}")
    _require_string_fields(record, {"root_id", "created_at", "creator"}, path, "root_identity")
    _required(record, {"root_id", "created_at", "creator"}, path, "root_identity")
    record["root_id"] = validate_id(record["root_id"], "root")
    record["created_at"] = _parse_time(record["created_at"], "created_at")
    record["creator"] = _line(record["creator"], "creator", maximum=200)
    return record


def load_root_identity(root: Path, *, required: bool = False) -> dict | None:
    path = root_identity_path(root)
    if not path.is_file():
        if required:
            raise GovernanceError(
                "ELM root identity is missing. Run `elm root-id init --dry-run`, then "
                "`elm root-id init --apply --creator <label>` outside MCP."
            )
        return None
    record = _validate_root_identity(_load_json(path, "root_identity"), path)
    return {**record, "path": ROOT_IDENTITY_RELATIVE}


def bootstrap_root_identity(
    root: Path,
    *,
    apply: bool,
    creator: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    """Preview or create the immutable portable root identity."""
    existing = load_root_identity(root)
    if existing:
        return {"mode": "apply" if apply else "dry-run", "created": False, **existing}
    if not apply:
        return {
            "mode": "dry-run",
            "created": False,
            "would_create": ROOT_IDENTITY_RELATIVE,
            "record_type": "root_identity",
            "format_version": 1,
        }
    actor = _line(creator, "creator", maximum=200)
    with WriterLock(
        root,
        "root-identity-init",
        timeout=lock_timeout,
        recover_stale=recover_stale,
    ):
        _refuse_pending_transactions(root)
        existing = load_root_identity(root)
        if existing:
            return {"mode": "apply", "created": False, **existing}
        record = {
            "format_version": 1,
            "record_type": "root_identity",
            "root_id": new_id("root"),
            "created_at": utc_now(),
            "creator": actor,
        }
        transaction_id = new_id("transaction")
        backup_path = (
            root
            / "backups"
            / "elm-root-identity"
            / transaction_id
            / "ELM_ROOT_ID.json"
        )
        payload = _json_bytes(record)
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation="root-identity-init",
            actor=actor,
            changes=[
                FileChange(root_identity_path(root), payload),
                FileChange(backup_path, payload),
            ],
        )
    return {
        "mode": "apply",
        "created": True,
        **record,
        "path": ROOT_IDENTITY_RELATIVE,
        "backup": _relative(root, backup_path),
        **transaction,
    }


def _closed_object(
    value: object,
    expected: set[str],
    field: str,
    *,
    optional: set[str] | None = None,
) -> dict:
    if not isinstance(value, dict):
        raise GovernanceError(f"{field} must be a JSON object.")
    unknown = sorted(set(value) - expected)
    missing = sorted((expected - (optional or set())) - set(value))
    if unknown:
        raise GovernanceError(f"{field} contains unknown fields: {', '.join(unknown)}")
    if missing:
        raise GovernanceError(f"{field} is missing fields: {', '.join(missing)}")
    return value


def _submission_source_uri(value: object) -> str:
    if not isinstance(value, str):
        raise GovernanceError("source_uri must be a JSON string.")
    source_uri = _line(value, "source_uri", maximum=2000)
    if not source_uri.startswith(("repo://", "elm://", "https://", "http://", "urn:")):
        raise GovernanceError("source_uri must use repo://, elm://, http(s)://, or urn:.")
    parsed = urlsplit(source_uri)
    if parsed.username is not None or parsed.password is not None:
        raise GovernanceError("source_uri must not contain embedded credentials.")
    if parsed.query:
        raise GovernanceError("source_uri query strings are not accepted by proposal-only MCP.")
    return source_uri


def parse_source_root_specs(values: list[str]) -> dict[str, Path]:
    """Parse trusted ``ALIAS=PATH`` verifier configuration from an operator surface."""
    roots: dict[str, Path] = {}
    for raw in values:
        if not isinstance(raw, str) or "=" not in raw:
            raise GovernanceError("source roots must use ALIAS=PATH.")
        alias, raw_path = raw.split("=", 1)
        alias = alias.strip().lower()
        if not SOURCE_ROOT_ALIAS_RE.fullmatch(alias):
            raise GovernanceError(
                "source-root aliases must start with a lowercase letter and use only a-z, 0-9, _, or -."
            )
        if alias in roots:
            raise GovernanceError(f"duplicate source-root alias: {alias}")
        candidate = Path(raw_path.strip()).expanduser()
        if not raw_path.strip() or candidate.is_symlink():
            raise GovernanceError(f"source root must be an existing non-symlink directory: {alias}")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise GovernanceError(f"source root does not exist: {alias}") from exc
        if not resolved.is_dir():
            raise GovernanceError(f"source root must be a directory: {alias}")
        roots[alias] = resolved
    return roots


def _repo_source_parts(source_ref: str) -> tuple[str, str, str] | None:
    match = SOURCE_REF_RE.fullmatch(source_ref)
    if not match:
        raise GovernanceError("source_ref must end with @sha256:<64-hex-digest>.")
    locator = source_ref[: source_ref.lower().rfind("@sha256:")]
    parsed = urlsplit(locator)
    if parsed.scheme != "repo":
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not SOURCE_ROOT_ALIAS_RE.fullmatch(parsed.netloc)
    ):
        raise GovernanceError("CAS repo source_ref has an invalid alias or URI component.")
    raw_path = parsed.path
    if not raw_path.startswith("/") or "\\" in raw_path or "%" in raw_path:
        raise GovernanceError("CAS repo source_ref must contain a plain POSIX relative path.")
    pieces = raw_path[1:].split("/")
    if not pieces or any(part in {"", ".", ".."} for part in pieces):
        raise GovernanceError("CAS repo source_ref path is empty or traverses directories.")
    relative = PurePosixPath(*pieces).as_posix()
    return locator, parsed.netloc, relative


def verify_cas_source_refs(
    proposal: dict,
    previous: dict | None,
    source_roots: dict[str, Path],
) -> list[dict]:
    """Verify current repository bytes and same-locator continuity for one CAS proposal."""
    if not source_roots:
        raise GovernanceError("CAS requires at least one operator-configured source root.")
    previous_locators = {
        parts[0]
        for source_ref in (previous or {}).get("source_refs", [])
        if (parts := _repo_source_parts(source_ref)) is not None
    }
    verified: list[dict] = []
    continuity = False
    for source_ref in proposal.get("source_refs", []):
        parts = _repo_source_parts(source_ref)
        if parts is None:
            continue
        locator, alias, relative = parts
        root = source_roots.get(alias)
        if root is None:
            raise GovernanceError(f"CAS source root is not configured: {alias}")
        cursor = root
        for piece in PurePosixPath(relative).parts:
            cursor = cursor / piece
            if cursor.is_symlink():
                raise GovernanceError("CAS source path contains a symlink.")
        try:
            target = cursor.resolve(strict=True)
            target.relative_to(root)
        except (OSError, ValueError) as exc:
            raise GovernanceError("CAS source path is missing or escapes its configured root.") from exc
        if not target.is_file():
            raise GovernanceError("CAS source path must resolve to a regular file.")
        try:
            observed = _hash_bytes(target.read_bytes())
        except OSError as exc:
            raise GovernanceError("CAS source could not be read during verification.") from exc
        expected = source_ref[source_ref.lower().rfind("@sha256:") + 8 :].lower()
        if observed != expected:
            raise GovernanceError("CAS source digest does not match current repository bytes.")
        continuity = continuity or locator in previous_locators
        verified.append({
            "source_ref": source_ref,
            "root_alias": alias,
            "relative_path": relative,
            "observed_sha256": observed,
        })
    if not verified:
        raise GovernanceError("CAS requires at least one verifiable repo:// source_ref.")
    if previous is not None and not continuity:
        raise GovernanceError(
            "CAS requires a verified repo source locator already present on the target claim."
        )
    return verified


def normalize_proposal_submission(request: dict) -> tuple[str, dict, list[dict]]:
    """Validate and normalize the closed Phase 5A submission payload."""
    value = _closed_object(request, SUBMISSION_FIELDS, "proposal submission")
    string_fields = SUBMISSION_FIELDS - {"source_refs", "evidence"}
    invalid = sorted(field for field in string_fields if not isinstance(value.get(field), str))
    if invalid:
        raise GovernanceError(
            "proposal submission fields must be JSON strings: " + ", ".join(invalid)
        )
    submission_id = validate_id(value["submission_id"], "submission")
    project = _project(value["project"])
    sensitivity = value["sensitivity"]
    if sensitivity not in SENSITIVITIES:
        raise GovernanceError(f"sensitivity must be one of: {', '.join(sorted(SENSITIVITIES))}.")
    source_values = value["source_refs"]
    evidence_values = value["evidence"]
    if not isinstance(source_values, list) or not isinstance(evidence_values, list):
        raise GovernanceError("source_refs and evidence must be JSON arrays.")
    normalized_sources = []
    for ordinal, source_value in enumerate(source_values):
        if not isinstance(source_value, str):
            raise GovernanceError(f"source_refs[{ordinal}] must be a JSON string.")
        candidate = _line(source_value, f"source_refs[{ordinal}]", maximum=2000)
        match = SOURCE_REF_RE.fullmatch(candidate)
        if not match:
            raise GovernanceError(
                "source_ref must end with @sha256:<64-hex-digest>."
            )
        locator = candidate[: candidate.lower().rfind("@sha256:")]
        normalized_sources.append(
            _submission_source_uri(locator) + "@sha256:" + match.group(1).lower()
        )
    normalized_sources.sort(key=canonical_json_bytes)
    if len(set(normalized_sources)) != len(normalized_sources):
        raise GovernanceError("Duplicate normalized source_refs are not allowed.")

    normalized_evidence: list[dict] = []
    descriptor_keys: set[bytes] = set()
    for ordinal, descriptor_value in enumerate(evidence_values):
        descriptor = _closed_object(
            descriptor_value,
            EVIDENCE_DESCRIPTOR_FIELDS,
            f"evidence[{ordinal}]",
            optional={"excerpt_sha256"},
        )
        required_strings = {"kind", "source_uri", "content_sha256", "sensitivity"}
        invalid = sorted(field for field in required_strings if not isinstance(descriptor.get(field), str))
        if invalid:
            raise GovernanceError(
                f"evidence[{ordinal}] fields must be JSON strings: {', '.join(invalid)}"
            )
        excerpt = descriptor.get("excerpt_sha256")
        if excerpt is not None and not isinstance(excerpt, str):
            raise GovernanceError(f"evidence[{ordinal}].excerpt_sha256 must be a JSON string or null.")
        kind = descriptor["kind"]
        if kind not in EVIDENCE_KINDS:
            raise GovernanceError(f"evidence[{ordinal}].kind is invalid.")
        descriptor_sensitivity = descriptor["sensitivity"]
        if descriptor_sensitivity not in SENSITIVITIES:
            raise GovernanceError(f"evidence[{ordinal}].sensitivity is invalid.")
        normalized = {
            "kind": kind,
            "source_uri": _submission_source_uri(descriptor["source_uri"]),
            "content_sha256": _sha256(
                descriptor["content_sha256"], "content_sha256"
            ),
            "excerpt_sha256": _sha256(
                descriptor.get("excerpt_sha256"), "excerpt_sha256", optional=True
            ),
            "sensitivity": descriptor_sensitivity,
        }
        try:
            key = canonical_json_bytes(normalized)
        except CanonicalJSONError as exc:
            raise GovernanceError(str(exc)) from exc
        if key in descriptor_keys:
            raise GovernanceError("Duplicate normalized evidence descriptors are not allowed.")
        descriptor_keys.add(key)
        normalized_evidence.append(normalized)
    normalized_evidence.sort(key=canonical_json_bytes)

    rationale = value["rationale"].replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(rationale) > 4000:
        raise GovernanceError("rationale exceeds the 4000-character limit.")
    payload = {
        "project": project,
        "subject": _line(value["subject"], "subject"),
        "predicate": _line(value["predicate"], "predicate"),
        "object": _line(value["object"], "object"),
        "valid_from": _parse_time(value["valid_from"], "valid_from"),
        "sensitivity": sensitivity,
        "rationale": rationale,
        "requested_authority": "agent_proposal",
        "source_refs": normalized_sources,
        "evidence": normalized_evidence,
    }
    try:
        payload_digest = hashlib.sha256(
            SUBMISSION_DIGEST_DOMAIN + canonical_json_bytes(payload)
        ).hexdigest()
    except CanonicalJSONError as exc:
        raise GovernanceError(str(exc)) from exc
    return submission_id, payload, normalized_evidence


def normalize_agent_memory_submission(
    request: dict,
    lifecycle: AgentMemoryLifecyclePolicy,
) -> tuple[str, dict, list[dict]]:
    """Normalize an autonomous leased request, optionally with v4 CAS preconditions."""
    lifecycle.validate()
    value = _closed_object(
        request,
        MEMORY_SUBMISSION_FIELDS,
        "autonomous memory submission",
        optional={"valid_to", "supersedes_claim_id", "expected_claim_sha256"},
    )
    base_request = {field: value[field] for field in SUBMISSION_FIELDS}
    submission_id, payload, descriptors = normalize_proposal_submission(base_request)
    raw_valid_to = value.get("valid_to")
    if raw_valid_to is not None and (
        not isinstance(raw_valid_to, str) or not raw_valid_to.strip()
    ):
        raise GovernanceError("valid_to must be a non-empty timezone-aware JSON string or null.")
    start = datetime.fromisoformat(payload["valid_from"])
    if raw_valid_to is not None:
        valid_to = _parse_time(raw_valid_to, "valid_to")
        end = datetime.fromisoformat(valid_to)
    else:
        try:
            end = start + timedelta(days=lifecycle.default_ttl_days)
        except OverflowError as exc:
            raise GovernanceError(
                "default agent-memory validity exceeds the ISO-8601 timestamp range."
            ) from exc
        valid_to = end.isoformat(timespec="microseconds")
    if end <= start:
        raise GovernanceError("valid_to must be later than valid_from.")
    if end - start > timedelta(days=lifecycle.max_ttl_days):
        raise GovernanceError(
            f"agent-memory validity cannot exceed {lifecycle.max_ttl_days} days."
        )
    payload["valid_to"] = valid_to
    raw_claim_id = value.get("supersedes_claim_id")
    raw_claim_hash = value.get("expected_claim_sha256")
    if (raw_claim_id is None) != (raw_claim_hash is None):
        raise GovernanceError(
            "supersedes_claim_id and expected_claim_sha256 must be supplied together."
        )
    if raw_claim_id is not None:
        if not isinstance(raw_claim_id, str) or not isinstance(raw_claim_hash, str):
            raise GovernanceError(
                "CAS preconditions must be non-empty JSON strings."
            )
        payload["supersedes_claim_id"] = validate_id(raw_claim_id, "claim")
        payload["expected_claim_sha256"] = _sha256(
            raw_claim_hash,
            "expected_claim_sha256",
        )
    return submission_id, payload, descriptors


def submission_replay_key(project: str, submission_id: str) -> str:
    identity = {
        "project": _project(project),
        "submission_id": validate_id(submission_id, "submission"),
    }
    return hashlib.sha256(
        SUBMISSION_RETIREMENT_DOMAIN + canonical_json_bytes(identity)
    ).hexdigest()


def _assert_allowed_project(root: Path, project: str, allowed_projects: set[str]) -> None:
    normalized_allowed = {_project(item) for item in allowed_projects}
    if project not in normalized_allowed:
        raise GovernanceError("project is not enabled by this proposal-only server.")
    target = root / "20_projects" / project
    _relative(root, target)
    if target.is_symlink() or not target.is_dir():
        raise GovernanceError("project must name an existing non-symlink ELM project directory.")


def validate_allowed_projects(root: Path, projects: set[str]) -> frozenset[str]:
    if not projects:
        raise GovernanceError("proposal-only mode requires at least one --allow-project value.")
    if any(not isinstance(project, str) for project in projects):
        raise GovernanceError("proposal-only project allowlist values must be strings.")
    normalized = frozenset(_project(item) for item in projects)
    for project in normalized:
        _assert_allowed_project(root, project, set(normalized))
    return normalized


def _pending_usage(
    root: Path,
    proposals: dict[str, dict],
    evidence: dict[str, dict],
    statuses: dict[str, str],
) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    projects: dict[str, tuple[int, int]] = {}
    root_paths: set[str] = set()
    for proposal_id, proposal in proposals.items():
        if statuses.get(proposal_id) != "pending":
            continue
        paths = {proposal["path"]}
        for evidence_id in proposal.get("evidence_ids", []):
            record = evidence.get(evidence_id)
            if record:
                paths.add(record["path"])
        byte_count = sum((root / path).stat().st_size for path in paths)
        count, total = projects.get(proposal["project"], (0, 0))
        projects[proposal["project"]] = (count + 1, total + byte_count)
        root_paths.update(paths)
    root_bytes = sum((root / path).stat().st_size for path in root_paths)
    return projects, (len(root_paths), root_bytes)


def _verify_versioned_proposal_digest(
    proposal: dict,
    evidence: dict[str, dict],
    tombstones: dict[str, dict],
    status: str,
) -> bool:
    """Verify a proposal-v2/v3/v4 digest, or allow terminal redacted evidence."""
    version = proposal.get("format_version")
    if version not in {
        PROPOSAL_V2_FORMAT_VERSION,
        PROPOSAL_V3_FORMAT_VERSION,
        PROPOSAL_FORMAT_VERSION,
    }:
        raise GovernanceError("Versioned proposal digest verification requires v2, v3, or v4.")
    label = f"Proposal-v{version}"
    descriptors: list[dict] = []
    descriptor_keys: set[bytes] = set()
    has_tombstoned_evidence = False
    for evidence_id in proposal["evidence_ids"]:
        record = evidence.get(evidence_id)
        if record is None:
            tombstone = tombstones.get(evidence_id)
            if not tombstone or tombstone.get("item_type") != "evidence":
                raise GovernanceError(
                    f"{label} references missing evidence without a tombstone: {evidence_id}"
                )
            if status == "pending":
                raise GovernanceError(
                    f"Pending {label.lower()} references tombstoned evidence: {evidence_id}"
                )
            has_tombstoned_evidence = True
            continue
        if record["project"] != proposal["project"]:
            raise GovernanceError(f"{label} evidence belongs to another project: {evidence_id}")
        if record["actor"] != "mcp:unverified":
            raise GovernanceError(f"{label} evidence has invalid provenance: {evidence_id}")
        descriptor = {
            "kind": record["kind"],
            "source_uri": _submission_source_uri(record["source_uri"]),
            "content_sha256": record["content_sha256"],
            "excerpt_sha256": record.get("excerpt_sha256"),
            "sensitivity": record["sensitivity"],
        }
        key = canonical_json_bytes(descriptor)
        if key in descriptor_keys:
            raise GovernanceError(f"{label} resolves to duplicate evidence descriptors.")
        descriptor_keys.add(key)
        descriptors.append(descriptor)
    if descriptors != sorted(descriptors, key=canonical_json_bytes):
        raise GovernanceError(f"{label} evidence descriptors are not in canonical order.")
    if has_tombstoned_evidence:
        return False
    payload = {
        "project": proposal["project"],
        "subject": proposal["subject"],
        "predicate": proposal["predicate"],
        "object": proposal["object"],
        "valid_from": proposal["valid_from"],
        "sensitivity": proposal["sensitivity"],
        "rationale": proposal["rationale"],
        "requested_authority": "agent_proposal",
        "source_refs": proposal["source_refs"],
        "evidence": descriptors,
    }
    digest_domain = SUBMISSION_DIGEST_DOMAIN
    if version in {PROPOSAL_V3_FORMAT_VERSION, PROPOSAL_FORMAT_VERSION}:
        payload["valid_to"] = proposal["valid_to"]
        digest_domain = MEMORY_SUBMISSION_DIGEST_DOMAIN
    if version == PROPOSAL_FORMAT_VERSION:
        payload["supersedes_claim_id"] = proposal["supersedes_claim_id"]
        payload["expected_claim_sha256"] = proposal["expected_claim_sha256"]
        digest_domain = MEMORY_CAS_SUBMISSION_DIGEST_DOMAIN
    digest = hashlib.sha256(
        digest_domain + canonical_json_bytes(payload)
    ).hexdigest()
    if digest != proposal["payload_digest"]:
        raise GovernanceError(
            f"{label} payload digest mismatch: {proposal['proposal_id']}"
        )
    return True


def submit_proposal_bundle(
    root: Path,
    *,
    request: dict,
    request_bytes: int,
    allowed_projects: set[str],
    limits: ProposalLimits,
    agent_lifecycle: AgentMemoryLifecyclePolicy | None = None,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    """Atomically create a proposal-v2, autonomous v3, or CAS proposal-v4 bundle."""
    limits.validate()
    if type(request_bytes) is not int or request_bytes < 1 or request_bytes > limits.max_request_bytes:
        raise GovernanceError(
            f"proposal request exceeds the {limits.max_request_bytes}-byte limit."
        )
    if agent_lifecycle is None:
        proposal_version = PROPOSAL_V2_FORMAT_VERSION
        digest_domain = SUBMISSION_DIGEST_DOMAIN
        submission_id, payload, descriptors = normalize_proposal_submission(request)
    else:
        submission_id, payload, descriptors = normalize_agent_memory_submission(
            request,
            agent_lifecycle,
        )
        if payload.get("supersedes_claim_id"):
            proposal_version = PROPOSAL_FORMAT_VERSION
            digest_domain = MEMORY_CAS_SUBMISSION_DIGEST_DOMAIN
        else:
            proposal_version = PROPOSAL_V3_FORMAT_VERSION
            digest_domain = MEMORY_SUBMISSION_DIGEST_DOMAIN
    if len(payload["source_refs"]) + len(descriptors) > limits.max_reference_count:
        raise GovernanceError(
            f"proposal references exceed the {limits.max_reference_count}-item limit."
        )
    project = payload["project"]
    load_root_identity(root, required=True)
    _assert_allowed_project(root, project, allowed_projects)
    payload_digest = hashlib.sha256(
        digest_domain + canonical_json_bytes(payload)
    ).hexdigest()

    with WriterLock(
        root,
        "proposal-submit",
        timeout=lock_timeout,
        recover_stale=recover_stale,
    ):
        _refuse_pending_transactions(root)
        proposals, evidence, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        replay_key = submission_replay_key(project, submission_id)
        if any(
            item.get("submission_replay_key") == replay_key
            for item in tombstones.values()
        ):
            raise GovernanceError(
                "submission_id was retired by explicit proposal deletion and cannot be reused."
            )
        matches = [
            item for item in proposals.values()
            if item.get("format_version") in {
                PROPOSAL_V2_FORMAT_VERSION,
                PROPOSAL_V3_FORMAT_VERSION,
                PROPOSAL_FORMAT_VERSION,
            }
            and item.get("project") == project
            and item.get("submission_id") == submission_id
        ]
        if len(matches) > 1:
            raise GovernanceError("Canonical submission identity is duplicated; mutation refused.")
        if matches:
            prior = matches[0]
            if prior.get("_payload_digest_verified") is not True:
                raise GovernanceError(
                    "submission_id cannot be replayed because tombstoned evidence prevents digest verification."
                )
            digest_matches = prior.get("payload_digest") == payload_digest
            if (
                not digest_matches
                and prior.get("format_version") == PROPOSAL_V2_FORMAT_VERSION
                and proposal_version == PROPOSAL_V3_FORMAT_VERSION
            ):
                legacy_payload = {
                    key: value for key, value in payload.items() if key != "valid_to"
                }
                legacy_digest = hashlib.sha256(
                    SUBMISSION_DIGEST_DOMAIN + canonical_json_bytes(legacy_payload)
                ).hexdigest()
                digest_matches = prior.get("payload_digest") == legacy_digest
            if not digest_matches:
                raise GovernanceError(
                    "submission_id was already used with a different normalized payload."
                )
            return {
                **{key: value for key, value in prior.items() if not key.startswith("_")},
                "status": statuses[prior["proposal_id"]],
                "idempotent_replay": True,
                "canonical_committed": True,
                "candidate_untrusted": True,
                "authority_warning": "Proposal text is untrusted candidate data, not accepted memory.",
            }

        now = utc_now()
        evidence_records: list[tuple[dict, Path, bytes]] = []
        for descriptor in descriptors:
            evidence_id = new_id("evidence")
            record = {
                "format_version": 1,
                "record_type": "evidence",
                "evidence_id": evidence_id,
                "project": project,
                "kind": descriptor["kind"],
                "source_uri": descriptor["source_uri"],
                "captured_at": now,
                "content_sha256": descriptor["content_sha256"],
                "excerpt_sha256": descriptor["excerpt_sha256"],
                "sensitivity": descriptor["sensitivity"],
                "retention": "reference_only",
                "actor": "mcp:unverified",
            }
            path = evidence_path(root, evidence_id)
            evidence_records.append((record, path, _json_bytes(record)))

        proposal_id = new_id("proposal")
        proposal = {
            "format_version": proposal_version,
            "record_type": "proposal",
            "proposal_id": proposal_id,
            "project": project,
            "subject": payload["subject"],
            "predicate": payload["predicate"],
            "object": payload["object"],
            "proposed_at": now,
            "valid_from": payload["valid_from"],
            "actor": "mcp:unverified",
            "requested_authority": "agent_proposal",
            "sensitivity": payload["sensitivity"],
            "evidence_ids": [item[0]["evidence_id"] for item in evidence_records],
            "source_refs": payload["source_refs"],
            "rationale": payload["rationale"],
            "submission_id": submission_id,
            "payload_digest": payload_digest,
            "source_channel": "mcp",
        }
        if proposal_version in {PROPOSAL_V3_FORMAT_VERSION, PROPOSAL_FORMAT_VERSION}:
            proposal["valid_to"] = payload["valid_to"]
        if proposal_version == PROPOSAL_FORMAT_VERSION:
            proposal["supersedes_claim_id"] = payload["supersedes_claim_id"]
            proposal["expected_claim_sha256"] = payload["expected_claim_sha256"]
        proposal_target = proposal_path(root, project, proposal_id)
        proposal_bytes = _json_bytes(proposal)
        validated_evidence: dict[str, dict] = {}
        for evidence_record, evidence_target, _ in evidence_records:
            validated = _validate_evidence_record(dict(evidence_record), evidence_target)
            validated_evidence[validated["evidence_id"]] = validated
        validated_proposal = _validate_proposal_record(dict(proposal), proposal_target)
        if not _verify_versioned_proposal_digest(
            validated_proposal,
            validated_evidence,
            {},
            "pending",
        ):
            raise GovernanceError(
                f"Generated proposal-v{proposal_version} bundle failed digest verification."
            )
        new_bytes = len(proposal_bytes) + sum(len(item[2]) for item in evidence_records)
        new_records = 1 + len(evidence_records)
        project_usage, root_usage = _pending_usage(root, proposals, evidence, statuses)
        project_count, project_bytes = project_usage.get(project, (0, 0))
        root_records, root_bytes = root_usage
        checks = (
            (project_count + 1, limits.max_pending_per_project, "project pending proposal quota"),
            (project_bytes + new_bytes, limits.max_pending_bytes_per_project, "project pending byte quota"),
            (root_records + new_records, limits.max_pending_records_root, "root pending record quota"),
            (root_bytes + new_bytes, limits.max_pending_bytes_root, "root pending byte quota"),
        )
        for actual, maximum, label in checks:
            if actual > maximum:
                raise GovernanceError(f"{label} exceeded ({actual} > {maximum}).")

        transaction = apply_transaction(
            root,
            transaction_id=new_id("transaction"),
            operation="proposal-submit",
            actor="mcp:unverified",
            changes=[
                *(FileChange(path, data) for _, path, data in evidence_records),
                FileChange(proposal_target, proposal_bytes),
            ],
        )
    return {
        **proposal,
        "path": _relative(root, proposal_target),
        "status": "pending",
        "idempotent_replay": False,
        "canonical_committed": True,
        "candidate_untrusted": True,
        "authority_warning": "Proposal text is untrusted candidate data, not accepted memory.",
        **transaction,
    }


def _metadata_lines(record: dict) -> list[str]:
    summary = f"{record['subject']} {record['predicate']} {record['object']}"
    return [
        f"Title: {record['subject']} {record['predicate']}",
        "Scope: Governed project claim with explicit provenance and temporal state.",
        f"Tags: elm-claim, {record['project']}",
        f"Last updated: {record['transitioned_at'][:10]}",
        f"Status: {record['status']}",
        f"Summary: {summary}",
        f"ELM ID: {record['claim_id']}",
        "Record type: claim",
        f"Project: {record['project']}",
        f"Subject: {record['subject']}",
        f"Predicate: {record['predicate']}",
        f"Object: {record['object']}",
        f"Authority: {record['authority']}",
        f"Valid from: {record['valid_from']}",
        f"Valid to: {record.get('valid_to') or ''}",
        f"Recorded at: {record['recorded_at']}",
        f"Transitioned at: {record['transitioned_at']}",
        f"Proposal ID: {record['proposal_id']}",
        f"Supersedes: {record.get('supersedes') or ''}",
        f"Superseded by: {record.get('superseded_by') or ''}",
        f"Evidence IDs: {json.dumps(record.get('evidence_ids', []), ensure_ascii=False)}",
        f"Source refs: {json.dumps(record.get('source_refs', []), ensure_ascii=False)}",
        f"Sensitivity: {record['sensitivity']}",
        f"Actor: {record['actor']}",
    ]


def render_claim(record: dict) -> bytes:
    rationale = str(record.get("rationale") or "Accepted after explicit governance validation.").strip()
    text = "\n".join(_metadata_lines(record)) + "\n\n# Rationale\n\n" + rationale + "\n"
    return text.encode("utf-8")


CLAIM_META = {
    "title": "title",
    "scope": "scope",
    "tags": "tags",
    "last updated": "last_updated",
    "status": "status",
    "summary": "summary",
    "elm id": "claim_id",
    "record type": "record_type",
    "project": "project",
    "subject": "subject",
    "predicate": "predicate",
    "object": "object",
    "authority": "authority",
    "valid from": "valid_from",
    "valid to": "valid_to",
    "recorded at": "recorded_at",
    "transitioned at": "transitioned_at",
    "proposal id": "proposal_id",
    "supersedes": "supersedes",
    "superseded by": "superseded_by",
    "evidence ids": "evidence_ids",
    "source refs": "source_refs",
    "sensitivity": "sensitivity",
    "actor": "actor",
}


def parse_claim(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise GovernanceError(f"Invalid claim Markdown at {path}: {exc}") from exc
    values: dict[str, str] = {}
    for raw in text.splitlines()[:80]:
        if raw.startswith("#"):
            break
        if not raw.strip():
            continue
        match = re.match(r"^([A-Za-z][A-Za-z ]+):\s*(.*)$", raw)
        if not match:
            raise GovernanceError(f"Invalid claim metadata line at {path}: {raw}")
        key = CLAIM_META.get(match.group(1).strip().lower())
        if key is None:
            raise GovernanceError(f"Unknown claim metadata field at {path}: {match.group(1)}")
        values[key] = match.group(2).strip()
    required = {
        "claim_id", "record_type", "project", "subject", "predicate", "object",
        "status", "authority", "valid_from", "recorded_at", "transitioned_at",
        "proposal_id", "sensitivity", "actor",
    }
    missing = sorted(key for key in required if not values.get(key))
    if missing:
        raise GovernanceError(f"Claim is missing required fields at {path}: {', '.join(missing)}")
    if values["record_type"] != "claim":
        raise GovernanceError(f"Claim record_type must be claim at {path}.")
    values["claim_id"] = validate_id(values["claim_id"], "claim")
    values["proposal_id"] = validate_id(values["proposal_id"], "proposal")
    values["project"] = _project(values["project"])
    if path.stem.lower() != values["claim_id"] or path.parent.parent.name != values["project"]:
        raise GovernanceError(f"Claim path does not match its ID/project: {path}")
    if values["status"] not in CLAIM_STATUSES:
        raise GovernanceError(f"Invalid claim status at {path}: {values['status']}")
    if values["authority"] not in CLAIM_AUTHORITIES:
        raise GovernanceError(f"Invalid claim authority at {path}: {values['authority']}")
    if values["sensitivity"] not in SENSITIVITIES:
        raise GovernanceError(f"Invalid claim sensitivity at {path}: {values['sensitivity']}")
    for field in ("valid_from", "recorded_at", "transitioned_at"):
        values[field] = _parse_time(values[field], field)
    values["valid_to"] = _parse_time(values["valid_to"], "valid_to") if values.get("valid_to") else None
    values["supersedes"] = validate_id(values["supersedes"], "claim") if values.get("supersedes") else None
    values["superseded_by"] = validate_id(values["superseded_by"], "claim") if values.get("superseded_by") else None
    try:
        values["evidence_ids"] = _validate_evidence_ids(json.loads(values.get("evidence_ids") or "[]"))
        values["source_refs"] = _validate_source_refs(json.loads(values.get("source_refs") or "[]"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GovernanceError(f"Claim list metadata is invalid at {path}.") from exc
    marker = "# Rationale"
    values["rationale"] = text.split(marker, 1)[1].strip() if marker in text else ""
    values["content_sha256"] = _hash_bytes(path.read_bytes())
    return values


@dataclass(frozen=True)
class FileChange:
    path: Path
    payload: bytes | None


def _journal_path(root: Path, transaction_id: str) -> Path:
    return root / "01_inbox" / "elm_transactions" / f"{validate_id(transaction_id, 'transaction')}.json"


def _rollback_manifest(root: Path, manifest: dict) -> None:
    for item in reversed(manifest.get("changes", [])):
        target = _target(root, item["target"])
        if item["before_exists"]:
            backup = _target(root, item["backup"])
            if not backup.is_file():
                raise GovernanceError(f"Transaction backup is missing: {backup}")
            backup_bytes = backup.read_bytes()
            if _hash_bytes(backup_bytes) != item.get("before_sha256"):
                raise GovernanceError(f"Transaction backup hash mismatch: {backup}")
            if target.exists():
                current_hash = _hash_bytes(target.read_bytes())
                allowed = {item.get("before_sha256"), item.get("after_sha256")}
                if current_hash not in allowed:
                    raise GovernanceError(
                        f"Refusing to overwrite an unexpectedly changed transaction target: {target}"
                    )
            atomic_write_bytes(target, backup_bytes)
            continue
        if not target.exists():
            continue
        current_hash = _hash_bytes(target.read_bytes())
        if current_hash != item["after_sha256"]:
            raise GovernanceError(
                f"Refusing to remove an unexpectedly changed transaction target: {target}"
            )
        target.unlink()


def _cleanup_transaction_backups(root: Path, manifest: dict) -> list[str]:
    directories: set[Path] = set()
    for item in manifest.get("changes", []):
        relative = item.get("backup")
        if not relative:
            continue
        backup = _target(root, relative)
        directories.add(backup.parent)
        try:
            backup.unlink()
        except (FileNotFoundError, OSError):
            pass
    for directory in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass
    return [
        str(item["backup"])
        for item in manifest.get("changes", [])
        if item.get("backup") and _target(root, item["backup"]).exists()
    ]


def _verify_committed_manifest(root: Path, manifest: dict) -> None:
    for item in manifest.get("changes", []):
        target = _target(root, item["target"])
        if item["action"] == "delete":
            if target.exists():
                raise GovernanceError(
                    f"Committed transaction delete target still exists: {target}"
                )
            continue
        if not target.is_file():
            raise GovernanceError(f"Committed transaction target is missing: {target}")
        if _hash_bytes(target.read_bytes()) != item.get("after_sha256"):
            raise GovernanceError(
                f"Committed transaction target hash mismatch: {target}"
            )


def recover_incomplete_transactions(root: Path) -> list[str]:
    directory = root / "01_inbox" / "elm_transactions"
    recovered: list[str] = []
    if not directory.is_dir():
        return recovered
    for path in sorted(directory.glob("transaction_*.json")):
        manifest = _validate_transaction_record(_load_json(path, "transaction"), path)
        if manifest["state"] == "committed":
            _verify_committed_manifest(root, manifest)
        else:
            _rollback_manifest(root, manifest)
        retained = _cleanup_transaction_backups(root, manifest)
        if retained:
            raise GovernanceError(
                "Transaction recovery could not remove backup paths: " + ", ".join(retained)
            )
        path.unlink()
        recovered.append(str(manifest["transaction_id"]))
    return recovered


def pending_transactions(root: Path) -> list[dict]:
    records: list[dict] = []
    for path in _iter_json(root, "01_inbox/elm_transactions", "transaction_*.json"):
        manifest = _validate_transaction_record(_load_json(path, "transaction"), path)
        records.append({
            "transaction_id": validate_id(manifest.get("transaction_id", ""), "transaction"),
            "operation": manifest.get("operation"),
            "created_at": manifest.get("created_at"),
            "actor": manifest.get("actor"),
            "state": manifest.get("state"),
            "recovery_action": (
                "finish_commit_cleanup" if manifest.get("state") == "committed" else "rollback"
            ),
            "path": _relative(root, path),
            "targets": [item.get("target") for item in manifest.get("changes", [])],
        })
    return records


def _refuse_pending_transactions(root: Path) -> None:
    pending = pending_transactions(root)
    if pending:
        raise GovernanceError(
            "Incomplete canonical governance transaction exists; preview it with "
            "elm recover --dry-run and recover it explicitly before another lifecycle mutation."
        )


def recover_governance_transactions(
    root: Path,
    *,
    apply: bool,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    preview = pending_transactions(root)
    if not apply:
        return {"mode": "dry-run", "pending_count": len(preview), "transactions": preview, "recovered": []}
    with WriterLock(root, "governance-recovery", timeout=lock_timeout, recover_stale=recover_stale):
        recovered = recover_incomplete_transactions(root)
    return {"mode": "apply", "pending_count": len(preview), "transactions": preview, "recovered": recovered}


def apply_transaction(
    root: Path,
    *,
    transaction_id: str,
    operation: str,
    actor: str,
    changes: list[FileChange],
) -> dict:
    transaction_id = validate_id(transaction_id, "transaction")
    backup_root = root / "backups" / "elm-governance" / transaction_id
    manifest_changes: list[dict] = []
    seen: set[str] = set()
    for ordinal, change in enumerate(changes):
        relative = _relative(root, change.path)
        if relative.startswith(".elm/"):
            raise GovernanceError("Canonical transactions cannot target disposable .elm state.")
        if relative in seen:
            raise GovernanceError(f"Duplicate transaction target: {relative}")
        seen.add(relative)
        before_exists = change.path.is_file()
        backup_relative = None
        before_sha = None
        if before_exists:
            before = change.path.read_bytes()
            before_sha = _hash_bytes(before)
            backup = backup_root / f"{ordinal:03d}-{change.path.name}.bak"
            atomic_write_bytes(backup, before)
            backup_relative = _relative(root, backup)
        after_hash = _hash_bytes(change.payload) if change.payload is not None else None
        manifest_changes.append({
            "target": relative,
            "action": "write" if change.payload is not None else "delete",
            "before_exists": before_exists,
            "before_sha256": before_sha,
            "after_sha256": after_hash,
            "backup": backup_relative,
        })
    manifest = {
        "format_version": CANONICAL_FORMAT_VERSION,
        "record_type": "transaction",
        "transaction_id": transaction_id,
        "operation": _line(operation, "operation", maximum=100),
        "actor": _line(actor, "actor", maximum=200),
        "created_at": utc_now(),
        "state": "prepared",
        "changes": manifest_changes,
    }
    journal = _journal_path(root, transaction_id)
    try:
        _atomic_new(journal, _json_bytes(manifest))
    except BaseException:
        _cleanup_transaction_backups(root, manifest)
        raise
    try:
        for change in changes:
            if change.payload is None:
                try:
                    change.path.unlink()
                except FileNotFoundError:
                    pass
            else:
                atomic_write_bytes(change.path, change.payload)
    except BaseException:
        _rollback_manifest(root, manifest)
        _cleanup_transaction_backups(root, manifest)
        try:
            journal.unlink()
        except FileNotFoundError:
            pass
        raise
    manifest["state"] = "committed"
    atomic_write_bytes(journal, _json_bytes(manifest))
    retained_backups = _cleanup_transaction_backups(root, manifest)
    if not retained_backups:
        journal.unlink()
    return {
        "transaction_id": transaction_id,
        "recovery_backup_retained": bool(retained_backups),
        "retained_backup_paths": retained_backups,
        "changed_paths": [item["target"] for item in manifest_changes],
    }


def _iter_json(root: Path, relative: str, pattern: str) -> list[Path]:
    directory = _target(root, relative)
    return sorted(directory.rglob(pattern)) if directory.is_dir() else []


def load_governance(root: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[str, dict]]:
    proposals: dict[str, dict] = {}
    evidence: dict[str, dict] = {}
    events: dict[str, dict] = {}
    tombstones: dict[str, dict] = {}
    for path in _iter_json(root, "01_inbox/elm_proposals", "proposal_*.json"):
        record = _validate_proposal_record(_load_json(path, "proposal"), path)
        record["path"] = _relative(root, path)
        if record["proposal_id"] in proposals:
            raise GovernanceError(f"Duplicate proposal ID: {record['proposal_id']}")
        proposals[record["proposal_id"]] = record
    for path in _iter_json(root, "40_sources/elm_evidence/metadata", "evidence_*.json"):
        record = _validate_evidence_record(_load_json(path, "evidence"), path)
        record["path"] = _relative(root, path)
        if record["evidence_id"] in evidence:
            raise GovernanceError(f"Duplicate evidence ID: {record['evidence_id']}")
        evidence[record["evidence_id"]] = record
    for path in _iter_json(root, "30_agent_logs/elm_events", "event_*.json"):
        record = _validate_event_record(_load_json(path, "event"), path)
        record["path"] = _relative(root, path)
        if record["event_id"] in events:
            raise GovernanceError(f"Duplicate event ID: {record['event_id']}")
        events[record["event_id"]] = record
    for path in _iter_json(root, "30_agent_logs/elm_tombstones", "*.json"):
        record = _validate_tombstone_record(_load_json(path, "tombstone"), path)
        record["path"] = _relative(root, path)
        item_id = _line(record.get("item_id", ""), "item_id")
        if item_id in tombstones:
            raise GovernanceError(f"Duplicate tombstone item ID: {item_id}")
        tombstones[item_id] = record
    statuses = proposal_statuses(proposals, events, tombstones)
    submission_keys: set[tuple[str, str]] = set()
    for proposal in proposals.values():
        if proposal.get("format_version") not in {
            PROPOSAL_V2_FORMAT_VERSION,
            PROPOSAL_V3_FORMAT_VERSION,
            PROPOSAL_FORMAT_VERSION,
        }:
            continue
        key = (proposal["project"], proposal["submission_id"])
        if key in submission_keys:
            raise GovernanceError(
                f"Duplicate proposal submission identity: {proposal['project']}/{proposal['submission_id']}"
            )
        submission_keys.add(key)
        proposal["_payload_digest_verified"] = _verify_versioned_proposal_digest(
            proposal,
            evidence,
            tombstones,
            statuses[proposal["proposal_id"]],
        )
    return proposals, evidence, events, tombstones


def proposal_statuses(
    proposals: dict[str, dict],
    events: dict[str, dict],
    tombstones: dict[str, dict] | None = None,
) -> dict[str, str]:
    statuses = {proposal_id: "pending" for proposal_id in proposals}
    terminal: dict[str, str] = {}
    ordered = sorted(events.values(), key=lambda item: (item.get("occurred_at", ""), item["event_id"]))
    for event in ordered:
        proposal_id = event.get("proposal_id")
        status = TERMINAL_PROPOSAL_ACTIONS.get(event.get("action"))
        if not proposal_id or not status:
            continue
        if proposal_id not in statuses and proposal_id in (tombstones or {}):
            continue
        if proposal_id not in statuses:
            raise GovernanceError(f"Audit event references a missing proposal: {proposal_id}")
        if proposal_id in terminal:
            raise GovernanceError(f"Proposal has multiple terminal lifecycle events: {proposal_id}")
        terminal[proposal_id] = event["event_id"]
        statuses[proposal_id] = status
    return statuses


def _load_claims(root: Path) -> dict[str, dict]:
    claims: dict[str, dict] = {}
    projects = root / "20_projects"
    if not projects.is_dir():
        return claims
    for path in sorted(projects.glob("*/CLAIMS/claim_*.md")):
        record = parse_claim(path)
        record["path"] = _relative(root, path)
        if record["claim_id"] in claims:
            raise GovernanceError(f"Duplicate claim ID: {record['claim_id']}")
        claims[record["claim_id"]] = record
    return claims


def _redacted_locator(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.query:
        return value
    keys = sorted({item.split("=", 1)[0] for item in parsed.query.split("&") if item})
    query = "&".join(f"{key}=<redacted>" for key in keys)
    return parsed._replace(query=query).geturl()


def _redacted_source_ref(value: str) -> str:
    marker = "@sha256:"
    index = value.lower().rfind(marker)
    if index < 0:
        return "<invalid-source-ref>"
    return _redacted_locator(value[:index]) + value[index:]


def preview_proposal_transition(
    root: Path,
    *,
    proposal_id: str,
    project: str,
    allowed_projects: set[str] | None = None,
) -> dict:
    """Build the Phase 5A non-signable acceptance review plan."""
    root_identity = load_root_identity(root, required=True)
    normalized_project = _project(project)
    if allowed_projects is not None:
        _assert_allowed_project(root, normalized_project, allowed_projects)
    _refuse_pending_transactions(root)
    proposals, evidence, events, tombstones = load_governance(root)
    normalized_id = validate_id(proposal_id, "proposal")
    proposal = proposals.get(normalized_id)
    if proposal is None or proposal.get("project") != normalized_project:
        raise GovernanceError("Proposal does not exist in the requested project.")
    statuses = proposal_statuses(proposals, events, tombstones)
    claims = _load_claims(root)
    current = sorted(
        (
            {
                "claim_id": item["claim_id"],
                "object": item["object"],
                "status": item["status"],
                "content_sha256": item["content_sha256"],
                "path": item["path"],
            }
            for item in claims.values()
            if item["project"] == normalized_project
            and item["subject"] == proposal["subject"]
            and item["predicate"] == proposal["predicate"]
            and item["status"] == "accepted"
        ),
        key=lambda item: item["claim_id"],
    )
    locators = []
    for evidence_id in proposal.get("evidence_ids", []):
        record = evidence.get(evidence_id)
        if record:
            locators.append({
                "evidence_id": evidence_id,
                "kind": record["kind"],
                "source_uri": _redacted_locator(record["source_uri"]),
                "content_sha256": record["content_sha256"],
                "excerpt_sha256": record.get("excerpt_sha256"),
                "retention": "reference_only",
            })
    proposal_hash = _hash_bytes((root / proposal["path"]).read_bytes())
    return {
        "review_plan": {
            "format_version": 1,
            "signable": False,
            "action": "accept",
            "root_id": root_identity["root_id"],
            "project": normalized_project,
            "proposal_id": normalized_id,
            "proposal_status": statuses[normalized_id],
            "proposal_sha256": proposal_hash,
            "candidate": {
                "subject": proposal["subject"],
                "predicate": proposal["predicate"],
                "object": proposal["object"],
                "valid_from": proposal["valid_from"],
                "sensitivity": proposal["sensitivity"],
                "rationale": proposal.get("rationale", ""),
                "requested_authority": proposal["requested_authority"],
            },
            "evidence_locators": locators,
            "source_refs": [
                _redacted_source_ref(value) for value in proposal.get("source_refs", [])
            ],
            "before": {"accepted_claims": current},
            "after": {
                "effect": "would_create_an_accepted_claim_only_after_separate_ratification",
                "candidate_object": proposal["object"],
            },
        },
        "candidate_untrusted": True,
        "authority_warning": (
            "This preview is not an approval grant and cannot authorize accepted-state mutation."
        ),
    }


def _event_record(
    *,
    action: str,
    actor: str,
    transaction_id: str,
    project: str,
    proposal_id: str | None = None,
    claim_id: str | None = None,
    previous_claim_id: str | None = None,
    target_id: str | None = None,
    authority: str | None = None,
    reason_code: str | None = None,
    previous_sha256: str | None = None,
    current_sha256: str | None = None,
) -> tuple[dict, Path]:
    occurred_at = utc_now()
    event_id = new_id("event")
    record = {
        "format_version": CANONICAL_FORMAT_VERSION,
        "record_type": "event",
        "event_id": event_id,
        "action": action,
        "occurred_at": occurred_at,
        "actor": _line(actor, "actor", maximum=200),
        "transaction_id": validate_id(transaction_id, "transaction"),
        "project": _project(project),
        "proposal_id": proposal_id,
        "claim_id": claim_id,
        "previous_claim_id": previous_claim_id,
        "target_id": target_id,
        "authority": authority,
        "reason_code": reason_code,
        "previous_sha256": previous_sha256,
        "current_sha256": current_sha256,
    }
    return record, event_path(Path("."), occurred_at, event_id)


def _assert_reason(reason_code: str) -> str:
    if reason_code not in REASON_CODES:
        raise GovernanceError(f"reason_code must be one of: {', '.join(sorted(REASON_CODES))}.")
    return reason_code


def _find_proposal(proposals: dict[str, dict], proposal_id: str) -> dict:
    proposal_id = validate_id(proposal_id, "proposal")
    try:
        return proposals[proposal_id]
    except KeyError as exc:
        raise GovernanceError(f"Proposal not found: {proposal_id}") from exc


def _find_claim(claims: dict[str, dict], claim_id: str) -> dict:
    claim_id = validate_id(claim_id, "claim")
    try:
        return claims[claim_id]
    except KeyError as exc:
        raise GovernanceError(f"Claim not found: {claim_id}") from exc


def _assert_pending(proposal_id: str, statuses: dict[str, str]) -> None:
    status = statuses.get(proposal_id)
    if status != "pending":
        raise GovernanceError(f"Proposal is not pending: {proposal_id} ({status or 'missing'}).")


def _assert_evidence(root: Path, proposal: dict, evidence: dict[str, dict]) -> None:
    for evidence_id in _validate_evidence_ids(proposal.get("evidence_ids")):
        record = evidence.get(evidence_id)
        if record is None:
            raise GovernanceError(f"Proposal references missing evidence: {evidence_id}")
        if record.get("project") != proposal["project"]:
            raise GovernanceError(f"Proposal references cross-project evidence: {evidence_id}")
        if (root / record["path"]).is_file() is False:
            raise GovernanceError(f"Evidence path is missing: {evidence_id}")


def accept_proposal(
    root: Path,
    *,
    proposal_id: str,
    actor: str,
    authority: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    if authority not in ACCEPTED_AUTHORITIES:
        raise GovernanceError(f"authority must be one of: {', '.join(sorted(ACCEPTED_AUTHORITIES))}.")
    with WriterLock(root, "claim-accept", timeout=lock_timeout, recover_stale=recover_stale):
        _refuse_pending_transactions(root)
        proposals, evidence, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        proposal = _find_proposal(proposals, proposal_id)
        _assert_pending(proposal["proposal_id"], statuses)
        _assert_evidence(root, proposal, evidence)
        claim_id = new_id("claim")
        now = utc_now()
        claim = {
            "claim_id": claim_id,
            "project": proposal["project"],
            "subject": proposal["subject"],
            "predicate": proposal["predicate"],
            "object": proposal["object"],
            "status": "accepted",
            "authority": authority,
            "valid_from": _parse_time(proposal["valid_from"], "valid_from"),
            "valid_to": proposal.get("valid_to"),
            "recorded_at": now,
            "transitioned_at": now,
            "proposal_id": proposal["proposal_id"],
            "supersedes": None,
            "superseded_by": None,
            "evidence_ids": proposal.get("evidence_ids", []),
            "source_refs": proposal.get("source_refs", []),
            "sensitivity": proposal["sensitivity"],
            "actor": _line(actor, "actor", maximum=200),
            "rationale": proposal.get("rationale") or "Accepted after explicit governance validation.",
        }
        claim_payload = render_claim(claim)
        transaction_id = new_id("transaction")
        event, event_relative = _event_record(
            action="proposal_accepted",
            actor=actor,
            transaction_id=transaction_id,
            project=proposal["project"],
            proposal_id=proposal["proposal_id"],
            claim_id=claim_id,
            authority=authority,
            current_sha256=_hash_bytes(claim_payload),
        )
        event_target = root / event_relative
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation="proposal-accept",
            actor=actor,
            changes=[
                FileChange(claim_path(root, proposal["project"], claim_id), claim_payload),
                FileChange(event_target, _json_bytes(event)),
            ],
        )
    return {
        "action": "accepted",
        "proposal_id": proposal["proposal_id"],
        "claim_id": claim_id,
        "authority": authority,
        **transaction,
    }


def _overlapping_current_claims(
    claims: dict[str, dict],
    proposal: dict,
    *,
    at: str | None = None,
) -> list[dict]:
    candidate_start = _parse_time(proposal["valid_from"], "valid_from")
    moment = at or utc_now()
    matches = []
    for claim in claims.values():
        if claim["status"] != "accepted":
            continue
        if claim["valid_from"] > moment:
            continue
        if claim.get("valid_to") and claim["valid_to"] <= moment:
            continue
        if (
            claim["project"],
            claim["subject"],
            claim["predicate"],
        ) != (
            proposal["project"],
            proposal["subject"],
            proposal["predicate"],
        ):
            continue
        claim_end = claim.get("valid_to") or "9999-12-31T23:59:59.999999+00:00"
        if candidate_start < claim_end:
            matches.append(claim)
    authority_rank = {
        "verified_repository_state": 0,
        "user_ratified": 1,
        "ratified_project_decision": 2,
        AGENT_MEMORY_AUTHORITY: 3,
    }
    return sorted(
        matches,
        key=lambda item: (
            authority_rank.get(item["authority"], 99),
            item["recorded_at"],
            item["claim_id"],
        ),
    )


def _agent_memory_terminal_result(
    proposal: dict,
    events: dict[str, dict],
    claims: dict[str, dict],
) -> dict:
    moment = utc_now()
    related_events = sorted(
        (
            event
            for event in events.values()
            if event.get("proposal_id") == proposal["proposal_id"]
            and event.get("action") in TERMINAL_PROPOSAL_ACTIONS
        ),
        key=lambda item: (item.get("occurred_at", ""), item["event_id"]),
    )
    if len(related_events) != 1:
        raise GovernanceError(
            "Autonomous memory proposal has ambiguous terminal history; mutation refused."
        )
    event = related_events[0]
    if event["action"] in {"proposal_accepted", "claim_superseded"}:
        claim_id = event.get("claim_id")
        claim = claims.get(claim_id or "")
        if claim is None:
            raise GovernanceError(
                "Accepted autonomous memory proposal has no canonical claim."
            )
        if claim["status"] != "accepted":
            terminal_status = claim["status"]
        elif claim["valid_from"] > moment:
            terminal_status = "future"
        elif claim.get("valid_to") and claim["valid_to"] <= moment:
            terminal_status = "expired"
        else:
            terminal_status = "accepted"
        autonomous_acceptance = (
            event["action"] in {"proposal_accepted", "claim_superseded"}
            and event.get("actor") == "mcp:autonomous"
            and event.get("authority") == AGENT_MEMORY_AUTHORITY
            and claim["authority"] == AGENT_MEMORY_AUTHORITY
            and claim.get("actor") == "mcp:autonomous"
            and claim.get("proposal_id") == proposal["proposal_id"]
        )
        if autonomous_acceptance and terminal_status == "accepted":
            previous = claims.get(event.get("previous_claim_id") or "")
            if event["action"] == "claim_superseded":
                action = (
                    "renewed"
                    if previous is not None and previous["object"] == claim["object"]
                    else "superseded"
                )
            else:
                action = "remembered"
            return {
                "action": action,
                "proposal_id": proposal["proposal_id"],
                "claim_id": claim["claim_id"],
                "previous_claim_id": event.get("previous_claim_id"),
                "authority": claim["authority"],
                "valid_to": claim.get("valid_to"),
                "source_verification_status": (
                    "verified_at_transition"
                    if event["action"] == "claim_superseded"
                    else "not_requested"
                ),
                "verified_source_refs": (
                    [
                        source_ref
                        for source_ref in proposal.get("source_refs", [])
                        if _repo_source_parts(source_ref) is not None
                    ]
                    if event["action"] == "claim_superseded"
                    else []
                ),
                "candidate_activated": True,
                "conflict_detected": False,
                "terminal_status": terminal_status,
                "activation_replay": True,
            }
        return {
            "action": (
                "existing_governed_memory"
                if terminal_status == "accepted"
                else "inactive_terminal"
            ),
            "proposal_id": proposal["proposal_id"],
            "candidate_activated": False,
            "conflict_detected": False,
            "existing_claim_id": claim["claim_id"],
            "existing_authority": claim["authority"],
            "terminal_status": terminal_status,
            "activation_replay": True,
        }
    existing = _overlapping_current_claims(claims, proposal, at=moment)
    same = next((item for item in existing if item["object"] == proposal["object"]), None)
    selected = claims.get(event.get("target_id") or "") or same or (
        existing[0] if existing else None
    )
    autonomous_terminal = event.get("actor") == "mcp:autonomous"
    if (
        autonomous_terminal
        and event["action"] == "proposal_rejected"
        and event.get("reason_code") == "duplicate"
    ):
        action = "duplicate"
        conflict = False
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "contradicted"
    ):
        action = "conflict_deferred"
        conflict = True
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "quota_exceeded"
    ):
        action = "quota_deferred"
        conflict = False
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "stale_cas_target"
    ):
        action = "stale_cas_deferred"
        conflict = True
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "source_verification_failed"
    ):
        action = "source_verification_deferred"
        conflict = False
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "out_of_scope"
        and proposal["valid_from"] > moment
    ):
        action = "future_deferred"
        conflict = False
    elif (
        autonomous_terminal
        and event["action"] == "proposal_deferred"
        and event.get("reason_code") == "out_of_scope"
        and proposal.get("valid_to")
        and proposal["valid_to"] <= moment
    ):
        action = "expired_deferred"
        conflict = False
    elif event["action"] == "proposal_rejected":
        action = "terminal_rejected"
        conflict = False
    elif event["action"] == "proposal_deferred":
        action = "terminal_deferred"
        conflict = False
    else:
        raise GovernanceError(
            "Autonomous memory proposal ended outside the supported lifecycle."
        )
    return {
        "action": action,
        "proposal_id": proposal["proposal_id"],
        "candidate_activated": False,
        "conflict_detected": conflict,
        "quota_exceeded": action == "quota_deferred",
        "quota_message": (
            "active agent-memory quota was exceeded at the original attempt."
            if action == "quota_deferred"
            else None
        ),
        "existing_claim_id": selected["claim_id"] if selected else None,
        "existing_authority": selected["authority"] if selected else None,
        "terminal_status": TERMINAL_PROPOSAL_ACTIONS[event["action"]],
        "terminal_reason_code": event.get("reason_code"),
        "source_verification_status": (
            "failed"
            if action == "source_verification_deferred"
            else "not_completed" if action == "stale_cas_deferred" else "not_requested"
        ),
        "activation_replay": True,
    }


def _defer_autonomous_proposal(
    root: Path,
    proposal: dict,
    *,
    reason_code: str,
    action: str,
    operation: str,
    target_id: str | None = None,
    existing_authority: str | None = None,
    conflict_detected: bool = False,
    source_verification_status: str = "not_requested",
) -> dict:
    transaction_id = new_id("transaction")
    event, relative = _event_record(
        action="proposal_deferred",
        actor="mcp:autonomous",
        transaction_id=transaction_id,
        project=proposal["project"],
        proposal_id=proposal["proposal_id"],
        target_id=target_id,
        reason_code=reason_code,
    )
    transaction = apply_transaction(
        root,
        transaction_id=transaction_id,
        operation=operation,
        actor="mcp:autonomous",
        changes=[FileChange(root / relative, _json_bytes(event))],
    )
    return {
        "action": action,
        "proposal_id": proposal["proposal_id"],
        "candidate_activated": False,
        "conflict_detected": conflict_detected,
        "existing_claim_id": target_id,
        "existing_authority": existing_authority,
        "terminal_status": "deferred",
        "terminal_reason_code": reason_code,
        "source_verification_status": source_verification_status,
        "activation_replay": False,
        **transaction,
    }


def activate_agent_memory(
    root: Path,
    *,
    proposal_id: str,
    limits: AgentMemoryLimits,
    source_roots: dict[str, Path],
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    """Activate one proposal as low-authority agent memory without a per-item human gate.

    Exact duplicates reuse the stronger/current claim. A different object for an
    overlapping subject/predicate is deferred instead of silently overwriting or
    creating contradictory active memory.
    """
    limits.validate()
    actor = "mcp:autonomous"
    with WriterLock(
        root,
        "agent-memory-activate",
        timeout=lock_timeout,
        recover_stale=recover_stale,
    ):
        _refuse_pending_transactions(root)
        proposals, evidence, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        proposal = _find_proposal(proposals, proposal_id)
        status = statuses[proposal["proposal_id"]]
        claims = _load_claims(root)
        if status != "pending":
            return _agent_memory_terminal_result(proposal, events, claims)
        _assert_evidence(root, proposal, evidence)

        moment = utc_now()
        if proposal["valid_from"] > moment:
            transaction_id = new_id("transaction")
            event, relative = _event_record(
                action="proposal_deferred",
                actor=actor,
                transaction_id=transaction_id,
                project=proposal["project"],
                proposal_id=proposal["proposal_id"],
                reason_code="out_of_scope",
            )
            transaction = apply_transaction(
                root,
                transaction_id=transaction_id,
                operation="agent-memory-future-defer",
                actor=actor,
                changes=[FileChange(root / relative, _json_bytes(event))],
            )
            return {
                "action": "future_deferred",
                "proposal_id": proposal["proposal_id"],
                "candidate_activated": False,
                "conflict_detected": False,
                "terminal_status": "deferred",
                "terminal_reason_code": "out_of_scope",
                "activation_replay": False,
                **transaction,
            }

        if proposal.get("valid_to") and proposal["valid_to"] <= moment:
            transaction_id = new_id("transaction")
            event, relative = _event_record(
                action="proposal_deferred",
                actor=actor,
                transaction_id=transaction_id,
                project=proposal["project"],
                proposal_id=proposal["proposal_id"],
                reason_code="out_of_scope",
            )
            transaction = apply_transaction(
                root,
                transaction_id=transaction_id,
                operation="agent-memory-expired-defer",
                actor=actor,
                changes=[FileChange(root / relative, _json_bytes(event))],
            )
            return {
                "action": "expired_deferred",
                "proposal_id": proposal["proposal_id"],
                "candidate_activated": False,
                "conflict_detected": False,
                "terminal_status": "deferred",
                "terminal_reason_code": "out_of_scope",
                "activation_replay": False,
                **transaction,
            }

        if proposal.get("format_version") == PROPOSAL_FORMAT_VERSION:
            previous_id = proposal["supersedes_claim_id"]
            previous = claims.get(previous_id)
            current = _overlapping_current_claims(claims, proposal, at=moment)
            stale = (
                previous is None
                or previous["status"] != "accepted"
                or previous["authority"] != AGENT_MEMORY_AUTHORITY
                or previous["valid_from"] > moment
                or (previous.get("valid_to") is not None and previous["valid_to"] <= moment)
                or previous["content_sha256"] != proposal["expected_claim_sha256"]
                or previous["project"] != proposal["project"]
                or (previous["subject"], previous["predicate"])
                != (proposal["subject"], proposal["predicate"])
                or proposal["valid_from"] <= previous["valid_from"]
                or any(item["claim_id"] != previous_id for item in current)
                or not any(item["claim_id"] == previous_id for item in current)
            )
            if stale:
                return _defer_autonomous_proposal(
                    root,
                    proposal,
                    reason_code="stale_cas_target",
                    action="stale_cas_deferred",
                    operation="agent-memory-cas-stale-defer",
                    target_id=previous_id,
                    existing_authority=previous["authority"] if previous is not None else None,
                    conflict_detected=True,
                    source_verification_status="not_completed",
                )
            assert previous is not None
            try:
                verified_sources = verify_cas_source_refs(
                    proposal,
                    previous,
                    source_roots,
                )
            except GovernanceError:
                return _defer_autonomous_proposal(
                    root,
                    proposal,
                    reason_code="source_verification_failed",
                    action="source_verification_deferred",
                    operation="agent-memory-cas-source-defer",
                    target_id=previous_id,
                    existing_authority=previous["authority"],
                    source_verification_status="failed",
                )

            now = utc_now()
            claim_id = new_id("claim")
            new_claim = {
                "claim_id": claim_id,
                "project": proposal["project"],
                "subject": proposal["subject"],
                "predicate": proposal["predicate"],
                "object": proposal["object"],
                "status": "accepted",
                "authority": AGENT_MEMORY_AUTHORITY,
                "valid_from": proposal["valid_from"],
                "valid_to": proposal["valid_to"],
                "recorded_at": now,
                "transitioned_at": now,
                "proposal_id": proposal["proposal_id"],
                "supersedes": previous_id,
                "superseded_by": None,
                "evidence_ids": proposal.get("evidence_ids", []),
                "source_refs": proposal.get("source_refs", []),
                "sensitivity": proposal["sensitivity"],
                "actor": actor,
                "rationale": proposal.get("rationale")
                or "Source-verified autonomous compare-and-swap replacement.",
            }
            old_updated = dict(previous)
            old_updated.update({
                "status": "superseded",
                "valid_to": proposal["valid_from"],
                "transitioned_at": now,
                "superseded_by": claim_id,
                "actor": actor,
            })
            old_target = root / previous["path"]
            old_payload = render_claim(old_updated)
            new_payload = render_claim(new_claim)
            transaction_id = new_id("transaction")
            event, relative = _event_record(
                action="claim_superseded",
                actor=actor,
                transaction_id=transaction_id,
                project=proposal["project"],
                proposal_id=proposal["proposal_id"],
                claim_id=claim_id,
                previous_claim_id=previous_id,
                authority=AGENT_MEMORY_AUTHORITY,
                previous_sha256=previous["content_sha256"],
                current_sha256=_hash_bytes(new_payload),
            )
            transaction = apply_transaction(
                root,
                transaction_id=transaction_id,
                operation="agent-memory-cas-supersede",
                actor=actor,
                changes=[
                    FileChange(old_target, old_payload),
                    FileChange(claim_path(root, proposal["project"], claim_id), new_payload),
                    FileChange(root / relative, _json_bytes(event)),
                ],
            )
            return {
                "action": (
                    "renewed" if previous["object"] == new_claim["object"] else "superseded"
                ),
                "proposal_id": proposal["proposal_id"],
                "claim_id": claim_id,
                "previous_claim_id": previous_id,
                "authority": AGENT_MEMORY_AUTHORITY,
                "valid_to": new_claim["valid_to"],
                "candidate_activated": True,
                "conflict_detected": False,
                "source_verification_status": "verified_at_transition",
                "verified_source_refs": [
                    item["source_ref"] for item in verified_sources
                ],
                "activation_replay": False,
                **transaction,
            }

        existing = _overlapping_current_claims(claims, proposal, at=moment)
        same = next((item for item in existing if item["object"] == proposal["object"]), None)
        if same is not None or existing:
            selected = same or existing[0]
            action = "reject" if same is not None else "defer"
            reason_code = "duplicate" if same is not None else "contradicted"
            transaction_id = new_id("transaction")
            event, relative = _event_record(
                action=f"proposal_{'rejected' if action == 'reject' else 'deferred'}",
                actor=actor,
                transaction_id=transaction_id,
                project=proposal["project"],
                proposal_id=proposal["proposal_id"],
                target_id=selected["claim_id"],
                reason_code=reason_code,
            )
            transaction = apply_transaction(
                root,
                transaction_id=transaction_id,
                operation=f"agent-memory-{action}",
                actor=actor,
                changes=[FileChange(root / relative, _json_bytes(event))],
            )
            return {
                "action": "duplicate" if same is not None else "conflict_deferred",
                "proposal_id": proposal["proposal_id"],
                "candidate_activated": False,
                "conflict_detected": same is None,
                "existing_claim_id": selected["claim_id"],
                "existing_authority": selected["authority"],
                "activation_replay": False,
                **transaction,
            }

        active_agent_claims = [
            item
            for item in claims.values()
            if item["status"] == "accepted"
            and item["authority"] == AGENT_MEMORY_AUTHORITY
            and item["valid_from"] <= moment
            and (item.get("valid_to") is None or item["valid_to"] > moment)
        ]
        project_count = sum(
            1 for item in active_agent_claims if item["project"] == proposal["project"]
        )
        quota_message = None
        if project_count + 1 > limits.max_active_per_project:
            quota_message = (
                "active agent-memory project quota exceeded "
                f"({project_count + 1} > {limits.max_active_per_project})."
            )
        elif len(active_agent_claims) + 1 > limits.max_active_root:
            quota_message = (
                "active agent-memory root quota exceeded "
                f"({len(active_agent_claims) + 1} > {limits.max_active_root})."
            )
        if quota_message is not None:
            transaction_id = new_id("transaction")
            event, relative = _event_record(
                action="proposal_deferred",
                actor=actor,
                transaction_id=transaction_id,
                project=proposal["project"],
                proposal_id=proposal["proposal_id"],
                reason_code="quota_exceeded",
            )
            transaction = apply_transaction(
                root,
                transaction_id=transaction_id,
                operation="agent-memory-quota-defer",
                actor=actor,
                changes=[FileChange(root / relative, _json_bytes(event))],
            )
            return {
                "action": "quota_deferred",
                "proposal_id": proposal["proposal_id"],
                "candidate_activated": False,
                "conflict_detected": False,
                "quota_exceeded": True,
                "quota_message": quota_message,
                "activation_replay": False,
                **transaction,
            }

        claim_id = new_id("claim")
        now = utc_now()
        claim = {
            "claim_id": claim_id,
            "project": proposal["project"],
            "subject": proposal["subject"],
            "predicate": proposal["predicate"],
            "object": proposal["object"],
            "status": "accepted",
            "authority": AGENT_MEMORY_AUTHORITY,
            "valid_from": _parse_time(proposal["valid_from"], "valid_from"),
            "valid_to": proposal.get("valid_to"),
            "recorded_at": now,
            "transitioned_at": now,
            "proposal_id": proposal["proposal_id"],
            "supersedes": None,
            "superseded_by": None,
            "evidence_ids": proposal.get("evidence_ids", []),
            "source_refs": proposal.get("source_refs", []),
            "sensitivity": proposal["sensitivity"],
            "actor": actor,
            "rationale": proposal.get("rationale")
            or "Autonomously curated under an operator-enabled agent-memory policy.",
        }
        claim_payload = render_claim(claim)
        transaction_id = new_id("transaction")
        event, relative = _event_record(
            action="proposal_accepted",
            actor=actor,
            transaction_id=transaction_id,
            project=proposal["project"],
            proposal_id=proposal["proposal_id"],
            claim_id=claim_id,
            authority=AGENT_MEMORY_AUTHORITY,
            current_sha256=_hash_bytes(claim_payload),
        )
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation="agent-memory-activate",
            actor=actor,
            changes=[
                FileChange(claim_path(root, proposal["project"], claim_id), claim_payload),
                FileChange(root / relative, _json_bytes(event)),
            ],
        )
    return {
        "action": "remembered",
        "proposal_id": proposal["proposal_id"],
        "claim_id": claim_id,
        "authority": AGENT_MEMORY_AUTHORITY,
        "valid_to": claim.get("valid_to"),
        "candidate_activated": True,
        "conflict_detected": False,
        "activation_replay": False,
        **transaction,
    }


def remember_memory_bundle(
    root: Path,
    *,
    request: dict,
    request_bytes: int,
    allowed_projects: set[str],
    proposal_limits: ProposalLimits,
    memory_limits: AgentMemoryLimits,
    lifecycle_policy: AgentMemoryLifecyclePolicy = AgentMemoryLifecyclePolicy(),
    source_roots: dict[str, Path] | None = None,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    """Persist and activate one bounded agent-curated memory with replay safety."""
    _, payload, descriptors = normalize_agent_memory_submission(
        request,
        lifecycle_policy,
    )
    if payload["sensitivity"] != "normal" or any(
        item["sensitivity"] != "normal" for item in descriptors
    ):
        raise GovernanceError(
            "autonomous memory accepts normal-sensitivity records only."
        )
    configured_source_roots = source_roots or {}
    if payload.get("supersedes_claim_id"):
        verify_cas_source_refs(payload, None, configured_source_roots)
    submitted = submit_proposal_bundle(
        root,
        request=request,
        request_bytes=request_bytes,
        allowed_projects=allowed_projects,
        limits=proposal_limits,
        agent_lifecycle=lifecycle_policy,
        lock_timeout=lock_timeout,
        recover_stale=recover_stale,
    )
    activated = activate_agent_memory(
        root,
        proposal_id=submitted["proposal_id"],
        limits=memory_limits,
        source_roots=configured_source_roots,
        lock_timeout=lock_timeout,
        recover_stale=recover_stale,
    )
    result = {
        **activated,
        "submission_id": submitted["submission_id"],
        "idempotent_replay": bool(
            submitted.get("idempotent_replay") or activated.get("activation_replay")
        ),
        "canonical_committed": True,
    }
    if activated.get("candidate_activated"):
        result.update({
            "content_role": "untrusted_agent_memory",
            "authority_warning": (
                "Agent-curated memory is active but unverified. It never outranks "
                "user-ratified or repository-verified memory."
            ),
        })
    elif activated.get("action") == "inactive_terminal":
        result.update({
            "content_role": "untrusted_memory_history",
            "authority_warning": (
                "The terminal claim is not current active memory; it is available only as history."
            ),
        })
    elif activated.get("existing_authority") == AGENT_MEMORY_AUTHORITY:
        result.update({
            "content_role": "untrusted_agent_memory_reference",
            "authority_warning": (
                "No new claim was activated; the existing agent-curated claim remains "
                "active but unverified."
            ),
        })
    elif activated.get("existing_authority"):
        result.update({
            "content_role": "governed_memory_reference",
            "authority_warning": (
                "No autonomous claim was activated; the existing governed claim was retained."
            ),
        })
    else:
        result.update({
            "content_role": "untrusted_memory_candidate",
            "authority_warning": (
                "The candidate is not active memory; inspect canonical history for its terminal outcome."
            ),
        })
    return result


def reject_or_defer_proposal(
    root: Path,
    *,
    proposal_id: str,
    actor: str,
    reason_code: str,
    action: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    if action not in {"reject", "defer"}:
        raise GovernanceError("Unsupported proposal transition.")
    with WriterLock(root, f"proposal-{action}", timeout=lock_timeout, recover_stale=recover_stale):
        _refuse_pending_transactions(root)
        proposals, _, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        proposal = _find_proposal(proposals, proposal_id)
        _assert_pending(proposal["proposal_id"], statuses)
        transaction_id = new_id("transaction")
        event, relative = _event_record(
            action=f"proposal_{'rejected' if action == 'reject' else 'deferred'}",
            actor=actor,
            transaction_id=transaction_id,
            project=proposal["project"],
            proposal_id=proposal["proposal_id"],
            reason_code=_assert_reason(reason_code),
        )
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation=f"proposal-{action}",
            actor=actor,
            changes=[FileChange(root / relative, _json_bytes(event))],
        )
    return {
        "action": "rejected" if action == "reject" else "deferred",
        "proposal_id": proposal["proposal_id"],
        **transaction,
    }


def dispute_claim(
    root: Path,
    *,
    claim_id: str,
    actor: str,
    reason_code: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    with WriterLock(root, "claim-dispute", timeout=lock_timeout, recover_stale=recover_stale):
        _refuse_pending_transactions(root)
        claims = _load_claims(root)
        claim = _find_claim(claims, claim_id)
        if claim["status"] != "accepted":
            raise GovernanceError(f"Only an accepted claim can be disputed: {claim_id}")
        target = root / claim["path"]
        previous_hash = _hash_bytes(target.read_bytes())
        updated = dict(claim)
        updated["status"] = "disputed"
        updated["transitioned_at"] = utc_now()
        updated["actor"] = _line(actor, "actor", maximum=200)
        payload = render_claim(updated)
        transaction_id = new_id("transaction")
        event, relative = _event_record(
            action="claim_disputed",
            actor=actor,
            transaction_id=transaction_id,
            project=claim["project"],
            proposal_id=claim["proposal_id"],
            claim_id=claim["claim_id"],
            reason_code=_assert_reason(reason_code),
            previous_sha256=previous_hash,
            current_sha256=_hash_bytes(payload),
        )
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation="claim-dispute",
            actor=actor,
            changes=[FileChange(target, payload), FileChange(root / relative, _json_bytes(event))],
        )
    return {"action": "disputed", "claim_id": claim["claim_id"], **transaction}


def supersede_claim(
    root: Path,
    *,
    claim_id: str,
    proposal_id: str,
    actor: str,
    authority: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    if authority not in ACCEPTED_AUTHORITIES:
        raise GovernanceError(f"authority must be one of: {', '.join(sorted(ACCEPTED_AUTHORITIES))}.")
    with WriterLock(root, "claim-supersede", timeout=lock_timeout, recover_stale=recover_stale):
        _refuse_pending_transactions(root)
        proposals, evidence, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        proposal = _find_proposal(proposals, proposal_id)
        _assert_pending(proposal["proposal_id"], statuses)
        _assert_evidence(root, proposal, evidence)
        claims = _load_claims(root)
        previous = _find_claim(claims, claim_id)
        if previous["status"] != "accepted":
            raise GovernanceError(f"Only an accepted claim can be superseded: {claim_id}")
        if previous["project"] != proposal["project"]:
            raise GovernanceError("A proposal cannot supersede a claim in another project.")
        if (previous["subject"], previous["predicate"]) != (proposal["subject"], proposal["predicate"]):
            raise GovernanceError("Supersession requires the same subject and predicate.")
        valid_from = _parse_time(proposal["valid_from"], "valid_from")
        if valid_from < previous["valid_from"]:
            raise GovernanceError("A superseding claim cannot begin before the previous claim.")
        now = utc_now()
        new_claim_id = new_id("claim")
        new_claim = {
            "claim_id": new_claim_id,
            "project": proposal["project"],
            "subject": proposal["subject"],
            "predicate": proposal["predicate"],
            "object": proposal["object"],
            "status": "accepted",
            "authority": authority,
            "valid_from": valid_from,
            "valid_to": proposal.get("valid_to"),
            "recorded_at": now,
            "transitioned_at": now,
            "proposal_id": proposal["proposal_id"],
            "supersedes": previous["claim_id"],
            "superseded_by": None,
            "evidence_ids": proposal.get("evidence_ids", []),
            "source_refs": proposal.get("source_refs", []),
            "sensitivity": proposal["sensitivity"],
            "actor": _line(actor, "actor", maximum=200),
            "rationale": proposal.get("rationale") or "Accepted as an explicit superseding claim.",
        }
        old_updated = dict(previous)
        old_updated.update({
            "status": "superseded",
            "valid_to": valid_from,
            "transitioned_at": now,
            "superseded_by": new_claim_id,
            "actor": _line(actor, "actor", maximum=200),
        })
        old_target = root / previous["path"]
        old_before_hash = _hash_bytes(old_target.read_bytes())
        old_payload = render_claim(old_updated)
        new_payload = render_claim(new_claim)
        transaction_id = new_id("transaction")
        event, relative = _event_record(
            action="claim_superseded",
            actor=actor,
            transaction_id=transaction_id,
            project=proposal["project"],
            proposal_id=proposal["proposal_id"],
            claim_id=new_claim_id,
            previous_claim_id=previous["claim_id"],
            authority=authority,
            previous_sha256=old_before_hash,
            current_sha256=_hash_bytes(new_payload),
        )
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation="claim-supersede",
            actor=actor,
            changes=[
                FileChange(old_target, old_payload),
                FileChange(claim_path(root, proposal["project"], new_claim_id), new_payload),
                FileChange(root / relative, _json_bytes(event)),
            ],
        )
    return {
        "action": "superseded",
        "previous_claim_id": previous["claim_id"],
        "claim_id": new_claim_id,
        "proposal_id": proposal["proposal_id"],
        **transaction,
    }


def delete_item(
    root: Path,
    *,
    item_id: str,
    actor: str,
    reason_code: str,
    lock_timeout: float,
    recover_stale: bool,
) -> dict:
    item_id = str(item_id).strip().lower()
    if item_id.startswith(ID_PREFIXES["claim"]):
        kind = "claim"
    elif item_id.startswith(ID_PREFIXES["proposal"]):
        kind = "proposal"
    elif item_id.startswith(ID_PREFIXES["evidence"]):
        kind = "evidence"
    else:
        raise GovernanceError("delete supports claim_, proposal_, or evidence_ IDs.")
    item_id = validate_id(item_id, kind)
    with WriterLock(root, f"{kind}-delete", timeout=lock_timeout, recover_stale=recover_stale):
        _refuse_pending_transactions(root)
        proposals, evidence, events, tombstones = load_governance(root)
        statuses = proposal_statuses(proposals, events, tombstones)
        claims = _load_claims(root)
        if kind == "claim":
            record = _find_claim(claims, item_id)
        elif kind == "proposal":
            record = _find_proposal(proposals, item_id)
        else:
            try:
                record = evidence[item_id]
            except KeyError as exc:
                raise GovernanceError(f"Evidence not found: {item_id}") from exc
            pending_references = sorted(
                proposal_id
                for proposal_id, proposal in proposals.items()
                if item_id in proposal.get("evidence_ids", [])
                and statuses.get(proposal_id) == "pending"
            )
            if pending_references:
                raise GovernanceError(
                    "Evidence referenced by a pending proposal cannot be deleted: "
                    + ", ".join(pending_references)
                )
        target = root / record["path"]
        prior_hash = _hash_bytes(target.read_bytes())
        transaction_id = new_id("transaction")
        now = utc_now()
        tombstone_id = new_id("tombstone")
        tombstone = {
            "format_version": CANONICAL_FORMAT_VERSION,
            "record_type": "tombstone",
            "tombstone_id": tombstone_id,
            "item_id": item_id,
            "item_type": kind,
            "project": record.get("project"),
            "deleted_at": now,
            "actor": _line(actor, "actor", maximum=200),
            "reason_code": _assert_reason(reason_code),
            "prior_sha256": prior_hash,
        }
        if kind == "proposal" and record.get("format_version") in {
            PROPOSAL_V2_FORMAT_VERSION,
            PROPOSAL_V3_FORMAT_VERSION,
            PROPOSAL_FORMAT_VERSION,
        }:
            tombstone["submission_replay_key"] = submission_replay_key(
                record["project"],
                record["submission_id"],
            )
        event, relative = _event_record(
            action="item_deleted",
            actor=actor,
            transaction_id=transaction_id,
            project=record["project"],
            proposal_id=record.get("proposal_id") if kind == "claim" else (item_id if kind == "proposal" else None),
            claim_id=item_id if kind == "claim" else None,
            target_id=item_id,
            reason_code=reason_code,
            previous_sha256=prior_hash,
        )
        transaction = apply_transaction(
            root,
            transaction_id=transaction_id,
            operation=f"{kind}-delete",
            actor=actor,
            changes=[
                FileChange(target, None),
                FileChange(tombstone_path(root, item_id), _json_bytes(tombstone)),
                FileChange(root / relative, _json_bytes(event)),
            ],
        )
    return {
        "action": "deleted",
        "item_id": item_id,
        "item_type": kind,
        "tombstone_id": tombstone_id,
        **transaction,
    }


def _overlap(left: dict, right: dict) -> bool:
    left_end = left.get("valid_to") or "9999-12-31T23:59:59.999999+00:00"
    right_end = right.get("valid_to") or "9999-12-31T23:59:59.999999+00:00"
    return left["valid_from"] < right_end and right["valid_from"] < left_end


def contradictions(claims: list[dict]) -> list[dict]:
    active = [item for item in claims if item.get("status") == "accepted"]
    found: list[dict] = []
    for index, left in enumerate(active):
        for right in active[index + 1:]:
            if left.get("project") != right.get("project"):
                continue
            if (left.get("subject"), left.get("predicate")) != (right.get("subject"), right.get("predicate")):
                continue
            if left.get("object") == right.get("object") or not _overlap(left, right):
                continue
            found.append({
                "project": left["project"],
                "subject": left["subject"],
                "predicate": left["predicate"],
                "left_claim_id": left["claim_id"],
                "left_object": left["object"],
                "right_claim_id": right["claim_id"],
                "right_object": right["object"],
            })
    return found


def _claim_lineages(
    claims: list[dict],
    tombstones: dict[str, dict],
) -> list[list[dict]]:
    """Return deterministic linear claim components without changing canon.

    Deleted neighbors are allowed only when their metadata-only tombstones are
    still present. Any other broken, branching, cross-project, or cyclic link
    makes a compact view unsafe and therefore fails closed.
    """

    by_id = {item["claim_id"]: item for item in claims}
    successors: dict[str, str] = {}
    predecessors: dict[str, str] = {}
    for claim_id, claim in by_id.items():
        previous_id = claim.get("supersedes")
        next_id = claim.get("superseded_by")
        if previous_id:
            previous = by_id.get(previous_id)
            if previous is None:
                tombstone = tombstones.get(previous_id)
                if tombstone is None:
                    raise GovernanceError(
                        f"Claim lineage references a missing predecessor: {claim_id} -> {previous_id}"
                    )
                if tombstone.get("item_type") != "claim":
                    raise GovernanceError(
                        f"Claim lineage predecessor is not a claim tombstone: {previous_id}"
                    )
                if tombstone.get("project") != claim["project"]:
                    raise GovernanceError("Claim lineage cannot cross project boundaries.")
            else:
                if previous.get("superseded_by") != claim_id:
                    raise GovernanceError(
                        f"Claim lineage has asymmetric predecessor linkage: {previous_id} -> {claim_id}"
                    )
                if previous["project"] != claim["project"]:
                    raise GovernanceError("Claim lineage cannot cross project boundaries.")
                predecessors[claim_id] = previous_id
        if next_id:
            successor = by_id.get(next_id)
            if successor is None:
                tombstone = tombstones.get(next_id)
                if tombstone is None:
                    raise GovernanceError(
                        f"Claim lineage references a missing successor: {claim_id} -> {next_id}"
                    )
                if tombstone.get("item_type") != "claim":
                    raise GovernanceError(
                        f"Claim lineage successor is not a claim tombstone: {next_id}"
                    )
                if tombstone.get("project") != claim["project"]:
                    raise GovernanceError("Claim lineage cannot cross project boundaries.")
            else:
                if successor.get("supersedes") != claim_id:
                    raise GovernanceError(
                        f"Claim lineage has asymmetric successor linkage: {claim_id} -> {next_id}"
                    )
                if successor["project"] != claim["project"]:
                    raise GovernanceError("Claim lineage cannot cross project boundaries.")
                if claim_id in successors and successors[claim_id] != next_id:
                    raise GovernanceError(f"Claim lineage branches at {claim_id}.")
                successors[claim_id] = next_id

    roots = sorted(claim_id for claim_id in by_id if claim_id not in predecessors)
    visited: set[str] = set()
    lineages: list[list[dict]] = []
    for root_id in roots:
        component: list[dict] = []
        current_id: str | None = root_id
        local: set[str] = set()
        while current_id is not None:
            if current_id in local:
                raise GovernanceError(f"Claim lineage cycle detected at {current_id}.")
            if current_id in visited:
                raise GovernanceError(f"Claim lineage converges more than once at {current_id}.")
            local.add(current_id)
            visited.add(current_id)
            component.append(by_id[current_id])
            current_id = successors.get(current_id)
        lineages.append(component)
    if len(visited) != len(by_id):
        unresolved = sorted(set(by_id) - visited)[0]
        raise GovernanceError(f"Claim lineage cycle detected at {unresolved}.")
    return lineages


def _claim_effective_status(claim: dict, moment: str) -> str:
    if claim["status"] != "accepted":
        return claim["status"]
    if claim["valid_from"] > moment:
        return "future"
    if claim.get("valid_to") and claim["valid_to"] <= moment:
        return "expired"
    return "active"


def _lineage_digest(lineage: list[dict]) -> str:
    identity = [
        {
            "claim_id": item["claim_id"],
            "content_sha256": item["content_sha256"],
            "proposal_id": item["proposal_id"],
            "status": item["status"],
            "supersedes": item.get("supersedes"),
            "superseded_by": item.get("superseded_by"),
        }
        for item in lineage
    ]
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _lineage_summary(lineage: list[dict], *, moment: str) -> dict:
    first = lineage[0]
    head = lineage[-1]
    renewals = sum(
        1 for previous, current in zip(lineage, lineage[1:])
        if (
            previous["subject"] == current["subject"]
            and previous["predicate"] == current["predicate"]
            and previous["object"] == current["object"]
        )
    )
    replacements = max(0, len(lineage) - 1 - renewals)
    semantic_keys = {
        (item["subject"], item["predicate"])
        for item in lineage
    }
    authorities = {item["authority"] for item in lineage}
    return {
        "lineage_id": first["claim_id"],
        "head_claim_id": head["claim_id"],
        "project": head["project"],
        "subject": head["subject"],
        "predicate": head["predicate"],
        "head_object": head["object"],
        "head_status": _claim_effective_status(head, moment),
        "head_authority": head["authority"],
        "claim_count": len(lineage),
        "renewal_count": renewals,
        "replacement_count": replacements,
        "authority_change_count": sum(
            1 for previous, current in zip(lineage, lineage[1:])
            if previous["authority"] != current["authority"]
        ),
        "semantic_key_changed": len(semantic_keys) > 1,
        "first_valid_from": first["valid_from"],
        "head_valid_from": head["valid_from"],
        "head_valid_to": head.get("valid_to"),
        "first_recorded_at": first["recorded_at"],
        "last_transitioned_at": max(item["transitioned_at"] for item in lineage),
        "source_ref_count": sum(len(item.get("source_refs", [])) for item in lineage),
        "evidence_ref_count": sum(len(item.get("evidence_ids", [])) for item in lineage),
        "canonical_lineage_sha256": _lineage_digest(lineage),
        "expand": {"history_lineage": first["claim_id"]},
        "authority_set": sorted(authorities),
        "predecessor_tombstone_id": (
            first.get("supersedes") if first.get("supersedes") else None
        ),
        "successor_tombstone_id": (
            head.get("superseded_by") if head.get("superseded_by") else None
        ),
    }


def _count_values(items: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(field) or "unspecified")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _json_token_estimate(value: dict) -> int:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return estimate_tokens(rendered)


def _with_token_estimate(value: dict) -> dict:
    result = dict(value)
    estimate = 0
    for _ in range(4):
        result["estimated_tokens"] = estimate
        updated = _json_token_estimate(result)
        if updated == estimate:
            break
        estimate = updated
    result["estimated_tokens"] = _json_token_estimate(result)
    return result


def logical_compaction_view(
    root: Path,
    *,
    project: str | None,
    subject: str | None,
    predicate: str | None,
    budget_tokens: int = DEFAULT_COMPACTION_BUDGET,
) -> dict:
    """Build a bounded, deterministic, derived lineage snapshot.

    This view never writes canonical or derived state. Exact claims, proposals,
    events, evidence, and tombstones remain available through ``history`` and
    ``history --lineage``.
    """

    if (
        not isinstance(budget_tokens, int)
        or isinstance(budget_tokens, bool)
        or not MIN_COMPACTION_BUDGET <= budget_tokens <= MAX_COMPACTION_BUDGET
    ):
        raise GovernanceError(
            "compaction budget must be an integer between "
            f"{MIN_COMPACTION_BUDGET} and {MAX_COMPACTION_BUDGET}."
        )
    normalized_project = _project(project) if project else None
    proposals, evidence, events, tombstones = load_governance(root)
    claims = list(_load_claims(root).values())
    all_lineages = _claim_lineages(claims, tombstones)
    selected: list[list[dict]] = []
    for lineage in all_lineages:
        if normalized_project and not any(
            item["project"] == normalized_project for item in lineage
        ):
            continue
        if subject and not any(item["subject"] == subject for item in lineage):
            continue
        if predicate and not any(item["predicate"] == predicate for item in lineage):
            continue
        selected.append(lineage)

    moment = utc_now()
    summaries = [_lineage_summary(lineage, moment=moment) for lineage in selected]
    status_rank = {"active": 0, "future": 1, "expired": 2, "disputed": 3, "superseded": 4}
    summaries.sort(key=lambda item: (
        status_rank.get(item["head_status"], 99),
        item["project"],
        item["subject"],
        item["predicate"],
        item["lineage_id"],
    ))
    claim_ids = {item["claim_id"] for lineage in selected for item in lineage}
    proposal_ids = {item["proposal_id"] for lineage in selected for item in lineage}
    tombstone_anchor_ids = {
        anchor_id
        for lineage in selected
        for anchor_id in (lineage[0].get("supersedes"), lineage[-1].get("superseded_by"))
        if anchor_id
    }
    statuses = proposal_statuses(proposals, events, tombstones)
    proposal_rows = [
        {**record, "status": statuses[proposal_id]}
        for proposal_id, record in proposals.items()
        if proposal_id in proposal_ids
    ]
    event_rows = [
        record for record in events.values()
        if record.get("proposal_id") in proposal_ids
        or record.get("claim_id") in claim_ids
        or record.get("claim_id") in tombstone_anchor_ids
        or record.get("previous_claim_id") in claim_ids
        or record.get("previous_claim_id") in tombstone_anchor_ids
        or record.get("target_id") in claim_ids
        or record.get("target_id") in tombstone_anchor_ids
    ]
    evidence_ids = {
        evidence_id
        for lineage in selected
        for item in lineage
        for evidence_id in item.get("evidence_ids", [])
    }
    related_tombstones = [
        item for item_id, item in tombstones.items()
        if item_id in claim_ids
        or item_id in proposal_ids
        or item_id in evidence_ids
        or item_id in tombstone_anchor_ids
    ]
    aggregate = {
        "claim_count": len(claim_ids),
        "proposal_count": len(proposal_rows),
        "proposal_status_counts": _count_values(proposal_rows, "status"),
        "event_count": len(event_rows),
        "event_action_counts": _count_values(event_rows, "action"),
        "evidence_count": sum(1 for evidence_id in evidence_ids if evidence_id in evidence),
        "tombstone_count": len(related_tombstones),
        "contradiction_count": len(contradictions([
            item for lineage in selected for item in lineage
        ])),
    }
    base = {
        "schema_version": LOGICAL_COMPACTION_SCHEMA_VERSION,
        "view": "logical_compaction",
        "content_role": "derived_untrusted_memory_manifest",
        "authority_warning": (
            "This is a deterministic derived snapshot, not a canonical rewrite or a semantic summary."
        ),
        "project": normalized_project,
        "subject": subject,
        "predicate": predicate,
        "budget_tokens": budget_tokens,
        "lineage_count": len(summaries),
        "lineage_count_returned": 0,
        "omitted_lineage_count": len(summaries),
        "truncated": bool(summaries),
        "aggregate": aggregate,
        "lineages": [],
    }
    measured = _with_token_estimate(base)
    if measured["estimated_tokens"] > budget_tokens:
        raise GovernanceError(
            "compaction budget is too small for the requested aggregate; narrow the history scope."
        )
    returned: list[dict] = []
    for summary in summaries:
        candidate = {
            **base,
            "lineage_count_returned": len(returned) + 1,
            "omitted_lineage_count": len(summaries) - len(returned) - 1,
            "truncated": len(returned) + 1 < len(summaries),
            "lineages": [*returned, summary],
        }
        measured = _with_token_estimate(candidate)
        if measured["estimated_tokens"] > budget_tokens:
            break
        returned.append(summary)
    result = {
        **base,
        "lineage_count_returned": len(returned),
        "omitted_lineage_count": len(summaries) - len(returned),
        "truncated": len(returned) < len(summaries),
        "lineages": returned,
    }
    result = _with_token_estimate(result)
    if result["estimated_tokens"] > budget_tokens:
        raise GovernanceError("logical compaction exceeded its deterministic token budget.")
    return result


def lineage_history_view(
    root: Path,
    *,
    lineage_claim_id: str,
    project: str | None,
    include_deleted: bool,
) -> dict:
    """Expand exactly one canonical lineage selected by any member claim ID."""

    requested_id = validate_id(lineage_claim_id, "claim")
    proposals, evidence, events, tombstones = load_governance(root)
    claims = list(_load_claims(root).values())
    lineages = _claim_lineages(claims, tombstones)
    selected = next(
        (lineage for lineage in lineages if any(item["claim_id"] == requested_id for item in lineage)),
        None,
    )
    if selected is None:
        raise GovernanceError(f"Claim lineage not found: {requested_id}")
    lineage_project = selected[0]["project"]
    if project and _project(project) != lineage_project:
        raise GovernanceError("Claim lineage is outside the requested project scope.")
    claim_ids = {item["claim_id"] for item in selected}
    proposal_ids = {item["proposal_id"] for item in selected}
    tombstone_anchor_ids = {
        anchor_id
        for anchor_id in (selected[0].get("supersedes"), selected[-1].get("superseded_by"))
        if anchor_id
    }
    statuses = proposal_statuses(proposals, events, tombstones)
    proposal_rows = [
        {**record, "status": statuses[proposal_id]}
        for proposal_id, record in proposals.items()
        if proposal_id in proposal_ids
    ]
    event_rows = [
        record for record in events.values()
        if record.get("proposal_id") in proposal_ids
        or record.get("claim_id") in claim_ids
        or record.get("claim_id") in tombstone_anchor_ids
        or record.get("previous_claim_id") in claim_ids
        or record.get("previous_claim_id") in tombstone_anchor_ids
        or record.get("target_id") in claim_ids
        or record.get("target_id") in tombstone_anchor_ids
    ]
    evidence_ids = {
        evidence_id
        for item in selected
        for evidence_id in item.get("evidence_ids", [])
    }
    related_ids = claim_ids | proposal_ids | evidence_ids | {
        item["event_id"] for item in event_rows
    }
    related_ids.update(tombstone_anchor_ids)
    return {
        "project": lineage_project,
        "lineage_id": selected[0]["claim_id"],
        "requested_claim_id": requested_id,
        "claims": sorted(selected, key=lambda item: (item["recorded_at"], item["claim_id"])),
        "proposals": sorted(
            proposal_rows,
            key=lambda item: (item.get("proposed_at", ""), item["proposal_id"]),
        ),
        "events": sorted(
            event_rows,
            key=lambda item: (item.get("occurred_at", ""), item["event_id"]),
        ),
        "contradictions": contradictions(selected),
        "evidence_count": sum(1 for evidence_id in evidence_ids if evidence_id in evidence),
        "tombstones": sorted(
            (item for item_id, item in tombstones.items() if item_id in related_ids),
            key=lambda item: item.get("deleted_at", ""),
        ) if include_deleted else [],
        "logical_compaction": _lineage_summary(selected, moment=utc_now()),
    }


def history_view(
    root: Path,
    *,
    project: str | None,
    subject: str | None,
    predicate: str | None,
    valid_at: str | None,
    recorded_at: str | None,
    include_deleted: bool,
) -> dict:
    proposals, evidence, events, tombstones = load_governance(root)
    claims = list(_load_claims(root).values())
    recorded_moment = _parse_time(recorded_at, "recorded_at") if recorded_at else None
    visible_events = {
        event_id: event for event_id, event in events.items()
        if recorded_moment is None or event.get("occurred_at", "") <= recorded_moment
    }
    statuses = proposal_statuses(proposals, visible_events, tombstones)
    if recorded_moment is not None:
        acceptance_actor = {
            event.get("claim_id"): event.get("actor")
            for event in visible_events.values()
            if event.get("action") in {"proposal_accepted", "claim_superseded"}
        }
        reconstructed: list[dict] = []
        for original in claims:
            if original["recorded_at"] > recorded_moment:
                continue
            item = dict(original)
            if (
                item["status"] in {"disputed", "superseded"}
                and recorded_moment < item["transitioned_at"]
            ):
                item["status"] = "accepted"
                item["transitioned_at"] = item["recorded_at"]
                item["actor"] = acceptance_actor.get(item["claim_id"], item["actor"])
                if original["status"] == "superseded":
                    item["valid_to"] = None
                    item["superseded_by"] = None
                item["recorded_time_reconstructed"] = True
            reconstructed.append(item)
        claims = reconstructed
    if project:
        project = _project(project)
        claims = [item for item in claims if item["project"] == project]
    if subject:
        claims = [item for item in claims if item["subject"] == subject]
    if predicate:
        claims = [item for item in claims if item["predicate"] == predicate]
    if valid_at:
        moment = _parse_time(valid_at, "valid_at")
        claims = [
            item for item in claims
            if item["valid_from"] <= moment and (item.get("valid_to") is None or moment < item["valid_to"])
        ]
    proposal_rows = [
        {**record, "status": statuses[proposal_id]}
        for proposal_id, record in proposals.items()
        if (project is None or record.get("project") == project)
        and (recorded_moment is None or record.get("proposed_at", "") <= recorded_moment)
    ]
    event_rows = [
        record for record in visible_events.values()
        if project is None or record.get("project") == project
    ]
    return {
        "project": project,
        "valid_at": valid_at,
        "recorded_at": recorded_at,
        "claims": sorted(claims, key=lambda item: (item["recorded_at"], item["claim_id"])),
        "proposals": sorted(proposal_rows, key=lambda item: (item.get("proposed_at", ""), item["proposal_id"])),
        "events": sorted(event_rows, key=lambda item: (item.get("occurred_at", ""), item["event_id"])),
        "contradictions": contradictions(claims),
        "evidence_count": sum(
            1 for item in evidence.values()
            if (project is None or item.get("project") == project)
            and (recorded_moment is None or item.get("captured_at", "") <= recorded_moment)
        ),
        "tombstones": sorted(
            (
                item for item in tombstones.values()
                if (project is None or item.get("project") == project)
                and (recorded_moment is None or item.get("deleted_at", "") <= recorded_moment)
            ),
            key=lambda item: item.get("deleted_at", ""),
        ) if include_deleted else [],
    }


def governance_projection_digest(root: Path) -> str:
    """Hash canonical governed records so read-only status can detect stale projection."""
    entries: list[bytes] = []
    patterns = (
        ("01_inbox/elm_proposals", "proposal_*.json"),
        ("40_sources/elm_evidence/metadata", "evidence_*.json"),
        ("30_agent_logs/elm_events", "event_*.json"),
        ("30_agent_logs/elm_tombstones", "*.json"),
    )
    for relative, pattern in patterns:
        for path in _iter_json(root, relative, pattern):
            path_bytes = _relative(root, path).encode("utf-8")
            entries.append(path_bytes + b"\x00" + _hash_bytes(path.read_bytes()).encode("ascii"))
    projects = root / "20_projects"
    if projects.is_dir():
        for path in sorted(projects.glob("*/CLAIMS/claim_*.md")):
            path_bytes = _relative(root, path).encode("utf-8")
            entries.append(path_bytes + b"\x00" + _hash_bytes(path.read_bytes()).encode("ascii"))
    return hashlib.sha256(b"\n".join(sorted(entries))).hexdigest()


def sync_governance_projection(con: sqlite3.Connection, root: Path) -> dict:
    pending = _iter_json(root, "01_inbox/elm_transactions", "transaction_*.json")
    if pending:
        raise GovernanceError(
            "Incomplete canonical governance transaction exists; run a lifecycle mutation to recover it."
        )
    proposals, evidence, events, tombstones = load_governance(root)
    statuses = proposal_statuses(proposals, events, tombstones)
    claims = _load_claims(root)
    with con:
        for table in ("governance_events", "governance_tombstones", "governance_evidence", "governance_proposals", "claims"):
            con.execute(f"DELETE FROM {table}")
        for proposal_id, record in proposals.items():
            con.execute(
                """INSERT INTO governance_proposals(
                       proposal_id,path,format_version,project,subject,predicate,object,status,proposed_at,valid_from,valid_to,
                       actor,requested_authority,sensitivity,evidence_ids_json,source_refs_json,
                       submission_id,payload_digest,source_channel,supersedes_claim_id,
                       expected_claim_sha256,content_hash
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposal_id, record["path"], record["format_version"], record["project"], record["subject"], record["predicate"],
                    record["object"], statuses[proposal_id], record["proposed_at"], record["valid_from"], record.get("valid_to"),
                    record["actor"], record["requested_authority"], record["sensitivity"],
                    json.dumps(record.get("evidence_ids", []), ensure_ascii=False),
                    json.dumps(record.get("source_refs", []), ensure_ascii=False),
                    record.get("submission_id"), record.get("payload_digest"), record.get("source_channel"),
                    record.get("supersedes_claim_id"), record.get("expected_claim_sha256"),
                    _hash_bytes((root / record["path"]).read_bytes()),
                ),
            )
        for evidence_id, record in evidence.items():
            con.execute(
                """INSERT INTO governance_evidence(
                       evidence_id,path,project,kind,source_uri,captured_at,content_sha256,excerpt_sha256,
                       sensitivity,retention,actor,record_hash
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id, record["path"], record["project"], record["kind"], record["source_uri"],
                    record["captured_at"], record["content_sha256"], record.get("excerpt_sha256"),
                    record["sensitivity"], record["retention"], record["actor"],
                    _hash_bytes((root / record["path"]).read_bytes()),
                ),
            )
        for claim_id, record in claims.items():
            document = con.execute("SELECT id FROM documents WHERE path=?", (record["path"],)).fetchone()
            if document is None:
                raise GovernanceError(f"Claim Markdown is absent from the document projection: {record['path']}")
            con.execute(
                """INSERT INTO claims(
                       claim_id,document_id,path,project,subject,predicate,object,status,authority,valid_from,
                       valid_to,recorded_at,transitioned_at,proposal_id,supersedes,superseded_by,
                       evidence_ids_json,source_refs_json,sensitivity,actor,content_hash
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id, int(document["id"]), record["path"], record["project"], record["subject"],
                    record["predicate"], record["object"], record["status"], record["authority"],
                    record["valid_from"], record.get("valid_to"), record["recorded_at"], record["transitioned_at"],
                    record["proposal_id"], record.get("supersedes"), record.get("superseded_by"),
                    json.dumps(record.get("evidence_ids", []), ensure_ascii=False),
                    json.dumps(record.get("source_refs", []), ensure_ascii=False),
                    record["sensitivity"], record["actor"], record["content_sha256"],
                ),
            )
        for event_id, record in events.items():
            con.execute(
                """INSERT INTO governance_events(
                       event_id,path,action,occurred_at,actor,transaction_id,project,proposal_id,claim_id,
                       previous_claim_id,target_id,authority,reason_code,previous_sha256,current_sha256
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, record["path"], record["action"], record["occurred_at"], record["actor"],
                    record["transaction_id"], record["project"], record.get("proposal_id"), record.get("claim_id"),
                    record.get("previous_claim_id"), record.get("target_id"), record.get("authority"),
                    record.get("reason_code"), record.get("previous_sha256"), record.get("current_sha256"),
                ),
            )
        for item_id, record in tombstones.items():
            con.execute(
                """INSERT INTO governance_tombstones(
                       item_id,path,tombstone_id,item_type,project,deleted_at,actor,reason_code,prior_sha256
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    item_id, record["path"], record["tombstone_id"], record["item_type"], record.get("project"),
                    record["deleted_at"], record["actor"], record["reason_code"], record["prior_sha256"],
                ),
            )
        con.execute(
            "INSERT OR REPLACE INTO elm_meta(key,value) VALUES('governance_projection_sha256',?)",
            (governance_projection_digest(root),),
        )
    return {
        "proposals": len(proposals),
        "evidence": len(evidence),
        "claims": len(claims),
        "events": len(events),
        "tombstones": len(tombstones),
        "contradictions": len(contradictions(list(claims.values()))),
    }
