"""Postgres schema + connection pool for the DDiQ pipeline.

Moved out of the legacy ``ddiq_report`` god-module in H-5. This
module owns:

* :data:`SCHEMA_SQL` — the full DDiQ table set (``ddiq_documents``,
  ``ddiq_doc_chunks``, ``ddiq_reports``, ``ddiq_geocode_cache``,
  ``ddiq_parcel_cache``, ``ddiq_project_areas``, ``ddiq_contracts``,
  ``ddiq_contract_parcels``, ``ddiq_classified_parcels``).
* :data:`DB_CONFIG` — connection params from env. Mirrored from
  the legacy module; production callers point at the
  ``lai_postgres_main`` container.
* :func:`init_db` — runs ``SCHEMA_SQL`` once at startup. Idempotent.
* :func:`init_pool` / :func:`close_pool` / :class:`_PooledConn` /
  :func:`get_conn` — a single :class:`psycopg2.pool.ThreadedConnectionPool`
  shared across the API + Celery worker process. ``conn.close()``
  returns the connection to the pool; the wrapper keeps existing
  call sites unchanged.
* :func:`reap_orphans` — startup hook that marks reports stuck in
  ``running`` for >3 h as failed. The timeout matches Celery's hard
  time-limit (120 min), so this won't fight at-least-once
  redelivery (see H-4 commit ``b87bf1f``).

No DDiQ-specific imports — only stdlib + psycopg2 — so this module
can be imported from anywhere without circular dependencies.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

__all__ = [
    "DB_CONFIG",
    "MAX_FILE_SIZE",
    "SCHEMA_SQL",
    "close_pool",
    "get_conn",
    "init_db",
    "init_pool",
    "reap_orphans",
]


_log = logging.getLogger("ddiq")


# ── Connection params ────────────────────────────────────────────────


DB_CONFIG: dict[str, Any] = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "lai_db"),
    "user":     os.getenv("DB_USER", "lai_user"),
    "password": os.getenv("DB_PASSWORD", "lai_test_password_2024"),
}


# Document upload size cap, shared with the API layer. Kept here so
# both the schema (``ddiq_documents.size_bytes``) and the upload
# handler enforce the same ceiling. Bumped 50 → 100 MiB after live runs
# showed that real legal binders (signed scans, expert reports, full
# data-room exports) routinely land in the 30–70 MB range and hit the
# old cap. 100 MiB still fits comfortably in memory for one upload at a
# time and keeps the OOM ceiling generous on the analyzer host.
MAX_FILE_SIZE: int = 100 * 1024 * 1024


# ── Schema ───────────────────────────────────────────────────────────


SCHEMA_SQL = """
-- pgvector is required for ddiq_doc_chunks.embedding (4096-dim Qwen3 vectors).
-- Idempotent: no-op when the extension is already enabled. The whole
-- SCHEMA_SQL runs in one transaction, so without this every CREATE TABLE
-- below fails when the DB is fresh.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS ddiq_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), filename TEXT NOT NULL,
    size_bytes BIGINT DEFAULT 0, upload_date TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'pending', category TEXT DEFAULT 'Uncategorized',
    full_text TEXT, chunk_count INT DEFAULT 0, session_id TEXT);
CREATE TABLE IF NOT EXISTS ddiq_doc_chunks (
    id SERIAL PRIMARY KEY, doc_id UUID REFERENCES ddiq_documents(id) ON DELETE CASCADE,
    chunk_idx INT NOT NULL, text TEXT NOT NULL, embedding vector(4096), UNIQUE(doc_id, chunk_idx));
    -- Qwen3-Embedding-8B returns 4096-dim vectors. If you swap to a different
    -- embedding model (1024-dim sentence-transformers / 1536-dim ada / etc.),
    -- update this column and drop ddiq_doc_chunks first.
CREATE TABLE IF NOT EXISTS ddiq_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), created_at TIMESTAMPTZ DEFAULT NOW(),
    project_name TEXT, document_ids UUID[], preset TEXT,
    report_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Status fields for the async job pattern. NULL/legacy rows count as "done".
    status TEXT DEFAULT 'done',          -- queued | running | done | failed
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    progress_step TEXT,                  -- short label for UI ("classifying", etc.)
    progress_percent DOUBLE PRECISION DEFAULT 0.0,
    error TEXT,
    -- Stable hash of (sorted doc_ids, preset, project_name) — lets us dedup
    -- repeat requests and return the cached/in-flight report instead of
    -- recomputing the 30-60 min pipeline.
    request_fingerprint TEXT);
