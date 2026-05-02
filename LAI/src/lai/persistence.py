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

def load_session(sid: str) -> Optional[dict]:
    r = _conn().execute(
        "SELECT * FROM sessions WHERE id = ?", (sid,)
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


def update_session_title(sid: str, title: str) -> bool:
    """Set the user-facing title for a session. Pass an empty string to
    clear (which falls back to the COALESCE chain in list_sessions).
    Returns True if a row was updated."""
    cleaned = (title or "").strip()
    with _STATE["lock"]:
        cur = _conn().execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (cleaned or None, time.time(), sid),
        )
        return cur.rowcount > 0


def delete_session(sid: str) -> None:
    with _STATE["lock"]:
        _conn().execute("DELETE FROM sessions WHERE id = ?", (sid,))
        _conn().execute("DELETE FROM messages WHERE session_id = ?", (sid,))


def session_exists(sid: str) -> bool:
    r = _conn().execute(
        "SELECT 1 FROM sessions WHERE id = ?", (sid,)
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
    session_id: str, role: str, content: str, mode: Optional[str] = None,
) -> int:
    with _STATE["lock"]:
        cur = _conn().execute(
            """
            INSERT INTO messages (session_id, role, content, mode, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, mode, time.time()),
        )
        return int(cur.lastrowid or 0)


def list_messages(session_id: str) -> list[dict]:
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


def get_session_meta(session_id: str) -> Optional[dict]:
    """Pinned conversational context (user name, project, key dates, ...)
    extracted by the LLM and saved alongside the session. None when the
    column is empty (brand-new session or extraction never ran)."""
    row = _conn().execute(
        "SELECT session_meta_json FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row or not row["session_meta_json"]:
        return None
    try:
        return json.loads(row["session_meta_json"])
    except json.JSONDecodeError:
        return None


def set_session_meta(session_id: str, meta: dict) -> None:
    """Persist a refreshed metadata snapshot for the session."""
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE sessions SET session_meta_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), time.time(), session_id),
        )


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
