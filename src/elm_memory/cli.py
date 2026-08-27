#!/usr/bin/env python3
"""Deterministic search and progressive-reading layer for External Local Memory.

The filesystem remains the source of truth. SQLite is a disposable, rebuildable index.
Uses only the Python standard library and SQLite FTS5.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Iterable

from .atomic import atomic_write_bytes
from .canonical import CanonicalJSONError, parse_closed_json
from .context import (
    DEFAULT_TRACE_RETENTION_DAYS,
    MIN_CONTEXT_BUDGET,
    build_context_packet,
    cleanup_retrieval_traces,
    write_retrieval_trace,
)
from .identity import (
    derive_namespace,
    derive_section_key,
    new_document_uid,
    normalize_heading_path,
    validate_document_uid,
)
from .governance import (
    ACCEPTED_AUTHORITIES,
    EVIDENCE_KINDS,
    PROPOSAL_AUTHORITIES,
    REASON_CODES,
    SENSITIVITIES,
    GovernanceError,
    ProposalLimits,
    accept_proposal,
    bootstrap_root_identity,
    create_evidence_reference,
    create_proposal,
    delete_item,
    dispute_claim,
    history_view,
    governance_projection_digest,
    load_root_identity,
    pending_transactions,
    recover_governance_transactions,
    reject_or_defer_proposal,
    supersede_claim,
    sync_governance_projection,
    submit_proposal_bundle,
    preview_proposal_transition,
    validate_id,
)
from .locking import WriterLock, WriterLockError
from .schema import (
    INDEX_SCHEMA_VERSION,
    SchemaMigrationError,
    UnsupportedSchemaError,
    ensure_schema,
    schema_version,
)
from .tokens import estimate_tokens

CONFIG_POINTER = Path.home() / ".elm-system" / "root"
META_KEYS = {
    "title": "title",
    "scope": "scope",
    "tags": "tags",
    "related files": "related_files",
    "last updated": "last_updated",
    "status": "status",
    "summary": "summary",
    "elm id": "elm_id",
    "record type": "record_type",
    "project": "governance_project",
    "subject": "claim_subject",
    "predicate": "claim_predicate",
    "object": "claim_object",
    "authority": "claim_authority",
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
EXPECTED_META = ("Title", "Scope", "Tags", "Last updated", "Status")
ARCHIVE_PARTS = {"backups", "99_archive"}
ARCHIVE_PATTERNS = (
    re.compile(r"\.bak(?:[._-].*)?$", re.I),
    re.compile(r"\.pre[-_.].*$", re.I),
)
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


class ReadOnlyIndexError(RuntimeError):
    """Raised when a read-only command cannot safely use the disposable index."""


def configure_standard_streams() -> None:
    """Keep machine-readable output UTF-8 even under legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            # Test doubles and already-detached streams may not be reconfigurable.
            continue


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def resolve_root(cli_root: str | None) -> Path:
    raw = cli_root or os.environ.get("ELM_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    if CONFIG_POINTER.is_file():
        configured = CONFIG_POINTER.read_text(encoding="utf-8-sig").strip()
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                candidate = CONFIG_POINTER.parent / candidate
            return candidate.resolve()
    cwd = Path.cwd()
    if (cwd / "00_registry").is_dir():
        return cwd.resolve()
    raise SystemExit(
        "ELM root not found. Pass --root PATH, set ELM_ROOT, run from an ELM root, "
        f"or write the root path to {CONFIG_POINTER}."
    )


def db_path(root: Path) -> Path:
    return root / ".elm" / "index.sqlite"


def connect(
    root: Path,
    *,
    schema_locked: bool = False,
    lock_timeout: float = 10.0,
    recover_stale: bool = False,
) -> sqlite3.Connection:
    p = db_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=max(0.1, lock_timeout))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA busy_timeout={max(1, int(lock_timeout * 1000))}")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        version = schema_version(con)
        if version is None or version < INDEX_SCHEMA_VERSION:
            if schema_locked:
                ensure_schema(con)
            else:
                with WriterLock(
                    root,
                    "index-schema-migration",
                    timeout=lock_timeout,
                    recover_stale=recover_stale,
                ):
                    ensure_schema(con)
        else:
            ensure_schema(con)
    except BaseException:
        con.close()
        raise
    return con


def connect_readonly(root: Path, *, lock_timeout: float = 10.0) -> sqlite3.Connection:
    """Open an existing compatible index without creating or migrating anything."""
    p = db_path(root)
    if not p.is_file():
        raise ReadOnlyIndexError(
            f"ELM index does not exist: {p}. Run `elm rebuild` before read-only access."
        )
    try:
        con = sqlite3.connect(
            f"{p.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=max(0.1, lock_timeout),
        )
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={max(1, int(lock_timeout * 1000))}")
        con.execute("PRAGMA query_only=ON")
        version = schema_version(con)
        if version != INDEX_SCHEMA_VERSION:
            con.close()
            raise ReadOnlyIndexError(
                "ELM index schema is not ready for read-only access: "
                f"expected {INDEX_SCHEMA_VERSION}, found {version!r}. Run `elm sync` or `elm rebuild`."
            )
        return con
    except ReadOnlyIndexError:
        raise
    except sqlite3.Error as exc:
        raise ReadOnlyIndexError(f"ELM index could not be opened read-only: {exc}") from exc


