"""Append-only audit-log writer (migration 006: ``audit_log`` in ``lai_db``).

One table, two driver paths so every component writes to the same trail
without a second datastore:

* :func:`record` — async, for callers that already hold an
  :class:`asyncpg.Connection` (``auth_router`` and other async handlers).
* :func:`record_sync` — psycopg2, for serve_rag's sync handlers and the DDiQ
  worker. It uses a lazily-created connection pool against the same ``DB_*``
  environment the rest of the stack reads.

Both are **best-effort**: writing an audit record must never break the request
it describes, so every failure is swallowed and logged at warning level.
Records are immutable (the table has a BEFORE UPDATE trigger); callers only
ever INSERT, and all values are bound as query parameters — never interpolated.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

_log = logging.getLogger("lai.audit")

# Plain string literals (not f-strings) so there is no SQL-construction surface
# at all. Values are always bound parameters; ``detail`` is cast to jsonb.
_INSERT_ASYNC = (
    "INSERT INTO audit_log "
    "(user_id, org_id, action, outcome, session_id, latency_ms, detail) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)"
)
_INSERT_SYNC = (
    "INSERT INTO audit_log "
    "(user_id, org_id, action, outcome, session_id, latency_ms, detail) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)"
)
# Read query. Optional filters are NULL-guarded (``$n IS NULL OR col = $n``) so
# the SQL is fully static — no clause-building, no interpolation. Newest first.
_SELECT = (
    "SELECT id, ts, user_id, org_id, action, outcome, session_id, latency_ms, detail "
    "FROM audit_log "
    "WHERE ($1::uuid IS NULL OR org_id = $1) "
    "AND ($2::text IS NULL OR action = $2) "
    "AND ($3::uuid IS NULL OR user_id = $3) "
    "ORDER BY ts DESC LIMIT $4 OFFSET $5"
)

_pool: Any = None
_pool_lock = threading.Lock()


def _detail_json(detail: dict[str, Any] | None) -> str | None:
    """Serialise ``detail`` to a JSON string (``default=str`` so UUIDs / dates
    survive); ``None`` and any encoding failure map to a NULL jsonb."""
    if detail is None:
        return None
    try:
        return json.dumps(detail, default=str)
    except (TypeError, ValueError):
        return None


def _get_pool() -> Any:
    """Lazily build a small psycopg2 pool against the shared ``lai_db``.

    Reuses the same ``DB_*`` env the auth pool, DDiQ, and the retrieval client
    read, so audit writes land in the one database every component shares.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            import psycopg2.pool

            _pool = psycopg2.pool.ThreadedConnectionPool(
                1,
                int(os.getenv("LAI_AUDIT_POOL_MAX", "4")),
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5433")),
                dbname=os.getenv("DB_NAME", "lai_db"),
                user=os.getenv("DB_USER", "lai_user"),
                password=os.getenv("DB_PASSWORD", "lai_test_password_2024"),
            )
    return _pool


async def record(
    conn: asyncpg.Connection,
    *,
    action: str,
    user_id: Any = None,
    org_id: Any = None,
    outcome: str = "success",
    session_id: str | None = None,
    latency_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit row via an existing asyncpg connection. Best-effort."""
    try:
        await conn.execute(
            _INSERT_ASYNC,
            user_id,
            org_id,
            action,
            outcome,
            session_id,
            latency_ms,
            _detail_json(detail),
        )
    except Exception as exc:  # best-effort: audit must never break the request
        _log.warning("audit record failed (action=%s): %s", action, exc)


def record_sync(
    *,
    action: str,
    user_id: Any = None,
    org_id: Any = None,
    outcome: str = "success",
    session_id: str | None = None,
    latency_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit row via the psycopg2 pool. Best-effort, never fatal."""
    conn = None
    pool = None
    try:
        pool = _get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_SYNC,
                (user_id, org_id, action, outcome, session_id, latency_ms, _detail_json(detail)),
            )
    except Exception as exc:  # best-effort: audit must never break the request
        _log.warning("audit record_sync failed (action=%s): %s", action, exc)
    finally:
        if conn is not None and pool is not None:
            with contextlib.suppress(Exception):
                pool.putconn(conn)


async def query(
    conn: asyncpg.Connection,
    *,
    org_id: Any = None,
    action: str | None = None,
    user_id: Any = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Read recent audit rows, newest first. Optional filters (``None`` skips
    one). The ``detail`` jsonb is parsed back to a dict.

    NOT best-effort (unlike the writers): a read failure surfaces to the admin
    caller so a broken query is visible rather than silently empty.
    """
    rows = await conn.fetch(_SELECT, org_id, action, user_id, limit, offset)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("detail")
        if isinstance(raw, str):
            try:
                item["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                item["detail"] = None
        out.append(item)
    return out
