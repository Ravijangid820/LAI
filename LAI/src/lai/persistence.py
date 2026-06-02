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

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from threading import RLock

_STATE: dict = {
    "conn": None,
    "uploads_dir": None,
    # RLock (reentrant) rather than Lock so a read function that calls another
    # read function in the same thread (e.g. list_messages -> session_exists)
    # doesn't deadlock when both acquire the lock. Writes never call reads
    # inside their locked block (verified), so the reentrancy is read-only and
    # the throughput characteristics match a plain Lock.
    "lock": RLock(),
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT,
    org_id                   TEXT,  -- firm tenant key (MULTIUSER_PLAN §3); Phase B scopes on it
    project_id               TEXT,  -- server-side project grouping (MULTIUSER_PLAN §6)
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    org_id       TEXT,                  -- firm tenant key (MULTIUSER_PLAN §3)
    doc_index    INTEGER NOT NULL,      -- the n in [M-n], 1-based, stable
    filename     TEXT,
    doc_text     TEXT,                  -- extracted markdown/text (filled by worker)
    n_pages      INTEGER,
    upload_ext   TEXT,                  -- ".pdf" / ".docx" — file on disk
    created_at   REAL NOT NULL,
    -- Async ingestion status: a document is queued the instant it's
    -- uploaded (so the UI never blocks), then a background worker walks it
    -- through processing → done|failed. ``pages_done``/``pages_total``
    -- drive the live progress bar; ``n_chunks`` is how many passages were
    -- indexed into pgvector; ``error`` carries the failure reason.
    status       TEXT NOT NULL DEFAULT 'done',  -- queued|processing|done|failed
    pages_done   INTEGER NOT NULL DEFAULT 0,
    pages_total  INTEGER NOT NULL DEFAULT 0,
    n_chunks     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
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
    org_id      TEXT,                  -- firm tenant key (MULTIUSER_PLAN §3)
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

-- Per-resource explicit sharing — Path A Step 2 (private-by-default + share
-- on top). One row per (session, shared-with user). The session's creator
-- (sessions.user_id) implicitly always sees their own row; the rows here
-- list COLLABORATORS the owner has explicitly granted access to. v1 is
-- view-only: shared users can READ everything under the session (messages,
-- matter documents, citations) but cannot rename/delete/append-message
-- (those stay owner-only). ``granted_by`` is the owner who issued the
-- share (audit); always == sessions.user_id at insert time.
CREATE TABLE IF NOT EXISTS session_shares (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE (session_id, user_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_session_shares_user
    ON session_shares(user_id);

CREATE INDEX IF NOT EXISTS idx_session_shares_session
    ON session_shares(session_id);
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
    # Firm tenancy (MULTIUSER_PLAN §3/§6). Nullable; unused until Phase B —
    # added here so existing chat DBs gain the columns on the next boot.
    if "org_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN org_id TEXT")
    if "project_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")

    # ``chunks_json`` holds the retrieval chunks ([M-n]/[C-n] sources) that
    # backed an assistant message, so citation chips still resolve to their
    # source after a page reload / conversation switch (the chunks otherwise
    # live only in the in-memory SSE response and vanish on rehydrate).
    msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "chunks_json" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN chunks_json TEXT")

    # Async-ingestion status columns on matter_documents (added with the
    # background-indexing feature). Older rows default to 'done' so existing
    # uploads keep working; new uploads start 'queued'.
    md_cols = {r["name"] for r in conn.execute("PRAGMA table_info(matter_documents)")}
    for col, ddl in (
        ("status", "ALTER TABLE matter_documents ADD COLUMN status TEXT NOT NULL DEFAULT 'done'"),
        ("pages_done", "ALTER TABLE matter_documents ADD COLUMN pages_done INTEGER NOT NULL DEFAULT 0"),
        ("pages_total", "ALTER TABLE matter_documents ADD COLUMN pages_total INTEGER NOT NULL DEFAULT 0"),
        ("n_chunks", "ALTER TABLE matter_documents ADD COLUMN n_chunks INTEGER NOT NULL DEFAULT 0"),
        ("error", "ALTER TABLE matter_documents ADD COLUMN error TEXT"),
        ("org_id", "ALTER TABLE matter_documents ADD COLUMN org_id TEXT"),
    ):
        if col not in md_cols:
            conn.execute(ddl)

    # feedback.org_id — firm tenancy (MULTIUSER_PLAN §3). Nullable, unused
    # until Phase B; the feedback table is created by the executescript above.
    fb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(feedback)")}
    if "org_id" not in fb_cols:
        conn.execute("ALTER TABLE feedback ADD COLUMN org_id TEXT")

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
    cols = set(r.keys())
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        # Phase B: round-trip the firm-tenant key so save_session(load_session(sid))
        # doesn't drop org_id. Falls back to None on legacy DBs predating 002.
        "org_id": r["org_id"] if "org_id" in cols else None,
        "title": r["title"] if "title" in cols else None,
        "filename": r["filename"],
        "contract_text": r["contract_text"],
        "n_pages": r["n_pages"] or 0,
        "tables": json.loads(r["tables_json"] or "[]"),
        "clauses": json.loads(r["clauses_json"]) if r["clauses_json"] else None,
        "analysis": json.loads(r["analysis_json"]) if r["analysis_json"] else None,
        "extraction_quality": (json.loads(r["extraction_quality_json"]) if r["extraction_quality_json"] else None),
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


def load_session(sid: str, user_id: str | None = None) -> dict | None:
    """Load a session by id, scoped to a viewer.

    Path A Step 2: visibility is creator OR explicit-share. A cross-user
    caller with no share returns ``None`` (route maps to 404 — no
    existence leak). This is a READ; for write-path checks use
    :func:`session_owned_by`.
    """
    with _STATE["lock"]:
        if user_id is None:
            r = _conn().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        else:
            r = (
                _conn()
                .execute(
                    """
                SELECT * FROM sessions
                WHERE id = ?
                  AND (user_id = ?
                       OR EXISTS (SELECT 1 FROM session_shares
                                  WHERE session_shares.session_id = sessions.id
                                    AND session_shares.user_id = ?))
                """,
                    (sid, user_id, user_id),
                )
                .fetchone()
            )
        return _row_to_session(r) if r else None


def save_session(sid: str, data: dict) -> None:
    """Upsert a session. Caller passes the same dict shape that was
    historically stored under ``STATE["sessions"][sid]``.

    Phase B writes BOTH ``user_id`` (created_by — attribution / audit
    trail) AND ``org_id`` (the firm-tenant key Phase B reads filter on).
    ``title`` is preserved on upsert when not explicitly set in ``data``
    — it's a user-controlled field, not derived from upload payloads."""
    now = time.time()
    with _STATE["lock"]:
        _conn().execute(
            """
            INSERT INTO sessions (
                id, user_id, org_id, title, filename, contract_text, n_pages,
                tables_json, clauses_json, analysis_json, extraction_quality_json,
                upload_ext, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id                 = COALESCE(excluded.user_id, sessions.user_id),
                org_id                  = COALESCE(excluded.org_id, sessions.org_id),
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
                data.get("org_id"),
                data.get("title"),
                data.get("filename"),
                data.get("contract_text"),
                int(data.get("n_pages") or 0),
                json.dumps(data.get("tables") or [], ensure_ascii=False),
                json.dumps(data["clauses"], ensure_ascii=False) if data.get("clauses") is not None else None,
                json.dumps(data["analysis"], ensure_ascii=False) if data.get("analysis") is not None else None,
                json.dumps(data["extraction_quality"], ensure_ascii=False)
                if data.get("extraction_quality") is not None
                else None,
                data.get("upload_ext"),
                data.get("uploaded_at") or now,
                now,
            ),
        )


def set_session_filename_if_unset(sid: str, fname: str) -> None:
    """Set the session's ``filename`` field iff it hasn't been set yet.

    The sidebar title resolves via COALESCE(title, filename, first message,
    'Untitled chat') — for sessions minted by POST /sessions (where
    /upload's legacy save_session is intentionally skipped to dodge a
    parallel-upload race), ``filename`` would otherwise stay null and the
    sidebar would show "Untitled chat" for an upload-only session. This
    fills in the first-arriving filename so the user sees something
    meaningful. ``WHERE filename IS NULL`` makes it race-safe across
    parallel uploads: only the first to land wins, the rest no-op."""
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE sessions SET filename = ?, updated_at = ? WHERE id = ? AND filename IS NULL",
            (fname, time.time(), sid),
        )


def set_session_contract(sid: str, contract_text: str, n_pages: int) -> None:
    """Fill a session's ``contract_text`` / ``n_pages`` after async ingestion.

    The FIRST uploaded document mirrors into these columns so the legacy
    single-document paths (analyze-contract, the old document preview)
    keep working. Done from the background worker once OCR finishes —
    upload() creates the session row with empty contract_text so it can
    return immediately.

    Race-safe: the UPDATE is guarded by ``WHERE contract_text IS NULL``
    so concurrent ingestion jobs for a multi-doc session (parallel
    uploads after POST /sessions) cannot stomp each other — only the
    first one to finish wins and subsequent calls are no-ops. Without
    this guard a folder drop of N files could leave ``contract_text``
    set to an arbitrary (last-arriving) doc's content, and a follow-up
    upload would silently overwrite the legacy mirror."""
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE sessions SET contract_text = ?, n_pages = ?, updated_at = ? WHERE id = ? AND contract_text IS NULL",
            (contract_text, n_pages, time.time(), sid),
        )


def update_session_title(sid: str, title: str, user_id: str | None = None) -> bool:
    """Set the user-facing title for a session. Pass an empty string to
    clear (which falls back to the COALESCE chain in list_sessions).
    Returns True if a row was updated.

    Phase B-revert: scoped on the creator. A cross-user UPDATE is a
    no-op; route handler maps False → 404 (no existence leak)."""
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


def _delete_session_files(sid: str) -> None:
    """Remove every uploaded blob on disk that belongs to a session.

    Files are named ``<sid><ext>`` (legacy single upload) and
    ``<sid>_m<doc_id><ext>`` (matter documents). Session ids are
    fixed-length UUIDs, so the single glob ``<sid>*`` matches exactly
    this session's files — one UUID can't be a prefix of another — and
    never another session's. Best-effort: a missing file or unlink error
    must not block the DB delete.
    """
    base = _STATE.get("uploads_dir")
    if not base:
        return
    try:
        for p in Path(base).glob(f"{sid}*"):
            with contextlib.suppress(OSError):
                p.unlink()
    except OSError:
        pass


def delete_session(sid: str, user_id: str | None = None) -> bool:
    """Delete a session, its messages, matter documents, feedback, AND the
    uploaded files on disk.

    Phase B-revert: scoped on the creator — only the user who started the
    session can delete it (no firm-wide rights). Returns ``True`` when a
    session row was actually deleted; caller maps ``False`` → 404.
    """
    with _STATE["lock"]:
        if user_id is None:
            cur = _conn().execute("DELETE FROM sessions WHERE id = ?", (sid,))
        else:
            cur = _conn().execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (sid, user_id),
            )
        deleted = cur.rowcount > 0
        if deleted:
            # Foreign-keys ON CASCADE was added to the schema, but
            # historical DBs may predate that; clean up messages
            # explicitly to keep the contract identical across schemas.
            # (matter_documents / feedback rely on the FK cascade, which
            # is enforced because init() sets PRAGMA foreign_keys=ON.)
            _conn().execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            # The cascade only clears DB rows — the uploaded PDF/DOCX
            # bytes live on disk and would otherwise be orphaned forever
            # (a confidentiality / retention problem for a legal product).
            _delete_session_files(sid)
        return deleted


def session_exists(sid: str, user_id: str | None = None) -> bool:
    """Cheap visibility check used by message/document/feedback READ gates.

    Path A Step 2: returns True when the caller is the session's owner
    OR the session has been explicitly shared with them via
    ``session_shares``. Cross-user callers with no share get False —
    callers map cleanly to 404 without revealing existence.

    WRITE paths (delete, rename, append-message, set-meta, record
    feedback, add matter doc) MUST use :func:`session_owned_by` instead
    — sharing is view-only in v1; a shared user can read but not write.
    """
    with _STATE["lock"]:
        if user_id is None:
            r = _conn().execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone()
        else:
            r = (
                _conn()
                .execute(
                    """
                SELECT 1 FROM sessions
                WHERE id = ?
                  AND (user_id = ?
                       OR EXISTS (SELECT 1 FROM session_shares
                                  WHERE session_shares.session_id = sessions.id
                                    AND session_shares.user_id = ?))
                """,
                    (sid, user_id, user_id),
                )
                .fetchone()
            )
        return r is not None


def session_owned_by(sid: str, user_id: str) -> bool:
    """Strict-owner visibility check for write gates (Path A Step 2).

    Returns True only when ``user_id`` is the session's creator —
    explicit shares grant READ access, not WRITE. Used by
    :func:`delete_session`, :func:`update_session_title`,
    :func:`add_message`, :func:`set_session_meta`,
    :func:`record_feedback`, :func:`add_matter_document` so a shared
    collaborator cannot rename / delete / append-message / re-share the
    session.
    """
    with _STATE["lock"]:
        r = (
            _conn()
            .execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?",
                (sid, user_id),
            )
            .fetchone()
        )
        return r is not None


def list_sessions(limit: int = 50, user_id: str | None = None) -> list[dict]:
    """Light-weight list (no contract_text/analysis blobs) for UI sidebars.

    Path A Step 2: sidebar shows the caller's own sessions PLUS sessions
    explicitly shared with them. Owner+shared appear as one list (no
    visual differentiation in v1 — the chat header carries the share
    state). Edit rights remain owner-only at the route layer."""
    with _STATE["lock"]:
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
            rows = (
                _conn()
                .execute(
                    base_select + " ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
                .fetchall()
            )
        else:
            rows = (
                _conn()
                .execute(
                    base_select
                    + """
                WHERE user_id = ?
                   OR EXISTS (SELECT 1 FROM session_shares
                              WHERE session_shares.session_id = sessions.id
                                AND session_shares.user_id = ?)
                ORDER BY updated_at DESC
                LIMIT ?""",
                    (user_id, user_id, limit),
                )
                .fetchall()
            )
        return [
            {
                "id": r["id"],
                "title": r["title"],  # always non-null (COALESCE chain)
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
    with _STATE["lock"]:
        r = _conn().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(r["n"])


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


def add_message(
    session_id: str,
    role: str,
    content: str,
    mode: str | None = None,
    user_id: str | None = None,
    chunks: list | None = None,
) -> int:
    """Append a message. Returns the new row id (or 0 on insert failure).

    Path A Step 2: WRITE-path — only the session's CREATOR can append
    messages (sharing is view-only in v1). A foreign caller — even one
    the session is shared with — gets 0. Editor-tier sharing is a
    future enhancement.

    ``chunks`` (assistant turns only) are the citation sources behind the
    answer; stored as JSON so the citation panel still resolves [M-n]/
    [C-n] handles after the conversation is reloaded from the DB.
    """
    if user_id is not None and not session_owned_by(session_id, user_id):
        return 0
    chunks_json = json.dumps(chunks) if chunks else None
    with _STATE["lock"]:
        cur = _conn().execute(
            """
            INSERT INTO messages (session_id, role, content, mode, created_at, chunks_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, mode, time.time(), chunks_json),
        )
        return int(cur.lastrowid or 0)


def list_messages(session_id: str, user_id: str | None = None) -> list[dict]:
    """Return all messages for a session.

    Phase B-revert: scoped on the creator. A cross-user caller gets an
    empty list — combined with the route's session_exists check this
    enforces "no cross-user message reads" without leaking via shape.
    """
    with _STATE["lock"]:
        if user_id is not None and not session_exists(session_id, user_id=user_id):
            return []
        rows = (
            _conn()
            .execute(
                """
            SELECT id, role, content, mode, created_at, chunks_json
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            """,
                (session_id,),
            )
            .fetchall()
        )
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "mode": r["mode"],
                "created_at": r["created_at"],
                "chunks": json.loads(r["chunks_json"]) if r["chunks_json"] else [],
            }
            for r in rows
        ]