def relpath(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def is_archive_path(relative: str) -> bool:
    parts = set(Path(relative).parts)
    if parts & ARCHIVE_PARTS:
        return True
    name = Path(relative).name
    return any(p.search(name) for p in ARCHIVE_PATTERNS)


def iter_markdown(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.md"):
        if ".elm" in p.parts:
            continue
        if p.is_file():
            yield p


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_metadata(lines: list[str]) -> tuple[dict[str, str], int]:
    meta: dict[str, str] = {}
    end = 0
    for i, raw in enumerate(lines[:80]):
        line = raw.rstrip("\r\n")
        if line.startswith("#"):
            end = i
            break
        if not line.strip():
            end = i + 1
            continue
        m = re.match(r"^([A-Za-z][A-Za-z ]+):\s*(.*)$", line)
        if not m:
            if meta:
                end = i
                break
            continue
        key = m.group(1).strip().lower()
        if key not in META_KEYS:
            if meta:
                end = i
                break
            continue
        meta[META_KEYS[key]] = m.group(2).strip()
        end = i + 1
    return meta, end


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_sections(lines: list[str], metadata_end: int) -> list[dict]:
    headings: list[tuple[int, int, str]] = []
    for i, raw in enumerate(lines):
        m = HEADING_RE.match(raw.rstrip("\r\n"))
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[dict] = []
    # Preamble after metadata, before first heading.
    first_heading_line = headings[0][0] if headings else len(lines)
    pre = "".join(lines[metadata_end:first_heading_line]).strip()
    if pre:
        sections.append({
            "heading": "[preamble]",
            "heading_path": "[preamble]",
            "level": 0,
            "parent_ordinal": None,
            "start_line": metadata_end + 1,
            "end_line": first_heading_line,
            "text": pre,
        })

    stack: list[tuple[int, str, int]] = []  # level, heading, ordinal
    for idx, (line_no, level, heading) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_ordinal = stack[-1][2] if stack else None
        path_names = [x[1] for x in stack] + [heading]
        next_heading_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        body = "".join(lines[line_no + 1:next_heading_line]).strip()
        ordinal = len(sections)
        sections.append({
            "heading": heading,
            "heading_path": " > ".join(path_names),
            "level": level,
            "parent_ordinal": parent_ordinal,
            "start_line": line_no + 1,
            "end_line": next_heading_line,
            "text": body,
        })
        stack.append((level, heading, ordinal))
    return sections


def derive_area_project(relative: str) -> tuple[str | None, str | None]:
    parts = Path(relative).parts
    area = parts[0] if parts else None
    project = parts[1] if len(parts) >= 3 and parts[0] == "20_projects" else None
    return area, project


def normalize_target(root: Path, source: Path, target: str) -> str | None:
    target = target.strip().strip("<>").replace("\\", "/")
    if not target or target.startswith(("http://", "https://", "mailto:", "obsidian://")):
        return None
    target = target.split("#", 1)[0]
    if not target:
        return None
    candidate = (source.parent / target).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def extract_links(root: Path, source: Path, text: str, meta: dict[str, str]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for anchor, target in MD_LINK_RE.findall(text):
        norm = normalize_target(root, source, target)
        if norm:
            out.append((norm, anchor.strip(), "markdown_link"))
    for target in split_csv(meta.get("related_files")):
        norm = normalize_target(root, source, target)
        if norm:
            out.append((norm, target, "related_file"))
    # preserve order but dedupe
    seen = set()
    unique = []
    for row in out:
        key = (row[0], row[1], row[2])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def index_one(con: sqlite3.Connection, root: Path, path: Path, force: bool = False) -> tuple[bool, int]:
    relative = relpath(root, path)
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    st = path.stat()
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    meta, metadata_end = parse_metadata(lines)
    raw_elm_id = meta.get("elm_id")
    if raw_elm_id and raw_elm_id.lower().startswith("claim_"):
        validate_id(raw_elm_id, "claim")
        source_document_uid = None
    else:
        source_document_uid = validate_document_uid(raw_elm_id)
    archive = 1 if is_archive_path(relative) else 0
    # An archive may be an exact byte-for-byte copy of its active document.
    # Projecting the same durable UID twice would collide in both documents and
    # derived section keys. Keep the UID in canonical metadata/properties, but
    # bind the disposable archive projection to its historical path.
    document_uid = None if archive else source_document_uid
    old = con.execute(
        "SELECT id,content_hash,document_uid FROM documents WHERE path=?", (relative,)
    ).fetchone()
    if (
        old
        and old["content_hash"] == digest
        and old["document_uid"] == document_uid
        and not force
    ):
        return False, int(old["id"])

    title = meta.get("title") or path.stem
    tags = split_csv(meta.get("tags"))
    area, project = derive_area_project(relative)
    namespace = derive_namespace(area, project)
    if old:
        doc_id = int(old["id"])
        con.execute(
            """UPDATE documents SET area=?,project=?,namespace=?,document_uid=?,title=?,scope=?,summary=?,status=?,
               last_updated=?,mtime_ns=?,size_bytes=?,content_hash=?,is_archive=?,indexed_at=? WHERE id=?""",
            (area, project, namespace, document_uid, title, meta.get("scope"), meta.get("summary"), meta.get("status"),
             meta.get("last_updated"), st.st_mtime_ns, st.st_size, digest, archive, now_iso(), doc_id),
        )
        con.execute("DELETE FROM sections_fts WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM sections WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM properties WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM links WHERE source_document_id=?", (doc_id,))
    else:
        cur = con.execute(
            """INSERT INTO documents(path,area,project,namespace,document_uid,title,scope,summary,status,last_updated,
               mtime_ns,size_bytes,content_hash,is_archive,indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (relative, area, project, namespace, document_uid, title, meta.get("scope"), meta.get("summary"), meta.get("status"),
             meta.get("last_updated"), st.st_mtime_ns, st.st_size, digest, archive, now_iso()),
        )
        doc_id = int(cur.lastrowid)

    for k, v in meta.items():
        if k in {"tags", "related_files"}:
            continue
        con.execute("INSERT OR REPLACE INTO properties(document_id,key,value) VALUES(?,?,?)", (doc_id, k, v))

    for tag in tags:
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
        tag_id = con.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (tag,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO document_tags(document_id,tag_id) VALUES(?,?)", (doc_id, tag_id))

    parsed = parse_sections(lines, metadata_end)
    ordinal_to_id: dict[int, int] = {}
    heading_occurrences: dict[str, int] = {}
    for ordinal, sec in enumerate(parsed):
        parent_id = ordinal_to_id.get(sec["parent_ordinal"]) if sec["parent_ordinal"] is not None else None
        normalized_heading = normalize_heading_path(sec["heading_path"])
        occurrence = heading_occurrences.get(normalized_heading, 0)
        heading_occurrences[normalized_heading] = occurrence + 1
        section_key, section_namespace = derive_section_key(
            document_uid, relative, sec["heading_path"], occurrence
        )
        cur = con.execute(
            """INSERT INTO sections(document_id,parent_id,section_key,section_namespace,heading,heading_path,
               level,ordinal,start_line,end_line,token_estimate,text)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, parent_id, section_key, section_namespace, sec["heading"], sec["heading_path"], sec["level"], ordinal,
             sec["start_line"], sec["end_line"], estimate_tokens(sec["text"]), sec["text"]),
        )
        section_id = int(cur.lastrowid)
        ordinal_to_id[ordinal] = section_id
        con.execute(
            "INSERT INTO sections_fts(section_id,document_id,title,heading,body,tags,scope,summary) VALUES(?,?,?,?,?,?,?,?)",
            (str(section_id), str(doc_id), title, sec["heading_path"], sec["text"], " ".join(tags),
             meta.get("scope") or "", meta.get("summary") or ""),
        )

    for target_path, anchor, relation in extract_links(root, path, text, meta):
        con.execute(
            "INSERT INTO links(source_document_id,target_path,anchor_text,relation_type) VALUES(?,?,?,?)",
            (doc_id, target_path, anchor, relation),
        )
    return True, doc_id


def resolve_link_targets(con: sqlite3.Connection) -> None:
    con.execute(
        """UPDATE links
           SET target_document_id=(SELECT d.id FROM documents d WHERE d.path=links.target_path)
           WHERE target_path IS NOT NULL"""
    )
    # Related-file metadata sometimes stores only a filename even when the canonical file lives elsewhere.
    # Resolve that only when the basename is globally unique; ambiguity remains visible to doctor.
    docs = con.execute("SELECT id,path FROM documents").fetchall()
    by_name = {}
    for d in docs:
        name = PurePosixPath(d["path"]).name.lower()
        by_name.setdefault(name, []).append(int(d["id"]))
    unresolved = con.execute("SELECT id,target_path FROM links WHERE target_document_id IS NULL").fetchall()
    for link in unresolved:
        name = PurePosixPath(link["target_path"].replace("\\", "/")).name.lower()
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            con.execute("UPDATE links SET target_document_id=? WHERE id=?", (candidates[0], link["id"]))


def _sync_unlocked(con: sqlite3.Connection, root: Path, force: bool = False) -> dict:
    files = list(iter_markdown(root))
    current = {relpath(root, p) for p in files}
    existing = {r["path"]: r["id"] for r in con.execute("SELECT id,path FROM documents")}
    removed = [p for p in existing if p not in current]
    with con:
        for p in removed:
            doc_id = existing[p]
            con.execute("DELETE FROM sections_fts WHERE document_id=?", (doc_id,))
            con.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    changed = 0
    unchanged = 0
    errors: list[dict] = []
    for path in files:
        try:
            with con:
                did_change, _ = index_one(con, root, path, force=force)
            changed += int(did_change)
            unchanged += int(not did_change)
        except Exception as exc:  # keep the rest of the vault indexable
            errors.append({"path": relpath(root, path), "error": str(exc)})
    with con:
        resolve_link_targets(con)
    try:
        governance = sync_governance_projection(con, root)
    except Exception as exc:
        governance = None
        errors.append({"path": "[governance]", "error": str(exc)})
    return {
        "root": str(root),
        "database": str(db_path(root)),
        "files_seen": len(files),
        "changed": changed,
        "unchanged": unchanged,
        "removed": len(removed),
        "errors": errors,
        "governance": governance,
    }


def sync(
    con: sqlite3.Connection,
    root: Path,
    force: bool = False,
    *,
    acquire_lock: bool = True,
    lock_timeout: float = 10.0,
    recover_stale: bool = False,
) -> dict:
    if not acquire_lock:
        return _sync_unlocked(con, root, force=force)
    with WriterLock(
        root,
        "index-sync",
        timeout=lock_timeout,
        recover_stale=recover_stale,
    ):
        return _sync_unlocked(con, root, force=force)


def safe_fts_query(query: str, broad: bool = False) -> str:
    terms = WORD_RE.findall(query)
    if not terms:
        raise SystemExit("Search query contains no searchable terms.")
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms[:24]]
    return (" OR " if broad else " ").join(quoted)


def fetch_tags(con: sqlite3.Connection, doc_id: int) -> list[str]:
    return [r["name"] for r in con.execute(
        """SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id=t.id
           WHERE dt.document_id=? ORDER BY t.name COLLATE NOCASE""", (doc_id,)
    )]


def _sync_from_args(args, con: sqlite3.Connection, root: Path, *, force: bool = False) -> dict:
    return sync(
        con,
        root,
        force=force,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )


def _require_stable_governance(root: Path) -> None:
    pending = pending_transactions(root)
    if pending:
        raise GovernanceError(
            "Governed reads are unavailable while a canonical transaction is incomplete; "
            "run elm recover --dry-run and recover it explicitly."
        )


def _prepare_governed_read(args, con: sqlite3.Connection, root: Path) -> None:
    _require_stable_governance(root)
    if getattr(args, "no_sync", False):
        return
    result = _sync_from_args(args, con, root)
    governance_errors = [
        item for item in result["errors"] if item.get("path") == "[governance]"
    ]
    if governance_errors:
        raise GovernanceError(
            "Governed projection could not be refreshed; the read was refused instead of "
            f"using ambiguous claim state: {governance_errors[0]['error']}"
        )


def _policy_sql(alias: str, args) -> tuple[list[str], list[object]]:
    conditions: list[str] = []
    params: list[object] = []
    if not args.include_archive:
        conditions.append(f"{alias}.is_archive=0")
    if args.project:
        conditions.append(f"{alias}.project=?")
        params.append(args.project)
    if args.namespace:
        conditions.append(f"{alias}.namespace=?")
        params.append(args.namespace)
    if not getattr(args, "include_history", False):
        moment = now_iso()
        conditions.append(
            f"NOT EXISTS (SELECT 1 FROM claims policy_claim WHERE policy_claim.document_id={alias}.id "
            "AND (policy_claim.status<>'accepted' OR policy_claim.valid_from>? "
            "OR (policy_claim.valid_to IS NOT NULL AND policy_claim.valid_to<=?)))"
        )
        params.extend((moment, moment))
    return conditions, params


def _path_allowed(path: str, args) -> bool:
    if not args.include_archive and is_archive_path(path):
        return False
    area, project = derive_area_project(path)
    if args.project and project != args.project:
        return False
    if args.namespace and derive_namespace(area, project) != args.namespace:
        return False
    return True


def _document_allowed(con: sqlite3.Connection, document_id: int, args) -> bool:
    conditions, params = _policy_sql("d", args)
    where = " AND ".join(["d.id=?"] + conditions)
    return con.execute(
        f"SELECT 1 FROM documents d WHERE {where}",
        [document_id] + params,
    ).fetchone() is not None


def search_sections(
    con: sqlite3.Connection,
    query: str,
    args,
    *,
    limit: int | None = None,
    broad: bool | None = None,
) -> list[dict]:
    fts = safe_fts_query(query, broad=args.broad if broad is None else broad)
    conditions = ["sections_fts MATCH ?"]
    params: list[object] = [fts]
    policy_conditions, policy_params = _policy_sql("d", args)
    conditions.extend(policy_conditions)
    params.extend(policy_params)
    if args.status:
        conditions.append("lower(coalesce(d.status,''))=lower(?)")
        params.append(args.status)
    if args.path_prefix:
        conditions.append("d.path LIKE ?")
        params.append(args.path_prefix.rstrip("/") + "/%")
    for tag in args.tag:
        conditions.append(
            "EXISTS (SELECT 1 FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=d.id AND lower(t.name)=lower(?))"
        )
        params.append(tag)

    sql = f"""
        SELECT d.id AS document_id,d.document_uid,s.id AS section_id,s.section_key,s.section_namespace,
               d.path,d.title,d.namespace,d.project,d.status,d.last_updated,d.is_archive,
               s.heading,s.heading_path,s.start_line,s.end_line,
               c.claim_id,c.subject AS claim_subject,c.predicate AS claim_predicate,
               c.object AS claim_object,c.authority AS claim_authority,c.valid_from,c.valid_to,
               CASE WHEN c.claim_id IS NOT NULL AND EXISTS(
                   SELECT 1 FROM claims other
                   WHERE other.claim_id<>c.claim_id AND other.project=c.project
                     AND other.status='accepted' AND c.status='accepted'
                     AND other.subject=c.subject AND other.predicate=c.predicate
                     AND other.object<>c.object
                     AND c.valid_from<coalesce(other.valid_to,'9999-12-31T23:59:59.999999+00:00')
                     AND other.valid_from<coalesce(c.valid_to,'9999-12-31T23:59:59.999999+00:00')
               ) THEN 1 ELSE 0 END AS contradiction,
               s.token_estimate,s.text,
               -bm25(sections_fts, 0.0,0.0,3.0,2.0,1.0,1.5,1.0,1.0) AS score,
               snippet(sections_fts, 4, '[', ']', ' ... ', 28) AS snippet
        FROM sections_fts
        JOIN sections s ON s.id=CAST(sections_fts.section_id AS INTEGER)
        JOIN documents d ON d.id=s.document_id
        LEFT JOIN claims c ON c.document_id=d.id
        WHERE {' AND '.join(conditions)}
        ORDER BY score DESC, d.last_updated DESC
        LIMIT ?
    """
    params.append(args.limit if limit is None else limit)
    rows = con.execute(sql, params).fetchall()
    results = []
    for r in rows:
        item = dict(r)
        item["is_archive"] = bool(item["is_archive"])
        item["contradiction"] = bool(item["contradiction"])
        item["tags"] = fetch_tags(con, int(item["document_id"]))
        results.append(item)
    return results


def command_search(args, con: sqlite3.Connection, root: Path):
    _prepare_governed_read(args, con, root)
    results = search_sections(con, args.query, args)
    public_results = []
    for result in results:
        item = dict(result)
        item.pop("text", None)
        public_results.append(item)
    emit({"query": args.query, "count": len(public_results), "results": public_results}, args.json)


def _context_supplemental_sections(
    con: sqlite3.Connection,
    args,
    project: str | None,
) -> list[dict]:
    if not project:
        return []
    conditions = ["d.is_archive=0", "d.project=?"]
    params: list[object] = [project]
    if args.namespace:
        conditions.append("d.namespace=?")
        params.append(args.namespace)
    if args.status:
        conditions.append("lower(coalesce(d.status,''))=lower(?)")
        params.append(args.status)
    if args.path_prefix:
        conditions.append("d.path LIKE ?")
        params.append(args.path_prefix.rstrip("/") + "/%")
    for tag in args.tag:
        conditions.append(
            "EXISTS (SELECT 1 FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=d.id AND lower(t.name)=lower(?))"
        )
        params.append(tag)
    conditions.extend(
        (
            "(lower(d.path) LIKE '%/active_context.md' OR lower(d.path) LIKE '%/project_hub.md')",
            "(lower(s.heading) LIKE '%current%' OR lower(s.heading) LIKE '%focus%' "
            "OR lower(s.heading) LIKE '%constraint%')",
        )
    )
    rows = con.execute(
        f"""SELECT d.id AS document_id,d.document_uid,s.id AS section_id,s.section_key,
                   s.section_namespace,d.path,d.title,d.namespace,d.project,d.status,
                   d.last_updated,d.is_archive,s.heading,s.heading_path,s.start_line,
                   s.end_line,s.token_estimate,s.text,0.0 AS score,'' AS snippet
            FROM sections s JOIN documents d ON d.id=s.document_id
            WHERE {' AND '.join(conditions)}
            ORDER BY CASE
                WHEN lower(s.heading) LIKE '%constraint%' THEN 0
                WHEN lower(d.path) LIKE '%/active_context.md' THEN 1
                ELSE 2 END,
                d.last_updated DESC,s.ordinal
            LIMIT 4""",
        params,
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["is_archive"] = bool(item["is_archive"])
        item["tags"] = fetch_tags(con, int(item["document_id"]))
        results.append(item)
    return results


def command_context(args, con: sqlite3.Connection, root: Path):
    started = time.perf_counter()
    if args.budget < MIN_CONTEXT_BUDGET:
        raise ValueError(
            f"Context budget must be at least {MIN_CONTEXT_BUDGET} estimated tokens."
        )
    if args.limit < 1:
        raise ValueError("Context candidate limit must be at least 1.")
    if args.trace_retention_days < 0:
        raise ValueError("Trace retention days cannot be negative.")
    _prepare_governed_read(args, con, root)

    results = search_sections(con, args.task, args, limit=args.limit, broad=False)
    fallback_used = False
    if not results and len(WORD_RE.findall(args.task)) > 1:
        results = search_sections(con, args.task, args, limit=args.limit, broad=True)
        fallback_used = bool(results)

    projects = {
        str(item["project"])
        for item in results[:5]
        if item.get("project") is not None
    }
    resolved_project = args.project
    scope_warnings: list[str] = []
    if resolved_project is None and len(projects) == 1:
        resolved_project = next(iter(projects))
        scope_warnings.append(
            f"Project scope was inferred from the top retrieval candidates as {resolved_project}."
        )
    elif resolved_project is None and len(projects) > 1:
        scope_warnings.append(
            "Project scope is ambiguous; pass --project to prevent cross-project context mixing."
        )

    supplemental = _context_supplemental_sections(con, args, resolved_project)
    # FTS matches stay first so a small budget cannot be consumed entirely by
    # generic current-state supplements before the task-relevant source appears.
    candidates = results + supplemental
    scope = {
        "project": resolved_project,
        "project_resolution": "explicit" if args.project else ("inferred" if resolved_project else "unresolved"),
        "namespace": args.namespace,
        "include_archive": bool(args.include_archive),
        "status": args.status,
    }
    response = build_context_packet(
        args.task,
        candidates,
        args.budget,
        scope=scope,
        additional_warnings=scope_warnings,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    candidate_keys = list(dict.fromkeys(
        str(candidate["section_key"])
        for candidate in candidates
        if candidate.get("section_key")
    ))
    if args.no_trace:
        response["trace"] = {"recorded": False, "reason": "disabled_by_request"}
    else:
        filters = {
            "project": args.project,
            "resolved_project": resolved_project,
            "namespace": args.namespace,
            "include_archive": bool(args.include_archive),
            "status": args.status,
            "path_prefix": args.path_prefix,
            "tags": list(args.tag),
        }
        try:
            response["trace"] = write_retrieval_trace(
                root,
                task=args.task,
                include_query_text=args.trace_query_text,
                project=resolved_project,
                filters=filters,
                candidate_section_keys=candidate_keys,
                selected_section_keys=response["selected_section_keys"],
                estimated_tokens=response["estimated_tokens"],
                latency_ms=latency_ms,
                fallback_used=fallback_used,
                retention_days=args.trace_retention_days,
            )
        except OSError as exc:
            response["trace"] = {
                "recorded": False,
                "reason": "trace_write_failed",
                "error": str(exc),
            }
    response["fallback_used"] = fallback_used
    response["latency_ms"] = round(latency_ms, 3)
    emit(response, args.json)


def command_traces_cleanup(args, root: Path) -> None:
    if args.retention_days is not None and args.retention_days < 0:
        raise ValueError("Trace retention days cannot be negative.")
    if args.apply:
        with WriterLock(
            root,
            "trace-cleanup",
            timeout=args.lock_timeout,
            recover_stale=args.recover_stale_lock,
        ):
            result = cleanup_retrieval_traces(
                root, apply=True, retention_days=args.retention_days
            )
    else:
        result = cleanup_retrieval_traces(
            root, apply=False, retention_days=args.retention_days
        )
    emit(result, args.json)


def resolve_document(con: sqlite3.Connection, ref: str, args) -> sqlite3.Row:
    reference_conditions: list[str]
    reference_params: list[object]
    if ref.isdigit():
        reference_conditions = ["d.id=?"]
        reference_params = [int(ref)]
    elif ref.lower().startswith("doc_"):
        reference_conditions = ["d.document_uid=?"]
        reference_params = [ref.lower()]
    elif ref.lower().startswith("claim_"):
        claim_id = validate_id(ref, "claim")
        reference_conditions = ["d.id=(SELECT document_id FROM claims WHERE claim_id=?)"]
        reference_params = [claim_id]
    else:
        reference_conditions = ["d.path=?"]
        reference_params = [ref.replace("\\", "/")]
    policy_conditions, policy_params = _policy_sql("d", args)
    conditions = reference_conditions + policy_conditions
    row = con.execute(
        f"SELECT d.* FROM documents d WHERE {' AND '.join(conditions)}",
        reference_params + policy_params,
    ).fetchone()
    if row is None:
        raise SystemExit(f"Document not found under the current read policy: {ref}")
    return row


def resolve_section(con: sqlite3.Connection, ref: str, args) -> sqlite3.Row:
    if ref.isdigit():
        reference = "s.id=?"
        value: object = int(ref)
    else:
        reference = "s.section_key=?"
        value = ref.lower()
    policy_conditions, policy_params = _policy_sql("d", args)
    conditions = [reference] + policy_conditions
    row = con.execute(
        f"""SELECT s.*,d.path,d.document_uid,d.namespace,d.title,d.project,d.status,
                   d.last_updated,d.is_archive
            FROM sections s JOIN documents d ON d.id=s.document_id
            WHERE {' AND '.join(conditions)}""",
        [value] + policy_params,
    ).fetchone()
    if row is None:
        raise SystemExit(f"Section not found under the current read policy: {ref}")
    return row


def command_outline(args, con: sqlite3.Connection, root: Path):
    _prepare_governed_read(args, con, root)
    doc = resolve_document(con, args.document, args)
    rows = con.execute(
        """SELECT id,parent_id,section_key,section_namespace,heading,heading_path,level,
                  ordinal,start_line,end_line,token_estimate
           FROM sections WHERE document_id=? ORDER BY ordinal""",
        (doc["id"],),
    ).fetchall()
    emit({"document": dict(doc), "sections": [dict(r) for r in rows]}, args.json)


def command_read(args, con: sqlite3.Connection, root: Path):
    _require_stable_governance(root)
    emit(dict(resolve_section(con, args.section, args)), args.json)


def command_related(args, con: sqlite3.Connection, root: Path):
    _prepare_governed_read(args, con, root)
    doc = resolve_document(con, args.document, args)
    outgoing_rows = [dict(r) for r in con.execute(
        """SELECT l.relation_type,l.anchor_text,l.target_path,d.id AS target_id,
                  d.document_uid AS target_uid,d.namespace AS target_namespace,
                  d.project AS target_project,d.is_archive AS target_is_archive,
                  d.title AS target_title,d.status AS target_status
           FROM links l LEFT JOIN documents d ON d.id=l.target_document_id
           WHERE l.source_document_id=? ORDER BY l.relation_type,l.target_path""", (doc["id"],)
    )]
    outgoing = [
        row for row in outgoing_rows
        if _path_allowed(row["target_path"], args)
        and not (
            row["target_id"] is None
            and not getattr(args, "include_history", False)
            and re.search(r"(?:^|/)CLAIMS/claim_[0-9a-f-]+\.md$", row["target_path"], re.I)
        )
        and (row["target_id"] is None or _document_allowed(con, int(row["target_id"]), args))
    ]
    incoming_conditions, incoming_params = _policy_sql("s", args)
    incoming = [dict(r) for r in con.execute(
        f"""SELECT l.relation_type,l.anchor_text,s.id AS source_id,s.document_uid AS source_uid,
                  s.namespace AS source_namespace,s.path AS source_path,s.title AS source_title,
                  s.status AS source_status
           FROM links l JOIN documents s ON s.id=l.source_document_id
           WHERE l.target_document_id=?
             {('AND ' + ' AND '.join(incoming_conditions)) if incoming_conditions else ''}
           ORDER BY s.path""",
        [doc["id"]] + incoming_params,
    )]
    emit({"document": dict(doc), "outgoing": outgoing, "incoming": incoming}, args.json)


def command_stats(args, con: sqlite3.Connection, root: Path):
    _prepare_governed_read(args, con, root)
    row = con.execute(
        """SELECT COUNT(*) docs, SUM(CASE WHEN is_archive=0 THEN 1 ELSE 0 END) active_docs,
                  SUM(CASE WHEN is_archive=1 THEN 1 ELSE 0 END) archive_docs FROM documents"""
    ).fetchone()
    out = dict(row)
    out.update({
        "sections": con.execute("SELECT COUNT(*) c FROM sections").fetchone()["c"],
        "tags": con.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"],
        "links": con.execute("SELECT COUNT(*) c FROM links").fetchone()["c"],
        "broken_links_all": con.execute("SELECT COUNT(*) c FROM links WHERE target_document_id IS NULL").fetchone()["c"],
        "broken_links_active": con.execute(
            """SELECT COUNT(*) c FROM links l JOIN documents d ON d.id=l.source_document_id
               WHERE l.target_document_id IS NULL AND d.is_archive=0"""
        ).fetchone()["c"],
        "index_schema_version": schema_version(con),
        "proposals": con.execute("SELECT COUNT(*) c FROM governance_proposals").fetchone()["c"],
        "evidence_refs": con.execute("SELECT COUNT(*) c FROM governance_evidence").fetchone()["c"],
        "claims": con.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"],
        "current_claims": con.execute(
            "SELECT COUNT(*) c FROM claims WHERE status='accepted' AND valid_from<=? "
            "AND (valid_to IS NULL OR valid_to>?)",
            (now_iso(), now_iso()),
        ).fetchone()["c"],
        "governance_events": con.execute("SELECT COUNT(*) c FROM governance_events").fetchone()["c"],
        "tombstones": con.execute("SELECT COUNT(*) c FROM governance_tombstones").fetchone()["c"],
        "database": str(db_path(root)),
    })
    emit(out, args.json)


def status_snapshot(root: Path) -> dict:
    """Describe read-only index readiness without creating or refreshing derived state."""
    p = db_path(root)
    pending = pending_transactions(root)
    result = {
        "root": str(root),
        "database": str(p),
        "index_exists": p.is_file(),
        "index_schema_version": None,
        "expected_index_schema_version": INDEX_SCHEMA_VERSION,
        "schema_compatible": False,
        "quick_check": None,
        "sync_required": True,
        "indexed_documents": 0,
        "markdown_files": sum(1 for _ in iter_markdown(root)),
        "changed_or_unindexed_files": [],
        "removed_index_entries": [],
        "pending_transaction_count": len(pending),
        "pending_transactions": pending,
        "root_identity": None,
        "governance_projection_sha256": None,
        "canonical_governance_sha256": None,
        "governance_projection_current": False,
        "healthy": False,
        "errors": [],
    }
    try:
        result["root_identity"] = load_root_identity(root)
    except GovernanceError as exc:
        result["errors"].append(f"root_identity_invalid: {exc}")
    if not p.is_file():
        result["errors"].append("index_missing")
        return result

    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(
            f"{p.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=2.0,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        version = schema_version(con)
        result["index_schema_version"] = version
        result["schema_compatible"] = version == INDEX_SCHEMA_VERSION
        result["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        if not result["schema_compatible"]:
            result["errors"].append("incompatible_schema")
            return result

        projection_row = con.execute(
            "SELECT value FROM elm_meta WHERE key='governance_projection_sha256'"
        ).fetchone()
        projected_digest = str(projection_row[0]) if projection_row else None
        canonical_digest = governance_projection_digest(root)
        result["governance_projection_sha256"] = projected_digest
        result["canonical_governance_sha256"] = canonical_digest
        result["governance_projection_current"] = projected_digest == canonical_digest
        if not result["governance_projection_current"]:
            result["errors"].append("governance_projection_stale")

        indexed = {
            row["path"]: (
                int(row["mtime_ns"]),
                int(row["size_bytes"]),
                row["content_hash"],
            )
            for row in con.execute(
                "SELECT path,mtime_ns,size_bytes,content_hash FROM documents"
            )
        }
        current: dict[str, tuple[int, int, str]] = {}
        for path in iter_markdown(root):
            raw = path.read_bytes()
            stat = path.stat()
            current[relpath(root, path)] = (
                stat.st_mtime_ns,
                stat.st_size,
                sha256_bytes(raw),
            )
        changed = sorted(
            path for path, signature in current.items() if indexed.get(path) != signature
        )
        removed = sorted(path for path in indexed if path not in current)
        result["indexed_documents"] = len(indexed)
        result["changed_or_unindexed_files"] = changed[:100]
        result["removed_index_entries"] = removed[:100]
        result["sync_required"] = bool(changed or removed)
        if len(changed) > 100 or len(removed) > 100:
            result["errors"].append("freshness_report_truncated")
    except (OSError, sqlite3.Error) as exc:
        result["errors"].append(f"index_read_failed: {exc}")
    finally:
        if con is not None:
            con.close()

    result["healthy"] = bool(
        result["schema_compatible"]
        and result["quick_check"] == "ok"
        and not result["sync_required"]
        and result["governance_projection_current"]
        and result["pending_transaction_count"] == 0
        and not result["errors"]
    )
    return result


def command_status(args, root: Path) -> None:
    emit(status_snapshot(root), args.json)


def command_doctor(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        _sync_from_args(args, con, root)
    issues: list[dict] = []
    for transaction in pending_transactions(root):
        issues.append({"kind": "incomplete_transaction", **transaction})
    active = con.execute("SELECT * FROM documents WHERE is_archive=0 ORDER BY path").fetchall()
    for d in active:
        props = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM properties WHERE document_id=?", (d["id"],))}
        for display in EXPECTED_META:
            key = META_KEYS[display.lower()]
            if key == "tags":
                missing = len(fetch_tags(con, int(d["id"]))) == 0
            elif key == "title":
                missing = not bool(d["title"])
            else:
                missing = key not in props or not str(props.get(key, "")).strip()
            if missing:
                issues.append({"kind": "missing_metadata", "path": d["path"], "field": display})
    for r in con.execute(
        """SELECT s.path AS source_path,l.target_path,l.anchor_text,l.relation_type
           FROM links l JOIN documents s ON s.id=l.source_document_id
           WHERE s.is_archive=0 AND l.target_document_id IS NULL ORDER BY s.path,l.target_path"""
    ):
        issues.append({"kind": "broken_link", **dict(r)})
    for r in con.execute(
        """SELECT coalesce(project,area,'') scope_key,lower(title) k,title,COUNT(*) c,GROUP_CONCAT(path,' | ') paths
           FROM documents WHERE is_archive=0
             AND NOT EXISTS (SELECT 1 FROM claims duplicate_claim WHERE duplicate_claim.document_id=documents.id)
           GROUP BY coalesce(project,area,''),lower(title) HAVING COUNT(*)>1 ORDER BY c DESC"""
    ):
        issues.append({"kind": "duplicate_title", "scope": r["scope_key"], "title": r["title"], "count": r["c"], "paths": r["paths"]})
    tombstoned = {
        row["item_id"] for row in con.execute("SELECT item_id FROM governance_tombstones")
    }
    evidence_ids = {
        row["evidence_id"] for row in con.execute("SELECT evidence_id FROM governance_evidence")
    }
    proposal_ids = {
        row["proposal_id"] for row in con.execute("SELECT proposal_id FROM governance_proposals")
    }
    claim_rows = [dict(row) for row in con.execute("SELECT * FROM claims ORDER BY claim_id")]
    for claim in claim_rows:
        if claim["proposal_id"] not in proposal_ids and claim["proposal_id"] not in tombstoned:
            issues.append({"kind": "missing_claim_proposal", "claim_id": claim["claim_id"], "proposal_id": claim["proposal_id"]})
        for evidence_id in json.loads(claim["evidence_ids_json"]):
            if evidence_id not in evidence_ids and evidence_id not in tombstoned:
                issues.append({"kind": "missing_claim_evidence", "claim_id": claim["claim_id"], "evidence_id": evidence_id})
        if claim["status"] == "superseded" and (not claim["valid_to"] or not claim["superseded_by"]):
            issues.append({"kind": "invalid_supersession", "claim_id": claim["claim_id"]})
    for index, left in enumerate(claim_rows):
        if left["status"] != "accepted":
            continue
        for right in claim_rows[index + 1:]:
            if right["status"] != "accepted":
                continue
            if (left["project"], left["subject"], left["predicate"]) != (right["project"], right["subject"], right["predicate"]):
                continue
            left_end = left["valid_to"] or "9999-12-31T23:59:59.999999+00:00"
            right_end = right["valid_to"] or "9999-12-31T23:59:59.999999+00:00"
            if left["object"] != right["object"] and left["valid_from"] < right_end and right["valid_from"] < left_end:
                issues.append({
                    "kind": "claim_contradiction",
                    "left_claim_id": left["claim_id"],
                    "right_claim_id": right["claim_id"],
                    "subject": left["subject"],
                    "predicate": left["predicate"],
                })
    emit({"issue_count": len(issues), "issues": issues[:args.limit], "truncated": len(issues) > args.limit}, args.json)


def _sync_governance_result(args, con: sqlite3.Connection, root: Path, result: dict) -> None:
    indexed = _sync_from_args(args, con, root)
    if indexed["errors"]:
        raise GovernanceError(f"Canonical mutation succeeded but derived sync reported errors: {indexed['errors']}")
    result["sync"] = indexed
    emit(result, args.json)


def command_evidence_add(args, con: sqlite3.Connection, root: Path) -> None:
    result = create_evidence_reference(
        root,
        project=args.project,
        kind=args.kind,
        source_uri=args.source_uri,
        content_sha256=args.content_sha256,
        excerpt_sha256=args.excerpt_sha256,
        sensitivity=args.sensitivity,
        actor=args.actor,
        captured_at=args.captured_at,
    )
    _sync_governance_result(args, con, root, result)


def command_propose(args, con: sqlite3.Connection, root: Path) -> None:
    result = create_proposal(
        root,
        project=args.project,
        subject=args.subject,
        predicate=args.predicate,
        object_value=args.object,
        actor=args.actor,
        requested_authority=args.requested_authority,
        valid_from=args.valid_from,
        sensitivity=args.sensitivity,
        evidence_ids=args.evidence,
        source_refs=args.source_ref,
        rationale=args.rationale,
    )
    _sync_governance_result(args, con, root, result)


def command_proposal_submit(args, con: sqlite3.Connection, root: Path) -> None:
    raw = sys.stdin.buffer.read(args.max_request_bytes + 1)
    if len(raw) > args.max_request_bytes:
        raise GovernanceError(
            f"proposal request exceeds the {args.max_request_bytes}-byte limit."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GovernanceError("proposal request stdin must be UTF-8 JSON.") from exc
    try:
        request = parse_closed_json(text)
    except CanonicalJSONError as exc:
        raise GovernanceError(str(exc)) from exc
    if not isinstance(request, dict):
        raise GovernanceError("proposal request must be one JSON object.")
    limits = ProposalLimits(
        max_request_bytes=args.max_request_bytes,
        max_reference_count=args.max_reference_count,
        max_pending_per_project=args.max_pending_per_project,
        max_pending_records_root=args.max_pending_records_root,
        max_pending_bytes_per_project=args.max_pending_bytes_per_project,
        max_pending_bytes_root=args.max_pending_bytes_root,
    )
    result = submit_proposal_bundle(
        root,
        request=request,
        request_bytes=len(raw),
        allowed_projects=set(args.allow_project),
        limits=limits,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    try:
        indexed = _sync_from_args(args, con, root)
    except Exception as exc:
        kind = (
            "writer_lock_unavailable"
            if isinstance(exc, WriterLockError)
            else "projection_refresh_failed"
        )
        message = (
            str(exc)
            if isinstance(exc, WriterLockError)
            else (
                "Canonical proposal committed, but the disposable projection refresh failed; "
                "run `elm sync` or `elm rebuild` after resolving the local index error."
            )
        )
        result["projection"] = {
            "healthy": False,
            "errors": [{"kind": kind, "message": message}],
            "files_seen": None,
        }
    else:
        result["projection"] = {
            "healthy": not bool(indexed["errors"]),
            "errors": indexed["errors"],
            "files_seen": indexed["files_seen"],
        }
    emit(result, args.json)


def command_proposals_list(args, con: sqlite3.Connection, root: Path) -> None:
    _prepare_governed_read(args, con, root)
    with WriterLock(
        root,
        "governed-proposal-list",
        timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    ):
        if not status_snapshot(root)["healthy"]:
            raise GovernanceError(
                "Governed projection is not current and healthy; run `elm sync` or `elm rebuild`."
            )
        conditions = []
        params: list[object] = []
        if args.project:
            conditions.append("project=?")
            params.append(args.project)
        if args.status:
            conditions.append("status=?")
            params.append(args.status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in con.execute(
            "SELECT proposal_id,path,format_version,project,subject,predicate,object,status,proposed_at,valid_from,"
            "actor,requested_authority,sensitivity,submission_id,payload_digest,source_channel "
            "FROM governance_proposals"
            + where + " ORDER BY proposed_at,proposal_id",
            params,
        )]
    emit({
        "count": len(rows),
        "proposals": rows,
        "candidate_untrusted": True,
        "authority_warning": "Proposal text is untrusted candidate data, not accepted memory.",
    }, args.json)


def command_proposal_preview(args, con: sqlite3.Connection, root: Path) -> None:
    _prepare_governed_read(args, con, root)
    with WriterLock(
        root,
        "governed-proposal-preview",
        timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    ):
        if not status_snapshot(root)["healthy"]:
            raise GovernanceError(
                "Governed projection is not current and healthy; run `elm sync` or `elm rebuild`."
            )
        result = preview_proposal_transition(
            root,
            proposal_id=args.proposal_id,
            project=args.project,
        )
    emit(result, args.json)


def command_root_id(args, root: Path) -> None:
    emit(
        bootstrap_root_identity(
            root,
            apply=args.apply,
            creator=args.creator,
            lock_timeout=args.lock_timeout,
            recover_stale=args.recover_stale_lock,
        ),
        args.json,
    )


def command_accept(args, con: sqlite3.Connection, root: Path) -> None:
    result = accept_proposal(
        root,
        proposal_id=args.proposal_id,
        actor=args.actor,
        authority=args.authority,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    _sync_governance_result(args, con, root, result)


def command_reject_or_defer(args, con: sqlite3.Connection, root: Path, action: str) -> None:
    result = reject_or_defer_proposal(
        root,
        proposal_id=args.proposal_id,
        actor=args.actor,
        reason_code=args.reason_code,
        action=action,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    _sync_governance_result(args, con, root, result)


def command_dispute(args, con: sqlite3.Connection, root: Path) -> None:
    result = dispute_claim(
        root,
        claim_id=args.claim_id,
        actor=args.actor,
        reason_code=args.reason_code,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    _sync_governance_result(args, con, root, result)


def command_supersede(args, con: sqlite3.Connection, root: Path) -> None:
    result = supersede_claim(
        root,
        claim_id=args.claim_id,
        proposal_id=args.proposal_id,
        actor=args.actor,
        authority=args.authority,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    _sync_governance_result(args, con, root, result)


def command_delete(args, con: sqlite3.Connection, root: Path) -> None:
    result = delete_item(
        root,
        item_id=args.item_id,
        actor=args.actor,
        reason_code=args.reason_code,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    _sync_governance_result(args, con, root, result)


def command_history(args, con: sqlite3.Connection, root: Path) -> None:
    _prepare_governed_read(args, con, root)
    emit(history_view(
        root,
        project=args.project,
        subject=args.subject,
        predicate=args.predicate,
        valid_at=args.valid_at,
        recorded_at=args.recorded_at,
        include_deleted=args.include_deleted,
    ), args.json)


def command_recover(args, con: sqlite3.Connection, root: Path) -> None:
    result = recover_governance_transactions(
        root,
        apply=args.apply,
        lock_timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    )
    if args.apply:
        indexed = _sync_from_args(args, con, root)
        if indexed["errors"]:
            raise GovernanceError(f"Recovery completed but derived sync reported errors: {indexed['errors']}")
        result["sync"] = indexed
    emit(result, args.json)


def _add_document_uid(raw: bytes, document_uid: str) -> bytes:
    has_bom = raw.startswith(codecs.BOM_UTF8)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Canonical Markdown must be valid UTF-8 before assigning an ELM ID.") from exc
    lines = text.splitlines(keepends=True)
    metadata, metadata_end = parse_metadata(lines)
    raw_elm_id = metadata.get("elm_id")
    if raw_elm_id and raw_elm_id.lower().startswith("claim_"):
        validate_id(raw_elm_id, "claim")
        return raw
    existing = validate_document_uid(raw_elm_id)
    if existing:
        return raw
    insert_at = metadata_end
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    newline = "\r\n" if "\r\n" in text else "\n"
    lines.insert(insert_at, f"ELM ID: {document_uid}{newline}")
    updated = "".join(lines).encode("utf-8")
    return (codecs.BOM_UTF8 + updated) if has_bom else updated


def _plan_document_uids(root: Path, args) -> list[dict]:
    plan: list[dict] = []
    seen_uids: dict[str, str] = {}
    prefix = args.path_prefix.replace("\\", "/").strip("/") if args.path_prefix else None
    for path in sorted(iter_markdown(root), key=lambda item: relpath(root, item).casefold()):
        relative = relpath(root, path)
        if not args.include_archive and is_archive_path(relative):
            continue
        if prefix and relative != prefix and not relative.startswith(prefix + "/"):
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Cannot assign an ID to non-UTF-8 Markdown: {relative}") from exc
        metadata, _ = parse_metadata(text.splitlines(keepends=True))
        raw_elm_id = metadata.get("elm_id")
        if raw_elm_id and raw_elm_id.lower().startswith("claim_"):
            validate_id(raw_elm_id, "claim")
            continue
        existing = validate_document_uid(raw_elm_id)
        if existing:
            previous = seen_uids.get(existing)
            if previous:
                raise ValueError(f"Duplicate ELM ID {existing}: {previous} and {relative}")
            seen_uids[existing] = relative
            continue
        document_uid = new_document_uid()
        while document_uid in seen_uids:
            document_uid = new_document_uid()
        seen_uids[document_uid] = relative
        plan.append({
            "path": relative,
            "document_uid": document_uid,
            "content_hash": sha256_bytes(raw),
            "raw": raw,
            "updated": _add_document_uid(raw, document_uid),
        })
    return plan


def _public_uid_plan(plan: list[dict]) -> list[dict]:
    return [
        {
            "path": item["path"],
            "document_uid": item["document_uid"],
            "content_hash": item["content_hash"],
        }
        for item in plan
    ]


def _write_uid_manifest(backup_root: Path, status: str, plan: list[dict], error: str | None = None) -> None:
    manifest = {
        "format_version": 1,
        "operation": "ids-assign",
        "status": status,
        "recorded_at": now_iso(),
        "documents": _public_uid_plan(plan),
    }
    if error:
        manifest["error"] = error
    atomic_write_bytes(
        backup_root / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _apply_document_uids(root: Path, args, plan: list[dict]) -> dict:
    if not plan:
        return {
            "mode": "apply",
            "planned": 0,
            "changed": 0,
            "backup": None,
            "documents": [],
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = root / "backups" / f"elm-ids-{stamp}-{os.getpid()}"
    changed: list[dict] = []
    with WriterLock(
        root,
        "ids-assign",
        timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    ):
        for item in plan:
            current = (root / item["path"]).read_bytes()
            if sha256_bytes(current) != item["content_hash"]:
                raise RuntimeError(
                    f"Canonical Markdown changed after dry planning: {item['path']}. No files were modified."
                )
        for item in plan:
            atomic_write_bytes(backup_root / item["path"], item["raw"])
        _write_uid_manifest(backup_root, "prepared", plan)
        try:
            for item in plan:
                target = root / item["path"]
                atomic_write_bytes(target, item["updated"])
                changed.append(item)
                metadata, _ = parse_metadata(
                    target.read_text(encoding="utf-8-sig").splitlines(keepends=True)
                )
                if validate_document_uid(metadata.get("elm_id")) != item["document_uid"]:
                    raise RuntimeError(f"Post-write UID validation failed: {item['path']}")
        except BaseException as exc:
            rollback_errors: list[str] = []
            for item in reversed(changed):
                try:
                    atomic_write_bytes(root / item["path"], item["raw"])
                except BaseException as rollback_exc:
                    rollback_errors.append(f"{item['path']}: {rollback_exc}")
            detail = str(exc)
            if rollback_errors:
                detail += "; rollback errors: " + " | ".join(rollback_errors)
            _write_uid_manifest(backup_root, "rolled_back", plan, detail)
            raise RuntimeError(detail) from exc
        _write_uid_manifest(backup_root, "applied", plan)
    return {
        "mode": "apply",
        "planned": len(plan),
        "changed": len(changed),
        "backup": str(backup_root),
        "documents": _public_uid_plan(plan),
    }


def command_ids_assign(args, root: Path) -> None:
    plan = _plan_document_uids(root, args)
    if args.dry_run:
        emit({
            "mode": "dry-run",
            "planned": len(plan),
            "changed": 0,
            "backup": None,
            "documents": _public_uid_plan(plan),
        }, args.json)
        return
    emit(_apply_document_uids(root, args, plan), args.json)


def command_rebuild(args, con: sqlite3.Connection, root: Path):
    con.close()
    p = db_path(root)
    with WriterLock(
        root,
        "index-rebuild",
        timeout=args.lock_timeout,
        recover_stale=args.recover_stale_lock,
    ):
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(p) + suffix).unlink()
            except FileNotFoundError:
                pass
        con2 = connect(
            root,
            schema_locked=True,
            lock_timeout=args.lock_timeout,
            recover_stale=args.recover_stale_lock,
        )
        try:
            result = sync(con2, root, force=True, acquire_lock=False)
        finally:
            con2.close()
    emit(result, args.json)


def emit(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if isinstance(data, dict) and "results" in data:
        print(f"Query: {data['query']}  Results: {data['count']}")
        for i, r in enumerate(data["results"], 1):
            tags = ", ".join(r.get("tags", []))
            print(f"{i:>2}. D{r['document_id']} S{r['section_id']}  {r['path']}")
            print(f"    {r['heading_path']}  score={r['score']:.3f}  tokens~{r['token_estimate']}")
            if tags:
                print(f"    tags: {tags}")
            print(f"    {r['snippet']}")
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_error(
    code: str,
    message: str,
    as_json: bool,
    details: dict | None = None,
) -> None:
    if as_json:
        payload = {"error": code, "message": message}
        if details:
            payload["details"] = details
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return
    print(f"elm: {message}", file=sys.stderr)


def add_common(p):
    p.add_argument("--root", help="ELM root. Defaults to ELM_ROOT, a config pointer, or the current directory.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument("--lock-timeout", type=float, default=10.0, help="Seconds to wait for the ELM writer lock.")
    p.add_argument(
        "--recover-stale-lock",
        action="store_true",
        help="Explicitly recover a lock whose owner is gone or whose age exceeds the stale threshold.",
    )


def add_read_policy(p) -> None:
    p.add_argument("--project")
    p.add_argument("--namespace", choices=("workspace", "shared", "project"))
    p.add_argument("--include-archive", action="store_true")
    p.add_argument(
        "--include-history",
        action="store_true",
        help="Explicitly allow disputed, superseded, future, or expired claim documents.",
    )


def add_governance_actor(p) -> None:
    add_common(p)
    p.add_argument("--actor", required=True, help="Non-authenticating provenance label for the explicit operator.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elm", description="ELM deterministic index and progressive-retrieval CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="Incrementally index changed Markdown files.")
    add_common(p); p.add_argument("--force", action="store_true")

    p = sub.add_parser("rebuild", help="Delete and rebuild the disposable SQLite index.")
    add_common(p)

    p = sub.add_parser("search", help="Search indexed sections and return compact candidate manifests.")
    add_common(p)
    p.add_argument("query")
    p.add_argument("--tag", action="append", default=[])
    add_read_policy(p)
    p.add_argument("--status")
    p.add_argument("--path-prefix")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--broad", action="store_true", help="Use OR between query terms for recall-heavy fallback search.")
    p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("context", help="Compile a bounded, source-linked context packet for a task.")
    add_common(p)
    p.add_argument("task")
    p.add_argument("--budget", type=int, required=True, help="Maximum estimated tokens in the rendered packet.")
    p.add_argument("--tag", action="append", default=[])
    add_read_policy(p)
    p.add_argument("--status")
    p.add_argument("--path-prefix")
    p.add_argument("--limit", type=int, default=24, help="Maximum FTS candidates considered before packing.")
    p.add_argument("--broad", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-sync", action="store_true")
    trace_mode = p.add_mutually_exclusive_group()
    trace_mode.add_argument("--no-trace", action="store_true", help="Do not record a disposable retrieval trace.")
    trace_mode.add_argument(
        "--trace-query-text",
        action="store_true",
        help="Explicitly include raw task text in the otherwise metadata-only trace.",
    )
    p.add_argument(
        "--trace-retention-days",
        type=int,
        default=DEFAULT_TRACE_RETENTION_DAYS,
        help="Declared retention window for the disposable trace (default: 30).",
    )

    p = sub.add_parser("outline", help="Return the heading tree/section IDs for a document ID or relative path.")
    add_common(p); add_read_policy(p); p.add_argument("document"); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("read", help="Read one indexed section by numeric ID or stable section key.")
    add_common(p); add_read_policy(p); p.add_argument("section")

    p = sub.add_parser("related", help="Return explicit outgoing/incoming document links.")
    add_common(p); add_read_policy(p); p.add_argument("document"); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("stats", help="Show index counts and health summary.")
    add_common(p); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("status", help="Report read-only index readiness without refreshing derived state.")
    add_common(p)

    p = sub.add_parser("doctor", help="Report metadata/link/duplicate issues without editing ELM.")
    add_common(p); p.add_argument("--limit", type=int, default=100); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("root-id", help="Inspect or explicitly initialize the immutable ELM root identity.")
    root_sub = p.add_subparsers(dest="root_id_command", required=True)
    initialize = root_sub.add_parser("init", help="Preview or create 00_registry/ELM_ROOT_ID.json.")
    add_common(initialize)
    identity_mode = initialize.add_mutually_exclusive_group(required=True)
    identity_mode.add_argument("--dry-run", action="store_true")
    identity_mode.add_argument("--apply", action="store_true")
    initialize.add_argument("--creator", required=True, help="Non-authenticating bootstrap provenance label.")

    p = sub.add_parser("evidence", help="Manage reference-only governed evidence metadata.")
    evidence_sub = p.add_subparsers(dest="evidence_command", required=True)
    add = evidence_sub.add_parser("add", help="Create one immutable reference-only evidence record.")
    add_governance_actor(add)
    add.add_argument("--project", required=True)
    add.add_argument("--kind", required=True, choices=tuple(sorted(EVIDENCE_KINDS)))
    add.add_argument("--source-uri", required=True)
    add.add_argument("--content-sha256", required=True)
    add.add_argument("--excerpt-sha256")
    add.add_argument("--captured-at")
    add.add_argument("--sensitivity", choices=tuple(sorted(SENSITIVITIES)), default="normal")

    p = sub.add_parser("propose", help="Create one immutable governed-memory proposal.")
    add_governance_actor(p)
    p.add_argument("--project", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--predicate", required=True)
    p.add_argument("--object", required=True)
    p.add_argument("--requested-authority", choices=tuple(sorted(PROPOSAL_AUTHORITIES)), default="agent_proposal")
    p.add_argument("--valid-from")
    p.add_argument("--sensitivity", choices=tuple(sorted(SENSITIVITIES)), default="normal")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--source-ref", action="append", default=[])
    p.add_argument("--rationale", default="")

    p = sub.add_parser(
        "proposal-submit",
        help="Submit one closed Phase 5A proposal bundle as UTF-8 JSON on stdin.",
    )
    add_common(p)
    p.add_argument("--request-stdin", action="store_true", required=True)
    p.add_argument("--allow-project", action="append", required=True)
    p.add_argument("--max-request-bytes", type=int, default=65_536)
    p.add_argument("--max-reference-count", type=int, default=16)
    p.add_argument("--max-pending-per-project", type=int, default=256)
    p.add_argument("--max-pending-records-root", type=int, default=2_048)
    p.add_argument("--max-pending-bytes-per-project", type=int, default=4 * 1024 * 1024)
    p.add_argument("--max-pending-bytes-root", type=int, default=32 * 1024 * 1024)

    p = sub.add_parser("proposals", help="Inspect the immutable proposal queue and derived status.")
    proposal_sub = p.add_subparsers(dest="proposals_command", required=True)
    listing = proposal_sub.add_parser("list", help="List proposals under optional project/status filters.")
    add_common(listing)
    listing.add_argument("--project")
    listing.add_argument("--status", choices=("pending", "accepted", "rejected", "deferred"))
    listing.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("proposal-preview", help="Build a non-signable Phase 5A transition review plan.")
    add_common(p)
    p.add_argument("proposal_id")
    p.add_argument("--project", required=True)
    p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("accept", help="Ratify one pending proposal into canonical claim Markdown.")
    add_governance_actor(p)
    p.add_argument("proposal_id")
    p.add_argument("--authority", required=True, choices=tuple(sorted(ACCEPTED_AUTHORITIES)))

    for command in ("reject", "defer"):
        p = sub.add_parser(command, help=f"{command.capitalize()} one pending proposal with a reason code.")
        add_governance_actor(p)
        p.add_argument("proposal_id")
        p.add_argument("--reason-code", required=True, choices=tuple(sorted(REASON_CODES)))

    p = sub.add_parser("dispute", help="Mark one accepted claim disputed without erasing history.")
    add_governance_actor(p)
    p.add_argument("claim_id")
    p.add_argument("--reason-code", required=True, choices=tuple(sorted(REASON_CODES)))

    p = sub.add_parser("supersede", help="Ratify a pending proposal as the successor to one accepted claim.")
    add_governance_actor(p)
    p.add_argument("claim_id")
    p.add_argument("proposal_id")
    p.add_argument("--authority", required=True, choices=tuple(sorted(ACCEPTED_AUTHORITIES)))

    p = sub.add_parser("delete", help="Delete one governed item and retain a metadata-only tombstone.")
    add_governance_actor(p)
    p.add_argument("item_id")
    p.add_argument("--reason-code", required=True, choices=tuple(sorted(REASON_CODES)))

    p = sub.add_parser("history", help="Query canonical claim history, lifecycle events, and contradictions.")
    add_common(p)
    p.add_argument("--project")
    p.add_argument("--subject")
    p.add_argument("--predicate")
    p.add_argument("--valid-at")
    p.add_argument("--recorded-at")
    p.add_argument("--include-deleted", action="store_true")
    p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("recover", help="Preview or roll back incomplete canonical governance transactions.")
    add_common(p)
    recovery_mode = p.add_mutually_exclusive_group(required=True)
    recovery_mode.add_argument("--dry-run", action="store_true")
    recovery_mode.add_argument("--apply", action="store_true")

    p = sub.add_parser("ids", help="Manage explicit durable public identities.")
    ids_sub = p.add_subparsers(dest="ids_command", required=True)
    assign = ids_sub.add_parser("assign", help="Explicitly assign document UUIDs with backups and rollback.")
    add_common(assign)
    mode = assign.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    assign.add_argument("--path-prefix")
    assign.add_argument("--include-archive", action="store_true")

    p = sub.add_parser("traces", help="Manage disposable privacy-minimized retrieval traces.")
    traces_sub = p.add_subparsers(dest="traces_command", required=True)
    cleanup = traces_sub.add_parser("cleanup", help="Preview or remove expired retrieval traces.")
    add_common(cleanup)
    cleanup.add_argument(
        "--retention-days",
        type=int,
        help="Override per-trace expiry using age in days from recorded_at.",
    )
    cleanup_mode = cleanup.add_mutually_exclusive_group(required=True)
    cleanup_mode.add_argument("--dry-run", action="store_true")
    cleanup_mode.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args()
    root = resolve_root(getattr(args, "root", None))
    if not root.is_dir():
        raise SystemExit(f"ELM root is not a directory: {root}")
    if args.command == "status":
        command_status(args, root)
        return 0
    if args.command == "root-id":
        try:
            if args.root_id_command == "init":
                command_root_id(args, root)
                return 0
            parser.error("unknown root-id command")
        except WriterLockError as exc:
            emit_error("writer_lock_unavailable", str(exc), args.json, exc.record)
            return 2
        except GovernanceError as exc:
            emit_error("governance_failed", str(exc), args.json)
            return 2
    if args.command == "ids":
        try:
            if args.ids_command == "assign":
                command_ids_assign(args, root)
                return 0
            parser.error("unknown ids command")
        except WriterLockError as exc:
            emit_error("writer_lock_unavailable", str(exc), args.json, exc.record)
            return 2
        except (ValueError, RuntimeError) as exc:
            emit_error("ids_assign_failed", str(exc), args.json)
            return 2
    if args.command == "traces":
        try:
            if args.traces_command == "cleanup":
                command_traces_cleanup(args, root)
                return 0
            parser.error("unknown traces command")
        except WriterLockError as exc:
            emit_error("writer_lock_unavailable", str(exc), args.json, exc.record)
            return 2
        except ValueError as exc:
            emit_error("trace_cleanup_failed", str(exc), args.json)
            return 2

    try:
        if args.command == "read" or bool(getattr(args, "no_sync", False)):
            con = connect_readonly(root, lock_timeout=args.lock_timeout)
        else:
            con = connect(
                root,
                lock_timeout=args.lock_timeout,
                recover_stale=args.recover_stale_lock,
            )
    except ReadOnlyIndexError as exc:
        emit_error("read_only_index_not_ready", str(exc), args.json)
        return 2
    except WriterLockError as exc:
        emit_error("writer_lock_unavailable", str(exc), args.json, exc.record)
        return 2
    except UnsupportedSchemaError as exc:
        emit_error("unsupported_index_schema", str(exc), args.json)
        return 2
    except SchemaMigrationError as exc:
        emit_error("index_schema_migration_failed", str(exc), args.json)
        return 2
    try:
        if args.command == "sync":
            emit(_sync_from_args(args, con, root, force=args.force), args.json)
        elif args.command == "rebuild":
            command_rebuild(args, con, root); return 0
        elif args.command == "search":
            command_search(args, con, root)
        elif args.command == "context":
            command_context(args, con, root)
        elif args.command == "outline":
            command_outline(args, con, root)
        elif args.command == "read":
            command_read(args, con, root)
        elif args.command == "related":
            command_related(args, con, root)
        elif args.command == "stats":
            command_stats(args, con, root)
        elif args.command == "doctor":
            command_doctor(args, con, root)
        elif args.command == "evidence" and args.evidence_command == "add":
            command_evidence_add(args, con, root)
        elif args.command == "propose":
            command_propose(args, con, root)
        elif args.command == "proposal-submit":
            command_proposal_submit(args, con, root)
        elif args.command == "proposals" and args.proposals_command == "list":
            command_proposals_list(args, con, root)
        elif args.command == "proposal-preview":
            command_proposal_preview(args, con, root)
        elif args.command == "accept":
            command_accept(args, con, root)
        elif args.command == "reject":
            command_reject_or_defer(args, con, root, "reject")
        elif args.command == "defer":
            command_reject_or_defer(args, con, root, "defer")
        elif args.command == "dispute":
            command_dispute(args, con, root)
        elif args.command == "supersede":
            command_supersede(args, con, root)
        elif args.command == "delete":
            command_delete(args, con, root)
        elif args.command == "history":
            command_history(args, con, root)
        elif args.command == "recover":
            command_recover(args, con, root)
        else:
            parser.error("unknown command")
    except WriterLockError as exc:
        emit_error("writer_lock_unavailable", str(exc), args.json, exc.record)
        return 2
    except (ValueError, GovernanceError) as exc:
        code = "governance_failed" if isinstance(exc, GovernanceError) else "invalid_argument"
        emit_error(code, str(exc), args.json)
        return 2
    finally:
        try:
            con.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
