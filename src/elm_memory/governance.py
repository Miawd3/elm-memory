"""Canonical governed-memory records and lifecycle operations for ELM Phase Three.

JSON and Markdown files under the ELM root are the durable source of truth.
SQLite only projects these records for retrieval.  Multi-file mutations use a
canonical recovery journal so deleting ``.elm`` never removes recovery state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from .atomic import atomic_create_bytes, atomic_write_bytes
from .locking import WriterLock


CANONICAL_FORMAT_VERSION = 1
ID_PREFIXES = {
    "proposal": "proposal_",
    "evidence": "evidence_",
    "claim": "claim_",
    "event": "event_",
    "tombstone": "tombstone_",
    "transaction": "transaction_",
}
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REF_RE = re.compile(r"^.+@sha256:([0-9a-fA-F]{64})$")
ACCEPTED_AUTHORITIES = {
    "ratified_project_decision",
    "user_ratified",
    "verified_repository_state",
}
PROPOSAL_AUTHORITIES = {
    "agent_proposal",
    "candidate_inference",
    "verified_repository_state",
}
SENSITIVITIES = {"normal", "restricted"}
EVIDENCE_KINDS = {"repository_file", "document_section", "external_uri"}
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
    "user_request",
    "other",
}


class GovernanceError(RuntimeError):
    """A safe, user-correctable governed-memory failure."""


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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"Invalid {record_type} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceError(f"{record_type} record must be a JSON object: {path}")
    if value.get("format_version") != CANONICAL_FORMAT_VERSION:
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


def _validate_proposal_record(record: dict, path: Path) -> dict:
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
    return record


def _validate_evidence_record(record: dict, path: Path) -> dict:
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
    if record.get("authority") and record["authority"] not in ACCEPTED_AUTHORITIES:
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
    if values["authority"] not in ACCEPTED_AUTHORITIES:
        raise GovernanceError(f"Invalid accepted authority at {path}: {values['authority']}")
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
            "valid_to": None,
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
            "valid_to": None,
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
        proposals, evidence, _, _ = load_governance(root)
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
                       proposal_id,path,project,subject,predicate,object,status,proposed_at,valid_from,
                       actor,requested_authority,sensitivity,evidence_ids_json,source_refs_json,content_hash
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposal_id, record["path"], record["project"], record["subject"], record["predicate"],
                    record["object"], statuses[proposal_id], record["proposed_at"], record["valid_from"],
                    record["actor"], record["requested_authority"], record["sensitivity"],
                    json.dumps(record.get("evidence_ids", []), ensure_ascii=False),
                    json.dumps(record.get("source_refs", []), ensure_ascii=False),
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
    return {
        "proposals": len(proposals),
        "evidence": len(evidence),
        "claims": len(claims),
        "events": len(events),
        "tombstones": len(tombstones),
        "contradictions": len(contradictions(list(claims.values()))),
    }