def get_session_meta(session_id: str, user_id: str | None = None) -> dict | None:
    """Pinned conversational context (user name, project, key dates, ...)
    extracted by the LLM and saved alongside the session. None when the
    column is empty (brand-new session or extraction never ran).

    Path A Step 2: READ-path — viewable by creator OR explicit-share
    user. Write counterpart :func:`set_session_meta` stays owner-only.
    """
    with _STATE["lock"]:
        if user_id is None:
            row = (
                _conn()
                .execute(
                    "SELECT session_meta_json FROM sessions WHERE id = ?",
                    (session_id,),
                )
                .fetchone()
            )
        else:
            row = (
                _conn()
                .execute(
                    """
                SELECT session_meta_json FROM sessions
                WHERE id = ?
                  AND (user_id = ?
                       OR EXISTS (SELECT 1 FROM session_shares
                                  WHERE session_shares.session_id = sessions.id
                                    AND session_shares.user_id = ?))
                """,
                    (session_id, user_id, user_id),
                )
                .fetchone()
            )
        if not row or not row["session_meta_json"]:
            return None
        try:
            return json.loads(row["session_meta_json"])
        except json.JSONDecodeError:
            return None


def set_session_meta(session_id: str, meta: dict, user_id: str | None = None) -> None:
    """Persist a refreshed metadata snapshot for the session. Path A
    Step 2: WRITE — owner-only. A shared collaborator's meta-extraction
    would no-op here (the WHERE ... AND user_id filter doesn't match)."""
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
    message_id: int | None = None,
    reason: str | None = None,
    comment: str | None = None,
) -> int | None:
    """Upsert a feedback row keyed by ``(user_id, session_id, message_id)``.

    Path A Step 2: ``user_id`` is the feedback author AND visibility
    check. Owner-only — a shared collaborator cannot rate the session
    (sharing is view-only). Cross-user feedback is silently dropped.

    The upsert uses ``ON CONFLICT ... DO UPDATE`` rather than ``INSERT OR
    REPLACE`` so the auto-incremented ``id`` is preserved across edits.
    """
    if not session_owned_by(session_id, user_id):
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