-- Forward-compat: add the columns if the table already exists.
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'done';
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS progress_step TEXT;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS progress_percent DOUBLE PRECISION DEFAULT 0.0;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
-- H-4: Celery task id. Set when the API enqueues a job; the worker
-- doesn't read it. Operators correlate Celery logs to this row by id.
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS celery_task_id TEXT;
-- Track A item 6: the fingerprint index is now UNIQUE so two concurrent
-- /report/generate calls with identical (doc_ids, preset, project_name)
-- can't both write a row. The old (non-unique) index is dropped first
-- because ``CREATE UNIQUE INDEX IF NOT EXISTS`` with the same name would
-- silently no-op if the existing index isn't unique. New name avoids
-- collision with any in-flight code referencing the old one.
DROP INDEX IF EXISTS ddiq_reports_fingerprint_idx;
CREATE UNIQUE INDEX IF NOT EXISTS ddiq_reports_fingerprint_uniq_idx
    ON ddiq_reports(request_fingerprint) WHERE request_fingerprint IS NOT NULL;
CREATE TABLE IF NOT EXISTS ddiq_geocode_cache (
    address TEXT PRIMARY KEY, lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW(),
    -- TTL on the geocode cache. Rows are honored only while
    -- ``expires_at > NOW()``; pre-TTL rows (NULL ``expires_at``) are
    -- treated as expired so any wrong-state Nominatim answers cached
    -- before the bbox gate landed get re-geocoded once.
    expires_at TIMESTAMPTZ);
ALTER TABLE ddiq_geocode_cache ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS ddiq_parcel_cache (
    coord_key TEXT PRIMARY KEY, parcel_data JSONB NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW(),
    -- TTL on the parcel cache. Cadastral data updates quarterly at most
    -- but 30 days is conservative and matches the geocode-cache pattern
    -- (Track A item 3). NULL ``expires_at`` is treated as expired so any
    -- pre-TTL row is refetched once.
    expires_at TIMESTAMPTZ);
ALTER TABLE ddiq_parcel_cache ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS ddiq_project_areas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT,
    polygon JSONB NOT NULL, centroid_lat DOUBLE PRECISION, centroid_lng DOUBLE PRECISION,
    area_km2 DOUBLE PRECISION DEFAULT 0, source TEXT DEFAULT 'user_drawn',
    created_at TIMESTAMPTZ DEFAULT NOW(), report_id UUID);
CREATE TABLE IF NOT EXISTS ddiq_contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), doc_id UUID,
    contract_ref TEXT, contract_type TEXT, contracting_entity TEXT,
    raw_text_excerpt TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), report_id UUID);
CREATE TABLE IF NOT EXISTS ddiq_contract_parcels (
    id SERIAL PRIMARY KEY, contract_id UUID REFERENCES ddiq_contracts(id) ON DELETE CASCADE,
    parcel_identifier TEXT NOT NULL, match_type TEXT DEFAULT 'exact',
    confidence DOUBLE PRECISION DEFAULT 1.0);
CREATE TABLE IF NOT EXISTS ddiq_classified_parcels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), report_id UUID,
    parcel_number TEXT NOT NULL, gemarkung TEXT, flur INT DEFAULT 0,
    normalized_id TEXT, polygon JSONB, polygon_source TEXT DEFAULT 'estimated',
    classification TEXT NOT NULL DEFAULT 'not_secured',
    color TEXT DEFAULT 'red', confidence DOUBLE PRECISION DEFAULT 0,
    matched_contract_id UUID, classification_reason TEXT,
    area_ha DOUBLE PRECISION DEFAULT 0, owner TEXT, linked_wea TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_ddiq_chunks_doc ON ddiq_doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_ddiq_classified_report ON ddiq_classified_parcels(report_id);
CREATE INDEX IF NOT EXISTS idx_ddiq_contracts_report ON ddiq_contracts(report_id);
-- Tenant isolation (auth migration): the aux tables carry user_id so a
-- report's parcels/contracts/area are scoped to their owner. The live
-- DB gained these columns via the auth rollout, but the CREATE TABLE
-- statements above predate it — without these forward-compat ALTERs a
-- fresh init_db() would build the tables without user_id and every
-- aux INSERT (which supplies user_id) would fail. Idempotent.
ALTER TABLE ddiq_project_areas ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_contracts ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_classified_parcels ADD COLUMN IF NOT EXISTS user_id UUID;
"""


def init_db() -> None:
    """Apply :data:`SCHEMA_SQL` once at startup. Idempotent.

    Errors are logged-and-swallowed so a transient DB outage during
    backend boot doesn't crash the FastAPI process; the per-request
    ``get_conn()`` path will surface the failure when work actually
    needs the DB.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
        conn.close()
        _log.info("DDiQ tables initialized")
    except Exception as e:  # pragma: no cover — DB outage path
        _log.warning(f"DDiQ DB init skipped: {e}")


