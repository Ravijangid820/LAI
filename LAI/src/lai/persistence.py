"""SQLite-backed persistence for the contract-review backend.

Replaces the in-memory ``STATE["sessions"]`` dict in serve_rag.py.

Design notes
------------
- Two tables: ``sessions`` (one row per upload) and ``messages`` (chat
  history). Heavy fields (tables_json, analysis_json, etc.) are JSON
  blobs — we don't query into them today; when we do, migrating to
  PostgreSQL+JSONB is straightforward.
- Original PDF/DOCX files are written to ``processed/uploads/<sid><ext>``
  so we can re-OCR or re-render. Disk-only for now; can move to MinIO.
- ``user_id`` reserved on every row for future auth; nullable today.
- Connection is process-scoped, ``check_same_thread=False`` so the
  FastAPI worker threadpool can share it. Uses ``immediate`` BEGIN
  semantics; SQLite's row-level WAL is sufficient for a single FastAPI
  process — no separate write coordinator needed.

Public API
----------
``init(db_path, uploads_dir)`` once at startup.
``load_session(sid)`` / ``save_session(sid, data)`` / ``delete_session(sid)``
``list_sessions(limit)`` / ``count_sessions()``
``add_message(sid, role, content, mode)`` / ``list_messages(sid)``
``save_upload(sid, content, filename)`` / ``upload_path(sid)``
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Optional


_STATE: dict = {
    "conn": None,
    "uploads_dir": None,
    "lock": Lock(),
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT,
    title                    TEXT,
    filename                 TEXT,
    contract_text            TEXT,
    n_pages                  INTEGER,
    tables_json              TEXT,
    clauses_json             TEXT,
    analysis_json            TEXT,
    extraction_quality_json  TEXT,
    upload_ext               TEXT,
    -- Pinned conversational context: stable user/project facts extracted
    -- from the first few turns and refreshed periodically. Survives the
    -- 32-msg rolling window so long sessions don't lose T1 facts like
    -- "user is Anika at Nordlicht Wind, evaluating Lamstedt".
    session_meta_json        TEXT,
    created_at               REAL NOT NULL,
    updated_at               REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON sessions(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,        -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    mode        TEXT,                  -- 'chat' | 'rag' | 'rag+contract' | 'contract'
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);

-- Matter documents: a session ("matter") can hold MANY uploaded
-- documents, each addressable as a [M-n] citation handle in chat.
-- ``doc_index`` is the stable 1-based n in [M-n] — assigned at upload
-- time in ascending id order so the handle for a document never shifts
-- when another is added. The first upload also mirrors into
-- ``sessions.contract_text`` (see upload()) so the single-document
-- analyze-contract path and older clients keep working unchanged.
CREATE TABLE IF NOT EXISTS matter_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    doc_index   INTEGER NOT NULL,      -- the n in [M-n], 1-based, stable
    filename    TEXT,
    doc_text    TEXT,                  -- extracted markdown/text
    n_pages     INTEGER,
    upload_ext  TEXT,                  -- ".pdf" / ".docx" — file on disk
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_matter_docs_session
    ON matter_documents(session_id, doc_index);

-- Lawyer-supplied feedback on an assistant turn. One row per
-- (user_id, session_id, message_id) — re-submitting from the UI
-- overwrites via INSERT OR REPLACE so toggling thumbs-up → thumbs-down
-- collapses to a single most-recent verdict. ``message_id`` is COALESCEd
-- to 0 in the unique key so session-level feedback (no specific bubble)
-- still benefits from the upsert. ``rating`` is constrained to -1 / +1
-- by the route handler; the column itself stays unconstrained so we
-- can add e.g. star ratings later without a destructive migration.
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    message_id  INTEGER,
    user_id     TEXT NOT NULL,
    rating      INTEGER NOT NULL,
    reason      TEXT,
    comment     TEXT,
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_user_session_msg
    ON feedback(user_id, session_id, COALESCE(message_id, 0));

CREATE INDEX IF NOT EXISTS idx_feedback_session
    ON feedback(session_id, created_at DESC);
"""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def init(db_path: Path, uploads_dir: Path) -> None:
    """Open (or create) the SQLite database and uploads directory."""
    db_path = Path(db_path)
    uploads_dir = Path(uploads_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Forward-compat migration — older DBs predate these columns.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "title" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
    if "session_meta_json" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_meta_json TEXT")

    # The feedback table + its unique index were added late; older DBs
    # predate the unique index even when they have the table. Re-running
    # the relevant CREATE statements is cheap and idempotent.
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_user_session_msg
            ON feedback(user_id, session_id, COALESCE(message_id, 0))
        """
    )
    # matter_documents was added with the Matter-workspace feature; the
    # executescript above creates it on fresh DBs, this guards older ones.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_matter_docs_session
            ON matter_documents(session_id, doc_index)
        """
    )

    _STATE["conn"] = conn
    _STATE["uploads_dir"] = uploads_dir