def list_feedback(session_id: str, user_id: str | None = None) -> list[dict]:
    """All feedback rows attached to a session.

    Phase B-revert: scoped on the creator — matches the access model of
    ``list_messages``. Order is newest-first so the UI can show the
    lawyer their most-recent verdict at the top without re-sorting.
    """
    with _STATE["lock"]:
        if user_id is not None and not session_exists(session_id, user_id=user_id):
            return []
        rows = (
            _conn()
            .execute(
                """
            SELECT id, session_id, message_id, user_id, rating, reason, comment, created_at
            FROM feedback
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            """,
                (session_id,),
            )
            .fetchall()
        )
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
    with _STATE["lock"]:
        row = (
            _conn()
            .execute(
                "SELECT 1 FROM messages WHERE id = ? AND session_id = ?",
                (message_id, session_id),
            )
            .fetchone()
        )
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
    user_id: str | None = None,
    org_id: str | None = None,
    status: str = "done",
) -> dict | None:
    """Append one uploaded document to a matter. Returns the new row as a
    dict (incl. its stable ``doc_index`` = the n in [M-n]), or ``None`` if
    the session isn't owned by ``user_id``.

    Path A Step 2: WRITE — owner-only. A shared collaborator cannot
    attach new documents (sharing is view-only); ``user_id`` is checked
    via :func:`session_owned_by`. ``org_id`` is still stamped on the row
    for membership/audit.

    ``doc_index`` is ``max(existing)+1`` so handles never shift when more
    documents are added later. ``status`` is ``'queued'`` for async
    ingestion (the worker fills ``doc_text``/``n_pages`` later) or the
    default ``'done'`` for synchronous callers/tests.
    """
    if user_id is not None and not session_owned_by(session_id, user_id):
        return None
    # Normalize to the LEAF filename for both the dedup probe and the
    # INSERT. Callers SHOULD pass a leaf (Path(filename).name), and both
    # POST /upload and the tus completion hook do — but some legacy rows
    # in the wild carry a folder prefix (``lai-test-drop/foo.pdf``) from
    # an older Chromium folder-drag quirk. That made the same file
    # collide under TWO different ``filename`` strings: prefix-form vs
    # leaf-form, both stored separately. Normalizing here means the
    # SELECT below can't be bypassed by a caller that forgot to strip,
    # and we never write a new row whose name carries a path component.
    filename = Path(filename).name or filename
    with _STATE["lock"]:
        # Same-name dedup — authoritative line of defense. The FE pre-
        # filter catches most cases but a second tab, direct API call or
        # parallel-batch race can bypass it. Inside the connection lock,
        # SELECT-then-INSERT is atomic (SQLite serialises writers on this
        # lock), so two concurrent callers with the same (session_id,
        # filename) cannot both end up inserting. Idempotent return
        # rather than 409 — honest retries succeed without ceremony.
        # Status is irrelevant: delete_matter_document HARD-deletes the
        # row, so a previously deleted file is genuinely absent here and
        # a re-upload flows through to the INSERT path normally.
        # The ``__dedup_existing`` marker lets the endpoint skip
        # save_matter_upload + _enqueue_ingestion (which would otherwise
        # rewrite the bytes and re-index, creating duplicate chunks).
        existing = (
            _conn()
            .execute(
                """
            SELECT id, session_id, doc_index, filename, n_pages,
                   upload_ext, created_at, status
            FROM matter_documents
            WHERE session_id = ? AND filename = ?
            """,
                (session_id, filename),
            )
            .fetchone()
        )
        if existing is not None:
            base = {
                "id": int(existing["id"]),
                "session_id": existing["session_id"],
                "doc_index": int(existing["doc_index"]),
                "filename": existing["filename"],
                "n_pages": int(existing["n_pages"] or 0),
                "upload_ext": existing["upload_ext"],
                "created_at": existing["created_at"],
                "status": existing["status"],
            }
            # Failed row → user is retrying after a transient upload
            # corruption (e.g. the 0-byte multipart that triggered
            # Docling's "Input document … is not valid"). Don't treat
            # this as a permanent dedup hit; mark it for the endpoint to
            # reset state, clear any stale matter_chunks, overwrite the
            # on-disk blob with the new bytes, and re-enqueue ingestion.
            # The doc_index STAYS so any in-flight UI / chat history
            # that already saw "M-{n}" continues to point at the same
            # row — no shifting handles.
            if existing["status"] == "failed":
                base["__dedup_failed_retry"] = True
                return base
            base["__dedup_existing"] = True
            return base
        row = (
            _conn()
            .execute(
                "SELECT COALESCE(MAX(doc_index), 0) AS mx FROM matter_documents WHERE session_id = ?",
                (session_id,),
            )
            .fetchone()
        )
        next_index = int(row["mx"]) + 1
        ts = time.time()
        cur = _conn().execute(
            """
            INSERT INTO matter_documents
                (session_id, org_id, doc_index, filename, doc_text, n_pages, upload_ext, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, org_id, next_index, filename, doc_text, n_pages, upload_ext, ts, status),
        )
        return {
            "id": int(cur.lastrowid or 0),
            "session_id": session_id,
            "doc_index": next_index,
            "filename": filename,
            "n_pages": n_pages,
            "upload_ext": upload_ext,
            "created_at": ts,
            "status": status,
        }


def update_matter_progress(
    doc_id: int,
    *,
    status: str | None = None,
    pages_done: int | None = None,
    pages_total: int | None = None,
) -> None:
    """Update a document's ingestion status / progress (called by the
    background worker as it processes pages). Any field left ``None`` is
    untouched."""
    sets, params = [], []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if pages_done is not None:
        sets.append("pages_done = ?")
        params.append(int(pages_done))
    if pages_total is not None:
        sets.append("pages_total = ?")
        params.append(int(pages_total))
    if not sets:
        return
    params.append(doc_id)
    with _STATE["lock"]:
        _conn().execute(
            f"UPDATE matter_documents SET {', '.join(sets)} WHERE id = ?",
            params,
        )


def finalize_matter_document(
    doc_id: int,
    *,
    doc_text: str,
    n_pages: int,
    n_chunks: int,
) -> None:
    """Mark a document done and store its extracted text + chunk count."""
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE matter_documents SET status='done', doc_text=?, n_pages=?, "
            "n_chunks=?, pages_done=?, pages_total=?, error=NULL WHERE id = ?",
            (doc_text, n_pages, n_chunks, n_pages, n_pages, doc_id),
        )


def reset_matter_document_for_retry(doc_id: int) -> None:
    """Reset a previously-failed row to 'queued' so the ingestion pipeline
    can re-process the new bytes the user just re-uploaded under the same
    filename. The endpoint calls this when ``add_matter_document`` returned
    the ``__dedup_failed_retry`` marker.

    Caller is responsible for the surrounding orchestration:
      1. Clear pgvector entries for this doc_index (defensive — failed
         rows usually never indexed, but partial chunking is possible
         if Docling died mid-batch on a previous attempt).
      2. Overwrite the on-disk blob with the new bytes
         (``save_matter_upload``).
      3. Re-enqueue ingestion.

    Keeping the orchestration in the API layer mirrors the
    :func:`delete_matter_document` pattern and avoids importing the
    retrieval client into this module.

    ``doc_index`` stays stable across the retry so any chat history /
    citation handle that already referenced ``[M-n]`` keeps pointing at
    the same row instead of silently rotating to a fresh slot.
    """
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE matter_documents SET status='queued', error=NULL, "
            "doc_text='', n_pages=0, pages_done=0, pages_total=0, n_chunks=0 "
            "WHERE id = ?",
            (doc_id,),
        )


def delete_matter_document(
    session_id: str,
    doc_index: int,
    *,
    user_id: str | None = None,
) -> dict | None:
    """Hard-delete a matter document from a session: DB row + on-disk file.

    Caller is responsible for also clearing the pgvector embeddings
    (``retrieval_client.delete_matter_chunks(session_id, doc_index=...)``)
    — keeping that orchestration in the API layer keeps this module free
    of a retrieval-client import.

    Returns the deleted row's ``{id, doc_index, filename, upload_ext}`` so
    the caller can also remove the disk file by path, or ``None`` when:
      • the session isn't owned by ``user_id`` (Path A Step 2 write gate —
        a shared collaborator can READ but not delete), or
      • no row matched.

    The ``doc_index`` value stays consumed afterwards — we never reuse a
    deleted index (``add_matter_document`` allocates ``MAX(doc_index)+1``,
    not a free-slot scan) so existing ``[M-n]`` citations in old chat
    history don't silently re-point at a different document.
    """
    if user_id is not None and not session_owned_by(session_id, user_id):
        return None
    with _STATE["lock"]:
        row = (
            _conn()
            .execute(
                "SELECT id, doc_index, filename, upload_ext FROM matter_documents "
                "WHERE session_id = ? AND doc_index = ?",
                (session_id, int(doc_index)),
            )
            .fetchone()
        )
        if row is None:
            return None
        info = {
            "id": int(row["id"]),
            "doc_index": int(row["doc_index"]),
            "filename": row["filename"],
            "upload_ext": row["upload_ext"],
        }
        _conn().execute("DELETE FROM matter_documents WHERE id = ?", (info["id"],))
    # Best-effort disk cleanup outside the lock (file I/O shouldn't block
    # other writes). A missing file is fine — the DB row is the source of
    # truth and the caller has already done what the user asked.
    try:
        path = matter_document_path(
            session_id,
            info["id"],
            ext=info["upload_ext"],
        )
        if path is not None and path.exists():
            path.unlink()
    except Exception as exc:
        print(
            f"[delete] matter file unlink failed for {session_id}/M-{doc_index}: {exc}",
            flush=True,
        )
    return info


def fail_matter_document(doc_id: int, error: str) -> None:
    """Mark a document's ingestion failed with a reason (shown in the UI)."""
    with _STATE["lock"]:
        _conn().execute(
            "UPDATE matter_documents SET status='failed', error=? WHERE id = ?",
            (error[:500], doc_id),
        )


