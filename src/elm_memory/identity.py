"""Stable public identity helpers for ELM's disposable index."""
from __future__ import annotations

from pathlib import PurePosixPath
import re
import unicodedata
import uuid


DOCUMENT_UID_PREFIX = "doc_"
SECTION_KEY_PREFIX = "section_"
_DOCUMENT_NAMESPACE = uuid.UUID("01ad132d-47e9-5c36-a3df-fd0961799e47")
_DOCUMENT_UID_RE = re.compile(
    r"^doc_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def normalize_relative_path(path: str) -> str:
    """Return the stable path spelling used by path-bound identities."""
    normalized = unicodedata.normalize("NFC", path.replace("\\", "/")).strip("/")
    return PurePosixPath(normalized).as_posix()


def validate_document_uid(value: str | None) -> str | None:
    """Validate and normalize an optional prefixed UUIDv4 document ID."""
    if value is None or not value.strip():
        return None
    candidate = value.strip().lower()
    if not _DOCUMENT_UID_RE.fullmatch(candidate):
        raise ValueError("ELM ID must use the form doc_<uuid4>.")
    parsed = uuid.UUID(candidate[len(DOCUMENT_UID_PREFIX):])
    if parsed.version != 4:
        raise ValueError("ELM ID must contain a UUIDv4 value.")
    return DOCUMENT_UID_PREFIX + str(parsed)


def new_document_uid() -> str:
    return DOCUMENT_UID_PREFIX + str(uuid.uuid4())


def normalize_heading_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return " ".join(normalized.split())


def document_namespace(document_uid: str | None, relative_path: str) -> tuple[uuid.UUID, str]:
    """Return the UUID namespace and whether it is durable or path-bound."""
    if document_uid:
        name = validate_document_uid(document_uid)
        assert name is not None
        return uuid.uuid5(_DOCUMENT_NAMESPACE, name), "document_uid"
    path_name = "path:" + normalize_relative_path(relative_path)
    return uuid.uuid5(_DOCUMENT_NAMESPACE, path_name), "path"


def derive_section_key(
    document_uid: str | None,
    relative_path: str,
    heading_path: str,
    occurrence: int,
) -> tuple[str, str]:
    """Derive a rebuild-stable key for one heading occurrence."""
    if occurrence < 0:
        raise ValueError("Section occurrence cannot be negative.")
    namespace, kind = document_namespace(document_uid, relative_path)
    name = f"{normalize_heading_path(heading_path)}\noccurrence:{occurrence}"
    return SECTION_KEY_PREFIX + str(uuid.uuid5(namespace, name)), kind


def derive_namespace(area: str | None, project: str | None) -> str:
    if project:
        return "project"
    if area == "10_shared":
        return "shared"
    return "workspace"