def _conn() -> sqlite3.Connection:
    c = _STATE["conn"]
    if c is None:
        raise RuntimeError("persistence.init() not called")
    return c


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row_to_session(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "title": r["title"] if "title" in r.keys() else None,
        "filename": r["filename"],
        "contract_text": r["contract_text"],
        "n_pages": r["n_pages"] or 0,
        "tables": json.loads(r["tables_json"] or "[]"),
        "clauses": json.loads(r["clauses_json"]) if r["clauses_json"] else None,
        "analysis": json.loads(r["analysis_json"]) if r["analysis_json"] else None,
        "extraction_quality": (
            json.loads(r["extraction_quality_json"])
            if r["extraction_quality_json"] else None
        ),
        "upload_ext": r["upload_ext"],
        "uploaded_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def _display_title_sql_expr() -> str:
    """SQL fragment that picks the best available title for a session:
    user-set title → uploaded filename → first user message (truncated)
    → ``Untitled chat``. Used in list_sessions so the sidebar always
    shows something useful even when nothing was set explicitly."""
    return (
        "COALESCE("
        "NULLIF(TRIM(sessions.title), ''),"
        "NULLIF(TRIM(sessions.filename), ''),"
        "(SELECT SUBSTR(content, 1, 80) FROM messages "
        " WHERE messages.session_id = sessions.id AND role = 'user' "
        " ORDER BY created_at ASC, id ASC LIMIT 1),"
        "'Untitled chat'"
        ")"
    )


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def load_session(sid: str, user_id: Optional[str] = None) -> Optional[dict]:
    """Load a session by id, optionally constrained to a user.

    When ``user_id`` is supplied the lookup filters on ownership; a
    miss returns ``None`` so callers map to 404 (AUTH_PLAN §6 rule 4 —
    never leak existence of another tenant's row).
    """
    if user_id is None:
        r = _conn().execute(
            "SELECT * FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    else:
        r = _conn().execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (sid, user_id),
        ).fetchone()
    return _row_to_session(r) if r else None


def save_session(sid: str, data: dict) -> None:
    """Upsert a session. Caller passes the same dict shape that was
    historically stored under ``STATE["sessions"][sid]``. Note: ``title``
    is preserved on upsert when not explicitly set in ``data`` — it's a
    user-controlled field, not derived from upload payloads."""
    now = time.time()
    with _STATE["lock"]:
        _conn().execute(
            """
            INSERT INTO sessions (
                id, user_id, title, filename, contract_text, n_pages,
                tables_json, clauses_json, analysis_json, extraction_quality_json,
                upload_ext, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id                 = COALESCE(excluded.user_id, sessions.user_id),
                title                   = COALESCE(excluded.title, sessions.title),
                filename                = excluded.filename,
                contract_text           = excluded.contract_text,
                n_pages                 = excluded.n_pages,
                tables_json             = excluded.tables_json,
                clauses_json            = excluded.clauses_json,
                analysis_json           = excluded.analysis_json,
                extraction_quality_json = excluded.extraction_quality_json,
                upload_ext              = COALESCE(excluded.upload_ext, sessions.upload_ext),
                updated_at              = excluded.updated_at
            """,
            (
                sid,
                data.get("user_id"),
                data.get("title"),
                data.get("filename"),
                data.get("contract_text"),
                int(data.get("n_pages") or 0),
                json.dumps(data.get("tables") or [], ensure_ascii=False),
                json.dumps(data["clauses"], ensure_ascii=False) if data.get("clauses") is not None else None,
                json.dumps(data["analysis"], ensure_ascii=False) if data.get("analysis") is not None else None,
                json.dumps(data["extraction_quality"], ensure_ascii=False) if data.get("extraction_quality") is not None else None,
                data.get("upload_ext"),
                data.get("uploaded_at") or now,
                now,
            ),
        )


def update_session_title(sid: str, title: str, user_id: Optional[str] = None) -> bool:
    """Set the user-facing title for a session. Pass an empty string to
    clear (which falls back to the COALESCE chain in list_sessions).
    Returns True if a row was updated.

    When ``user_id`` is supplied the UPDATE is scoped so a foreign
    caller cannot mutate another tenant's row (AUTH_PLAN G2).
    """
    cleaned = (title or "").strip()
    with _STATE["lock"]:
        if user_id is None:
            cur = _conn().execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned or None, time.time(), sid),
            )
        else:
            cur = _conn().execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (cleaned or None, time.time(), sid, user_id),
            )
        return cur.rowcount > 0