def list_unfinished_matter_documents() -> list[dict]:
    """All documents still queued/processing across every session — used at
    startup to re-enqueue work that an interrupted process left mid-flight."""
    with _STATE["lock"]:
        rows = (
            _conn()
            .execute(
                "SELECT id, session_id, doc_index, filename, upload_ext "
                "FROM matter_documents WHERE status IN ('queued', 'processing')",
            )
            .fetchall()
        )
        return [
            {
                "id": int(r["id"]),
                "session_id": r["session_id"],
                "doc_index": int(r["doc_index"]),
                "filename": r["filename"],
                "upload_ext": r["upload_ext"],
            }
            for r in rows
        ]


def list_matter_documents(
    session_id: str,
    user_id: str | None = None,
    *,
    include_text: bool = False,
) -> list[dict]:
    """All documents attached to a matter, ordered by ``doc_index`` (= [M-n]).

    ``include_text=False`` (default) omits the heavy ``doc_text`` blob —
    the UI document list doesn't need it. The chat path passes
    ``include_text=True`` to build the [M-n] prompt sources.
    Phase B-revert: filtered by the session's creator so a caller who
    didn't create the session sees an empty list (no cross-user leakage).
    """
    with _STATE["lock"]:
        if user_id is not None and not session_exists(session_id, user_id=user_id):
            return []
        cols = "id, doc_index, filename, n_pages, upload_ext, created_at, status, pages_done, pages_total, n_chunks, error"
        if include_text:
            cols += ", doc_text"
        rows = (
            _conn()
            .execute(
                f"SELECT {cols} FROM matter_documents WHERE session_id = ? ORDER BY doc_index ASC",
                (session_id,),
            )
            .fetchall()
        )
        out: list[dict] = []
        for r in rows:
            d = {
                "id": int(r["id"]),
                "doc_index": int(r["doc_index"]),
                "filename": r["filename"],
                "n_pages": int(r["n_pages"]) if r["n_pages"] is not None else 0,
                "upload_ext": r["upload_ext"],
                "created_at": float(r["created_at"]),
                "status": r["status"] or "done",
                "pages_done": int(r["pages_done"] or 0),
                "pages_total": int(r["pages_total"] or 0),
                "n_chunks": int(r["n_chunks"] or 0),
                "error": r["error"],
            }
            if include_text:
                d["doc_text"] = r["doc_text"] or ""
            out.append(d)
        return out


