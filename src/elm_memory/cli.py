#!/usr/bin/env python3
"""Deterministic search and progressive-reading layer for External Local Memory.

The filesystem remains the source of truth. SQLite is a disposable, rebuildable index.
Uses only the Python standard library and SQLite FTS5.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

CONFIG_POINTER = Path.home() / ".elm-system" / "root"
META_KEYS = {
    "title": "title",
    "scope": "scope",
    "tags": "tags",
    "related files": "related_files",
    "last updated": "last_updated",
    "status": "status",
    "summary": "summary",
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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def connect(root: Path) -> sqlite3.Connection:
    p = db_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_schema(con)
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            area TEXT,
            project TEXT,
            title TEXT,
            scope TEXT,
            summary TEXT,
            status TEXT,
            last_updated TEXT,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            is_archive INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
            heading TEXT,
            heading_path TEXT,
            level INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            token_estimate INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sections_document ON sections(document_id, ordinal);

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS document_tags (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY(document_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS properties (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY(document_id, key)
        );

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            target_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            target_path TEXT NOT NULL,
            anchor_text TEXT,
            relation_type TEXT NOT NULL DEFAULT 'markdown_link'
        );
        CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_document_id);
        CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_document_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
            section_id UNINDEXED,
            document_id UNINDEXED,
            title,
            heading,
            body,
            tags,
            scope,
            summary,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )


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


def estimate_tokens(text: str) -> int:
    # Deliberately model-agnostic. Good enough for retrieval budgeting.
    return max(1, (len(text) + 3) // 4)


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
    old = con.execute("SELECT id, content_hash FROM documents WHERE path=?", (relative,)).fetchone()
    if old and old["content_hash"] == digest and not force:
        return False, int(old["id"])

    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    meta, metadata_end = parse_metadata(lines)
    title = meta.get("title") or path.stem
    tags = split_csv(meta.get("tags"))
    area, project = derive_area_project(relative)
    archive = 1 if is_archive_path(relative) else 0

    if old:
        doc_id = int(old["id"])
        con.execute(
            """UPDATE documents SET area=?, project=?, title=?, scope=?, summary=?, status=?,
               last_updated=?, mtime_ns=?, size_bytes=?, content_hash=?, is_archive=?, indexed_at=? WHERE id=?""",
            (area, project, title, meta.get("scope"), meta.get("summary"), meta.get("status"),
             meta.get("last_updated"), st.st_mtime_ns, st.st_size, digest, archive, now_iso(), doc_id),
        )
        con.execute("DELETE FROM sections_fts WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM sections WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM properties WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM links WHERE source_document_id=?", (doc_id,))
    else:
        cur = con.execute(
            """INSERT INTO documents(path, area, project, title, scope, summary, status, last_updated,
               mtime_ns, size_bytes, content_hash, is_archive, indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (relative, area, project, title, meta.get("scope"), meta.get("summary"), meta.get("status"),
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
    for ordinal, sec in enumerate(parsed):
        parent_id = ordinal_to_id.get(sec["parent_ordinal"]) if sec["parent_ordinal"] is not None else None
        cur = con.execute(
            """INSERT INTO sections(document_id,parent_id,heading,heading_path,level,ordinal,start_line,end_line,token_estimate,text)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, parent_id, sec["heading"], sec["heading_path"], sec["level"], ordinal,
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


def sync(con: sqlite3.Connection, root: Path, force: bool = False) -> dict:
    files = list(iter_markdown(root))
    current = {relpath(root, p) for p in files}
    existing = {r["path"]: r["id"] for r in con.execute("SELECT id,path FROM documents")}
    removed = [p for p in existing if p not in current]
    for p in removed:
        doc_id = existing[p]
        con.execute("DELETE FROM sections_fts WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    changed = 0
    unchanged = 0
    errors: list[dict] = []
    for path in files:
        try:
            did_change, _ = index_one(con, root, path, force=force)
            changed += int(did_change)
            unchanged += int(not did_change)
        except Exception as exc:  # keep the rest of the vault indexable
            errors.append({"path": relpath(root, path), "error": str(exc)})
    resolve_link_targets(con)
    con.commit()
    return {
        "root": str(root),
        "database": str(db_path(root)),
        "files_seen": len(files),
        "changed": changed,
        "unchanged": unchanged,
        "removed": len(removed),
        "errors": errors,
    }


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


def command_search(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        sync(con, root)
    fts = safe_fts_query(args.query, broad=args.broad)
    conditions = ["sections_fts MATCH ?"]
    params: list[object] = [fts]
    if not args.include_archive:
        conditions.append("d.is_archive=0")
    if args.project:
        conditions.append("d.project=?")
        params.append(args.project)
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
        SELECT d.id AS document_id, s.id AS section_id, d.path, d.title, d.project, d.status,
               d.last_updated, d.is_archive, s.heading, s.heading_path, s.start_line, s.end_line,
               s.token_estimate, -bm25(sections_fts, 0.0,0.0,3.0,2.0,1.0,1.5,1.0,1.0) AS score,
               snippet(sections_fts, 4, '[', ']', ' ... ', 28) AS snippet
        FROM sections_fts
        JOIN sections s ON s.id=CAST(sections_fts.section_id AS INTEGER)
        JOIN documents d ON d.id=s.document_id
        WHERE {' AND '.join(conditions)}
        ORDER BY score DESC, d.last_updated DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = con.execute(sql, params).fetchall()
    results = []
    for r in rows:
        item = dict(r)
        item["is_archive"] = bool(item["is_archive"])
        item["tags"] = fetch_tags(con, int(item["document_id"]))
        results.append(item)
    emit({"query": args.query, "count": len(results), "results": results}, args.json)


def resolve_document(con: sqlite3.Connection, ref: str) -> sqlite3.Row:
    row = None
    if ref.isdigit():
        row = con.execute("SELECT * FROM documents WHERE id=?", (int(ref),)).fetchone()
    if row is None:
        row = con.execute("SELECT * FROM documents WHERE path=?", (ref.replace("\\", "/"),)).fetchone()
    if row is None:
        raise SystemExit(f"Document not found: {ref}")
    return row


def command_outline(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        sync(con, root)
    doc = resolve_document(con, args.document)
    rows = con.execute(
        "SELECT id,parent_id,heading,heading_path,level,ordinal,start_line,end_line,token_estimate FROM sections WHERE document_id=? ORDER BY ordinal",
        (doc["id"],),
    ).fetchall()
    emit({"document": dict(doc), "sections": [dict(r) for r in rows]}, args.json)


def command_read(args, con: sqlite3.Connection, root: Path):
    row = con.execute(
        """SELECT s.*, d.path, d.title, d.project, d.status, d.last_updated
           FROM sections s JOIN documents d ON d.id=s.document_id WHERE s.id=?""",
        (args.section_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"Section not found: {args.section_id}. Run search/outline again after syncing.")
    out = dict(row)
    emit(out, args.json)


def command_related(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        sync(con, root)
    doc = resolve_document(con, args.document)
    outgoing = [dict(r) for r in con.execute(
        """SELECT l.relation_type,l.anchor_text,l.target_path,d.id AS target_id,d.title AS target_title,d.status AS target_status
           FROM links l LEFT JOIN documents d ON d.id=l.target_document_id
           WHERE l.source_document_id=? ORDER BY l.relation_type,l.target_path""", (doc["id"],)
    )]
    incoming = [dict(r) for r in con.execute(
        """SELECT l.relation_type,l.anchor_text,s.id AS source_id,s.path AS source_path,s.title AS source_title,s.status AS source_status
           FROM links l JOIN documents s ON s.id=l.source_document_id
           WHERE l.target_document_id=? ORDER BY s.path""", (doc["id"],)
    )]
    emit({"document": dict(doc), "outgoing": outgoing, "incoming": incoming}, args.json)


def command_stats(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        sync(con, root)
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
        "database": str(db_path(root)),
    })
    emit(out, args.json)


def command_doctor(args, con: sqlite3.Connection, root: Path):
    if not args.no_sync:
        sync(con, root)
    issues: list[dict] = []
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
           GROUP BY coalesce(project,area,''),lower(title) HAVING COUNT(*)>1 ORDER BY c DESC"""
    ):
        issues.append({"kind": "duplicate_title", "scope": r["scope_key"], "title": r["title"], "count": r["c"], "paths": r["paths"]})
    emit({"issue_count": len(issues), "issues": issues[:args.limit], "truncated": len(issues) > args.limit}, args.json)


def command_rebuild(args, con: sqlite3.Connection, root: Path):
    con.close()
    p = db_path(root)
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(p) + suffix).unlink()
        except FileNotFoundError:
            pass
    con2 = connect(root)
    result = sync(con2, root, force=True)
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


def add_common(p):
    p.add_argument("--root", help="ELM root. Defaults to ELM_ROOT or the known Windows ELM path.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


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
    p.add_argument("--project")
    p.add_argument("--status")
    p.add_argument("--path-prefix")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--include-archive", action="store_true")
    p.add_argument("--broad", action="store_true", help="Use OR between query terms for recall-heavy fallback search.")
    p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("outline", help="Return the heading tree/section IDs for a document ID or relative path.")
    add_common(p); p.add_argument("document"); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("read", help="Read one indexed section by section ID.")
    add_common(p); p.add_argument("section_id", type=int)

    p = sub.add_parser("related", help="Return explicit outgoing/incoming document links.")
    add_common(p); p.add_argument("document"); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("stats", help="Show index counts and health summary.")
    add_common(p); p.add_argument("--no-sync", action="store_true")

    p = sub.add_parser("doctor", help="Report metadata/link/duplicate issues without editing ELM.")
    add_common(p); p.add_argument("--limit", type=int, default=100); p.add_argument("--no-sync", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = resolve_root(getattr(args, "root", None))
    if not root.is_dir():
        raise SystemExit(f"ELM root is not a directory: {root}")
    con = connect(root)
    try:
        if args.command == "sync":
            emit(sync(con, root, force=args.force), args.json)
        elif args.command == "rebuild":
            command_rebuild(args, con, root); return 0
        elif args.command == "search":
            command_search(args, con, root)
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
        else:
            parser.error("unknown command")
    finally:
        try:
            con.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