def delete_session(sid: str, user_id: Optional[str] = None) -> bool:
    """Delete a session and its messages.

    Returns ``True`` when a session row was actually deleted (caller
    maps a ``False`` return to 404 — never 403 — to avoid leaking
    existence).
    """
    with _STATE["lock"]:
        if user_id is None:
            cur = _conn().execute("DELETE FROM sessions WHERE id = ?", (sid,))
        else:
            cur = _conn().execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?", (sid, user_id),
            )
        deleted = cur.rowcount > 0
        if deleted:
            # Foreign-keys ON CASCADE was added to the schema, but
            # historical DBs may predate that; clean up messages
            # explicitly to keep the contract identical across schemas.
            _conn().execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        return deleted


def session_exists(sid: str, user_id: Optional[str] = None) -> bool:
    if user_id is None:
        r = _conn().execute(
            "SELECT 1 FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    else:
        r = _conn().execute(
            "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?", (sid, user_id),
        ).fetchone()
    return r is not None


def list_sessions(limit: int = 50, user_id: Optional[str] = None) -> list[dict]:
    """Light-weight list (no contract_text/analysis blobs) for UI sidebars."""
    title_expr = _display_title_sql_expr()
    base_select = f"""
        SELECT id,
               title AS user_title,
               {title_expr} AS title,
               filename, n_pages, created_at, updated_at,
               (clauses_json IS NOT NULL) AS has_analysis,
               (SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) AS n_messages
        FROM sessions
    """
    if user_id is None:
        rows = _conn().execute(
            base_select + " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = _conn().execute(
            base_select + " WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],          # always non-null (COALESCE chain)
            "user_title": r["user_title"],  # what the user explicitly set, or null
            "filename": r["filename"],
            "n_pages": r["n_pages"] or 0,
            "uploaded_at": r["created_at"],
            "updated_at": r["updated_at"],
            "has_analysis": bool(r["has_analysis"]),
            "n_messages": int(r["n_messages"]),
        }
        for r in rows
    ]


def count_sessions() -> int:
    r = _conn().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    return int(r["n"])


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