# ── Connection pool ──────────────────────────────────────────────────
# Every endpoint used to call psycopg2.connect() directly, paying the TCP +
# auth handshake cost (~5-50ms) per request. /report/generate opened ~20+
# connections in a single call. A single shared ThreadedConnectionPool
# eliminates the cost; a thin _PooledConn wrapper makes existing call sites
# (`conn.close()`) return the connection to the pool instead of really
# closing it, so we don't have to refactor every endpoint.


_pg_pool: Optional[ThreadedConnectionPool] = None


class _PooledConn:
    """Proxy a psycopg2 connection from the pool.

    ``close()`` returns it to the pool; everything else delegates to
    the underlying connection so existing code continues to work.
    """

    def __init__(self, conn: Any, pool: ThreadedConnectionPool) -> None:
        self.__dict__["_conn"] = conn
        self.__dict__["_pool"] = pool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Mirror writes onto the underlying connection.
        setattr(self._conn, name, value)

    def close(self) -> None:
        if self._conn is None or self._pool is None:
            return
        try:
            # If the txn is in a bad state, return aborted so the pool can
            # reset/discard the connection cleanly.
            if not self._conn.closed:
                self._pool.putconn(self._conn)
        finally:
            self.__dict__["_conn"] = None
            self.__dict__["_pool"] = None

    def __enter__(self) -> _PooledConn:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Mirror psycopg2 connection-as-context-manager semantics:
        # commit on clean exit, rollback on exception, then return to pool.
        try:
            if exc is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()


def init_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        return
    _pg_pool = ThreadedConnectionPool(
        minconn=int(os.getenv("DB_POOL_MIN", "2")),
        maxconn=int(os.getenv("DB_POOL_MAX", "20")),
        **DB_CONFIG,
    )
    _log.info(f"DDiQ DB pool: {_pg_pool.minconn}/{_pg_pool.maxconn} connections")


def close_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None


def get_conn() -> _PooledConn:
    """Return a connection from the pool (lazy-init the pool on first use).

    Call ``conn.close()`` as before — that returns it to the pool, not
    actually closes it. Use ``with get_conn() as conn:`` to also pick up
    auto-commit-on-success / rollback-on-exception.
    """
    if _pg_pool is None:
        init_pool()
    assert _pg_pool is not None  # for type checkers; init_pool sets it
    return _PooledConn(_pg_pool.getconn(), _pg_pool)


# ── Startup-time orphan reaper ───────────────────────────────────────


def reap_orphans() -> None:
    """Mark long-stuck queued/running reports as failed.

    H-4 (commit ``b87bf1f``): Celery's ``acks_late=True`` puts
    crashed-worker tasks back on the queue automatically — so a row
    in ``running`` state does NOT imply the work is lost. Could be:

      a) actively running on a worker (correct, leave alone),
      b) just-requeued waiting for a worker (correct, leave alone),
      c) genuinely stuck — the worker died before recording any
         progress AND the broker lost the message, OR the row was
         created before the Celery refactor landed.

    The reaper distinguishes (c) from (a)+(b) by AGE: anything older
    than 3 hours is well past the observed 60-min worst-case runtime
    AND past the Celery hard-time-limit of 120 min. Anything younger
    is left alone.

    The previous "fail everything not done at startup" behaviour was
    correct for the ThreadPoolExecutor design (threads literally died
    with the API process) but is wrong now — that would kill reports
    still being processed by a separate worker container.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE ddiq_reports
                       SET status = 'failed',
                           error = COALESCE(error, '') ||
                                   ' (reaper: stuck > 3h past started_at)',
                           finished_at = NOW()
                       WHERE status IN ('queued','running')
                         AND started_at IS NOT NULL
                         AND started_at < NOW() - INTERVAL '3 hours'"""
                )
                n = cur.rowcount
        if n:
            _log.warning(
                "reaped %d stuck report(s) older than 3h (started_at"
                " > Celery hard time limit)", n,
            )
    except Exception as e:  # pragma: no cover — DB outage path
        _log.warning(f"orphan reap failed: {e}")