def get_matter_document(
    session_id: str,
    doc_index: int,
    user_id: str | None = None,
) -> dict | None:
    """One matter document by its [M-n] index, or None. Phase B-revert:
    scoped on the session's creator so a caller who doesn't own the
    session sees ``None`` (the per-document preview route maps to 404)."""
    with _STATE["lock"]:
        if user_id is not None and not session_exists(session_id, user_id=user_id):
            return None
        r = (
            _conn()
            .execute(
                "SELECT id, doc_index, filename, n_pages, upload_ext, created_at "
                "FROM matter_documents WHERE session_id = ? AND doc_index = ?",
                (session_id, doc_index),
            )
            .fetchone()
        )
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


def matter_document_path(session_id: str, doc_id: int, ext: str | None = None) -> Path | None:
    """Disk path of a matter document's original file. Files are stored as
    ``<session_id>_m<doc_id><ext>`` to keep many docs per session distinct.

    ``ext`` should be passed by callers that already know the extension
    (it's stored on ``matter_documents.upload_ext`` and is derivable from
    the filename); the no-arg form is a defensive fallback for callers
    that don't, and tries the full known-extensions set including
    images. Without images in the fallback set, image uploads landed on
    disk but the ingestion job couldn't find them back."""
    base = Path(_STATE["uploads_dir"])
    if ext:
        p = base / f"{session_id}_m{doc_id}{ext}"
        return p if p.exists() else None
    for candidate in (
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".txt",
        ".csv",
        ".md",
        # Image OCR (vision-LLM path):
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".tiff",
        ".tif",
        ".bmp",
        ".bin",
    ):
        p = base / f"{session_id}_m{doc_id}{candidate}"
        if p.exists():
            return p
    return None