def add_message(
    session_id: str,
    role: str,
    content: str,
    mode: Optional[str] = None,
    user_id: Optional[str] = None,
) -> int:
    """Append a message. Returns the new row id (or 0 on insert failure).

    When ``user_id`` is provided we verify ownership before writing —
    a foreign caller cannot inject messages into someone else's chat
    history (AUTH_PLAN G3).
    """
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return 0
    with _STATE["lock"]:
        cur = _conn().execute(
            """
            INSERT INTO messages (session_id, role, content, mode, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, mode, time.time()),
        )
        return int(cur.lastrowid or 0)


def list_messages(session_id: str, user_id: Optional[str] = None) -> list[dict]:
    """Return all messages for a session.

    When ``user_id`` is supplied we verify the session belongs to that
    user first — returns an empty list otherwise. Combined with the
    endpoint's existence check, this enforces "no cross-tenant message
    reads" without leaking via response shape.
    """
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return []
    rows = _conn().execute(
        """
        SELECT id, role, content, mode, created_at
        FROM messages
        WHERE session_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "mode": r["mode"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_session_meta(session_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    """Pinned conversational context (user name, project, key dates, ...)
    extracted by the LLM and saved alongside the session. None when the
    column is empty (brand-new session or extraction never ran).

    Filters by ``user_id`` when supplied so meta blobs do not leak
    between tenants.
    """
    if user_id is None:
        row = _conn().execute(
            "SELECT session_meta_json FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    else:
        row = _conn().execute(
            "SELECT session_meta_json FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    if not row or not row["session_meta_json"]:
        return None
    try:
        return json.loads(row["session_meta_json"])
    except json.JSONDecodeError:
        return None


def set_session_meta(session_id: str, meta: dict, user_id: Optional[str] = None) -> None:
    """Persist a refreshed metadata snapshot for the session."""
    with _STATE["lock"]:
        if user_id is None:
            _conn().execute(
                "UPDATE sessions SET session_meta_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), time.time(), session_id),
            )
        else:
            _conn().execute(
                "UPDATE sessions SET session_meta_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (json.dumps(meta, ensure_ascii=False), time.time(), session_id, user_id),
            )


# ---------------------------------------------------------------------------
# feedback (lawyer's thumbs-up/down on an assistant turn)
# ---------------------------------------------------------------------------

def record_feedback(
    *,
    session_id: str,
    user_id: str,
    rating: int,
    message_id: Optional[int] = None,
    reason: Optional[str] = None,
    comment: Optional[str] = None,
) -> Optional[int]:
    """Upsert a feedback row keyed by ``(user_id, session_id, message_id)``.

    Returns the row id on success, or ``None`` when the session does not
    belong to ``user_id`` — we treat cross-tenant feedback as silently
    dropped (matches the rest of persistence.py's pattern).

    The upsert uses ``ON CONFLICT ... DO UPDATE`` rather than ``INSERT OR
    REPLACE`` so the auto-incremented ``id`` is preserved across edits
    (REPLACE deletes and re-inserts, which makes ``id`` churn). That
    keeps any future audit trail / link-back-by-id stable.
    """
    if not session_exists(session_id, user_id=user_id):
        return None
    with _STATE["lock"]:
        cur = _conn().execute(
            """
            INSERT INTO feedback
                (session_id, message_id, user_id, rating, reason, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id, COALESCE(message_id, 0))
            DO UPDATE SET
                rating     = excluded.rating,
                reason     = excluded.reason,
                comment    = excluded.comment,
                created_at = excluded.created_at
            RETURNING id
            """,
            (session_id, message_id, user_id, rating, reason, comment, time.time()),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else None


def list_feedback(session_id: str, user_id: Optional[str] = None) -> list[dict]:
    """All feedback rows attached to a session.

    Filtered by ``user_id`` when supplied — matches the access model of
    ``list_messages``. Order is newest-first so the UI can show the
    lawyer their most-recent verdict at the top without re-sorting.
    """
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return []
    rows = _conn().execute(
        """
        SELECT id, session_id, message_id, user_id, rating, reason, comment, created_at
        FROM feedback
        WHERE session_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "message_id": r["message_id"],
            "user_id": r["user_id"],
            "rating": int(r["rating"]),
            "reason": r["reason"],
            "comment": r["comment"],
            "created_at": float(r["created_at"]),
        }
        for r in rows
    ]


def message_belongs_to_session(message_id: int, session_id: str) -> bool:
    """Cheap referential-integrity check used by the /feedback route.

    The unique index on feedback already prevents duplicate rows per
    (user, session, message), but it doesn't catch ``message_id`` values
    that point at a different session's message — that would silently
    record feedback against the wrong bubble. This guard is the cheap
    fix.
    """
    row = _conn().execute(
        "SELECT 1 FROM messages WHERE id = ? AND session_id = ?",
        (message_id, session_id),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# matter documents (multiple uploads per session = a "Matter")
# ---------------------------------------------------------------------------

def add_matter_document(
    session_id: str,
    *,
    filename: str,
    doc_text: str,
    n_pages: int,
    upload_ext: str,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """Append one uploaded document to a matter. Returns the new row as a
    dict (incl. its stable ``doc_index`` = the n in [M-n]), or ``None`` if
    the session isn't owned by ``user_id``.

    ``doc_index`` is ``max(existing)+1`` so handles never shift when more
    documents are added later.
    """
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return None
    with _STATE["lock"]:
        row = _conn().execute(
            "SELECT COALESCE(MAX(doc_index), 0) AS mx FROM matter_documents WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        next_index = int(row["mx"]) + 1
        ts = time.time()
        cur = _conn().execute(
            """
            INSERT INTO matter_documents
                (session_id, doc_index, filename, doc_text, n_pages, upload_ext, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, next_index, filename, doc_text, n_pages, upload_ext, ts),
        )
        return {
            "id": int(cur.lastrowid or 0),
            "session_id": session_id,
            "doc_index": next_index,
            "filename": filename,
            "n_pages": n_pages,
            "upload_ext": upload_ext,
            "created_at": ts,
        }


def list_matter_documents(
    session_id: str, user_id: Optional[str] = None, *, include_text: bool = False,
) -> list[dict]:
    """All documents attached to a matter, ordered by ``doc_index`` (= [M-n]).

    ``include_text=False`` (default) omits the heavy ``doc_text`` blob —
    the UI document list doesn't need it. The chat path passes
    ``include_text=True`` to build the [M-n] prompt sources.
    Filtered by ``user_id`` when supplied (no cross-tenant leakage).
    """
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return []
    cols = "id, doc_index, filename, n_pages, upload_ext, created_at"
    if include_text:
        cols += ", doc_text"
    rows = _conn().execute(
        f"SELECT {cols} FROM matter_documents WHERE session_id = ? ORDER BY doc_index ASC",
        (session_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = {
            "id": int(r["id"]),
            "doc_index": int(r["doc_index"]),
            "filename": r["filename"],
            "n_pages": int(r["n_pages"]) if r["n_pages"] is not None else 0,
            "upload_ext": r["upload_ext"],
            "created_at": float(r["created_at"]),
        }
        if include_text:
            d["doc_text"] = r["doc_text"] or ""
        out.append(d)
    return out


def get_matter_document(
    session_id: str, doc_index: int, user_id: Optional[str] = None,
) -> Optional[dict]:
    """One matter document by its [M-n] index, or None. Used by the
    per-document preview endpoint."""
    if user_id is not None and not session_exists(session_id, user_id=user_id):
        return None
    r = _conn().execute(
        "SELECT id, doc_index, filename, n_pages, upload_ext, created_at "
        "FROM matter_documents WHERE session_id = ? AND doc_index = ?",
        (session_id, doc_index),
    ).fetchone()
    if not r:
        return None
    return {
        "id": int(r["id"]),
        "doc_index": int(r["doc_index"]),
        "filename": r["filename"],
        "n_pages": int(r["n_pages"]) if r["n_pages"] is not None else 0,
        "upload_ext": r["upload_ext"],
        "created_at": float(r["created_at"]),
    }


def matter_document_path(session_id: str, doc_id: int, ext: Optional[str] = None) -> Optional[Path]:
    """Disk path of a matter document's original file. Files are stored as
    ``<session_id>_m<doc_id><ext>`` to keep many docs per session distinct."""
    base = Path(_STATE["uploads_dir"])
    if ext:
        p = base / f"{session_id}_m{doc_id}{ext}"
        return p if p.exists() else None
    for candidate in (".pdf", ".docx", ".doc", ".txt", ".md", ".bin"):
        p = base / f"{session_id}_m{doc_id}{candidate}"
        if p.exists():
            return p
    return None


def save_matter_upload(session_id: str, doc_id: int, content: bytes, filename: str) -> str:
    """Write a matter document's bytes to ``<session_id>_m<doc_id><ext>``.
    Returns the extension stored."""
    suffix = Path(filename).suffix.lower() or ".bin"
    target = Path(_STATE["uploads_dir"]) / f"{session_id}_m{doc_id}{suffix}"
    target.write_bytes(content)
    return suffix


# ---------------------------------------------------------------------------
# uploads (file blobs on disk)
# ---------------------------------------------------------------------------

def save_upload(sid: str, content: bytes, filename: str) -> str:
    """Write the raw upload to disk and return the extension stored. Cheap
    audit/re-OCR backstop. The session row tracks the extension so we
    know how to find it back."""
    suffix = Path(filename).suffix.lower() or ".bin"
    target = Path(_STATE["uploads_dir"]) / f"{sid}{suffix}"
    target.write_bytes(content)
    return suffix


def upload_path(sid: str, ext: Optional[str] = None) -> Optional[Path]:
    base = Path(_STATE["uploads_dir"])
    if ext:
        p = base / f"{sid}{ext}"
        return p if p.exists() else None
    # No ext known — try common ones
    for candidate in (".pdf", ".docx", ".doc", ".txt", ".md", ".bin"):
        p = base / f"{sid}{candidate}"
        if p.exists():
            return p
    return None