def save_matter_upload(session_id: str, doc_id: int, content: bytes, filename: str) -> str:
    """Write a matter document's bytes to ``<session_id>_m<doc_id><ext>``.
    Returns the extension stored.

    Refuses empty payloads up-front so the ingestion worker can't pick up
    a 0-byte file and surface Docling's cryptic "Input document … is not
    valid." We hit this in the wild — a row landed with 0 bytes on disk
    while the matter_documents row claimed an upload had happened; the
    chip then sat at "Fehlgeschlagen: Input document is not valid".
    Better to fail loudly here than to write the empty file and let the
    error surface 30 seconds later via Docling.

    Also fsyncs and verifies the on-disk size matches the buffer we tried
    to write, so a partial write surfaces immediately instead of silently
    truncating a doc the user thinks is fully ingested.
    """
    if not content:
        raise ValueError(
            f"save_matter_upload: refusing to write 0 bytes for {filename!r} (session={session_id}, doc_id={doc_id})"
        )
    suffix = Path(filename).suffix.lower() or ".bin"
    target = Path(_STATE["uploads_dir"]) / f"{session_id}_m{doc_id}{suffix}"
    # write_bytes() ≈ open+write+close — but doesn't fsync. We add the
    # fsync because the worker pool reads the file back in a different
    # thread (and after enqueue → context switch → some millis later);
    # without fsync, on a heavily-loaded box the reader can race the
    # OS page cache flush.
    with open(target, "wb") as f:
        f.write(content)
        f.flush()
        with contextlib.suppress(OSError):
            os.fsync(f.fileno())
    actual = target.stat().st_size
    if actual != len(content):
        # Surface partial writes loudly. The caller (`POST /upload` and
        # the tus completion hook) now sees an IOError and the
        # matter_documents row gets marked status='failed' with this
        # exact message — instead of "Input document is not valid"
        # popping out of Docling 30s later.
        raise OSError(
            f"save_matter_upload: wrote {actual} bytes to {target}, "
            f"expected {len(content)} bytes — partial write detected"
        )
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


def upload_path(sid: str, ext: str | None = None) -> Path | None:
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


# ---------------------------------------------------------------------------
# session sharing (Path A Step 2 — explicit per-resource view-only sharing)
# ---------------------------------------------------------------------------


def session_owner(session_id: str) -> str | None:
    """Return the ``user_id`` of the session's creator, or ``None`` if the
    session is unknown. Used by route handlers to gate owner-only share
    management (add/remove/list shares) without coupling them to the
    read-visibility widening that everyone else gets."""
    with _STATE["lock"]:
        r = (
            _conn()
            .execute(
                "SELECT user_id FROM sessions WHERE id = ?",
                (session_id,),
            )
            .fetchone()
        )
        return r["user_id"] if r else None


def list_session_shares(session_id: str) -> list[dict]:
    """All users this session is explicitly shared with, newest-first.

    Authorisation is the caller's responsibility — route handlers must
    confirm the caller is the session owner (or a super-admin) before
    invoking this. We don't take a viewer arg here because the share list
    itself is owner-only metadata, not part of the "can I see it?" plane.
    """
    with _STATE["lock"]:
        rows = (
            _conn()
            .execute(
                """
            SELECT id, session_id, user_id, granted_by, created_at
            FROM session_shares
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            """,
                (session_id,),
            )
            .fetchall()
        )
        return [
            {
                "id": int(r["id"]),
                "session_id": r["session_id"],
                "user_id": r["user_id"],
                "granted_by": r["granted_by"],
                "created_at": float(r["created_at"]),
            }
            for r in rows
        ]


def add_session_share(
    session_id: str,
    target_user_id: str,
    *,
    granted_by: str,
) -> int | None:
    """Grant ``target_user_id`` view access to ``session_id``.

    Returns the new row id, OR an existing row id if the share already
    exists (idempotent; re-clicking Share in the UI is a no-op). Returns
    ``None`` when the caller (``granted_by``) is not the session's
    creator — the route handler maps that to 404 (no existence leak).

    Self-share (granting access to the owner) is also a no-op: an owner
    already has full access by definition. Returns the synthetic id 0
    in that case so the caller can distinguish "owner re-added" from
    a hard authorisation failure.
    """
    owner = session_owner(session_id)
    if owner is None or owner != granted_by:
        return None
    if target_user_id == owner:
        return 0
    with _STATE["lock"]:
        cur = _conn().execute(
            """
            INSERT INTO session_shares (session_id, user_id, granted_by, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (session_id, user_id) DO UPDATE SET
                granted_by = excluded.granted_by
            RETURNING id
            """,
            (session_id, target_user_id, granted_by, time.time()),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else None


def revoke_session_share(
    session_id: str,
    target_user_id: str,
    *,
    granted_by: str,
) -> bool:
    """Remove ``target_user_id`` from the session's share list.

    Returns ``True`` when a row was deleted, ``False`` otherwise (no such
    share, OR caller is not the session owner — route maps to 404 to avoid
    leaking ownership of someone else's session)."""
    owner = session_owner(session_id)
    if owner is None or owner != granted_by:
        return False
    with _STATE["lock"]:
        cur = _conn().execute(
            "DELETE FROM session_shares WHERE session_id = ? AND user_id = ?",
            (session_id, target_user_id),
        )
        return cur.rowcount > 0


def session_share_user_ids(session_id: str) -> set[str]:
    """Set of user_ids the session is shared with. Used by tests + internal
    callers; route handlers should prefer the SQL-level ``EXISTS`` clauses
    in the visibility-widening reads (less data over the wire)."""
    with _STATE["lock"]:
        rows = (
            _conn()
            .execute(
                "SELECT user_id FROM session_shares WHERE session_id = ?",
                (session_id,),
            )
            .fetchall()
        )
        return {r["user_id"] for r in rows}
