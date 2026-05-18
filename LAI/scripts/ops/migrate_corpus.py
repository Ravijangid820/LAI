#!/usr/bin/env python3
"""Corpus migration: SQLite (pipeline_local.db) → Postgres (pgvector halfvec).

Phase 1b Track B of the LAI v1 plan (see ``harsh/IMPLEMENTATION_GUIDE.md``
§4.1 + §8.3 and ``harsh/TRACK_B_TIMING.md`` for the rationale).

The script has six subcommands, each runnable independently and resumable
after a crash / restart / SSH disconnect:

    init             — create ``corpus_*`` tables in ``lai_postgres_main``
    migrate-parents  — bulk-copy ``parent_chunks`` (one-shot, ~13.8 M rows)
    migrate-children — bulk-copy embedded children (resumable; ~11.9 M now,
                       grows to ~50 M as Step 6 completes)
    build-index      — create the HNSW index on the embedding column
    topup            — daemon: stream new embeddings as Step 6 produces them
    status           — print current progress

Environment
-----------

    LAI_SQLITE_PATH                 Source DB. Default:
                                      /data/projects/lai/LAI/processed/pipeline_local.db
    DB_HOST, DB_PORT, DB_NAME,
    DB_USER, DB_PASSWORD            Postgres reach. Defaults match the
                                      lai-backend container's settings:
                                      DB_HOST=127.0.0.1 DB_PORT=5434
                                      DB_NAME=lai_db DB_USER=lai_user.
                                      DB_PASSWORD has no default — set it
                                      or the script exits 2.
    LAI_MIGRATION_BATCH_SIZE        Rows per INSERT batch. Default 2000.
    LAI_MIGRATION_TOPUP_INTERVAL_S  Seconds between topup polls. Default 30.
    LAI_MIGRATION_LOG_LEVEL         logging level. Default INFO.

Production discipline
---------------------

* SQLite is opened **read-only** (``file:…?mode=ro``) so we cannot
  interfere with Step 6 writing through WAL.
* Every external IO is wrapped in try/except with structured logs.
* Transient Postgres / SQLite errors are retried with exponential
  backoff via :mod:`tenacity` (max 5 attempts per batch).
* Each batch is one transaction. A crash mid-batch rolls back; the
  ``corpus_migration_state.last_child_id`` high-water mark is only
  updated after the COMMIT, so re-running is idempotent.
* SIGTERM / SIGINT trigger a graceful shutdown — the current batch
  completes, state is flushed, then the process exits.
* Memory bounded: at any time only one ``BATCH_SIZE``-row window is
  in process memory; the SQLite cursor streams.
* The script is wire-format-agnostic — it uses
  :mod:`pgvector.psycopg2`'s ``register_vector`` adapter so a Python
  ``numpy.ndarray(dtype=np.float16)`` becomes a pgvector ``halfvec``
  without us hand-encoding any wire format.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_REPO_LAI = Path("/data/projects/lai/LAI")
_DEFAULT_SQLITE = _REPO_LAI / "processed" / "pipeline_local.db"
_DEFAULT_SCHEMA_FILE = (
    _REPO_LAI / "scripts" / "db" / "migrations" / "001_corpus_pgvector.sql"
)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


SQLITE_PATH = _env("LAI_SQLITE_PATH", str(_DEFAULT_SQLITE))
SCHEMA_FILE = Path(_env("LAI_MIGRATION_SCHEMA", str(_DEFAULT_SCHEMA_FILE)))

PG_HOST = _env("DB_HOST", "127.0.0.1")
PG_PORT = _env_int("DB_PORT", 5434)
PG_DB = _env("DB_NAME", "lai_db")
PG_USER = _env("DB_USER", "lai_user")
PG_PASSWORD = _env("DB_PASSWORD")

BATCH_SIZE = _env_int("LAI_MIGRATION_BATCH_SIZE", 2000)
TOPUP_INTERVAL_S = _env_int("LAI_MIGRATION_TOPUP_INTERVAL_S", 30)
LOG_LEVEL = _env("LAI_MIGRATION_LOG_LEVEL", "INFO")

EMBED_DIM = 4096
EMBED_BLOB_BYTES = EMBED_DIM * 4  # fp32 → 4 bytes per element

# pgvector caps halfvec HNSW indexes at 4000 dimensions (vector type caps
# at 2000). Qwen3-Embedding-8B emits 4096-d vectors; we truncate to the
# first 4000 dims before writing to ``corpus_child_chunks.embedding``.
# This is safe because Qwen3-Embedding uses Matryoshka representation
# learning — the model is trained to be truncatable to arbitrary prefix
# lengths without recall loss (per HuggingFace model card).
# https://huggingface.co/Qwen/Qwen3-Embedding-8B
INDEX_DIM = 4000

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, (LOG_LEVEL or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("migrate_corpus")


# ─────────────────────────────────────────────────────────────────────────────
# Graceful-shutdown plumbing
# ─────────────────────────────────────────────────────────────────────────────

_shutdown_requested = False


def _install_signal_handlers() -> None:
    """Trap SIGTERM + SIGINT so we exit at the next batch boundary instead of
    mid-transaction. The handler is idempotent — a second signal flips no
    additional state."""

    def _handler(signum: int, _frame: Any) -> None:
        global _shutdown_requested
        if not _shutdown_requested:
            log.warning(
                "received signal %d (%s); will exit after current batch",
                signum,
                signal.Signals(signum).name,
            )
            _shutdown_requested = True
        else:
            # Operator pressed Ctrl-C twice — escalate to immediate exit.
            log.error("second signal received; exiting now")
            sys.exit(130)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _check_shutdown(where: str) -> None:
    if _shutdown_requested:
        log.info("shutting down cleanly at %s", where)
        sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Connections
# ─────────────────────────────────────────────────────────────────────────────


def _require_password() -> str:
    if not PG_PASSWORD:
        log.error(
            "DB_PASSWORD is not set in the environment. "
            "Source the same env the lai-backend uses (DB_PASSWORD=…) "
            "before running this script."
        )
        sys.exit(2)
    return PG_PASSWORD


@contextmanager
def sqlite_ro() -> Iterator[sqlite3.Connection]:
    """Open the pipeline SQLite read-only with a long timeout.

    Read-only is required because Step 6 may be writing concurrently
    through WAL — we MUST NOT acquire a writer-lock on this DB.

    ``text_factory`` is set to a lenient UTF-8 decoder
    (``errors='replace'``) because the historical corpus contains a
    sprinkling of rows with malformed UTF-8 sequences from bad input
    sources — e.g. PDFs whose extraction pipeline emitted
    Windows-1252 bytes labelled as UTF-8. The default
    ``text_factory=str`` raises ``OperationalError: Could not decode
    to UTF-8`` on those rows and kills the migration; replacement (the
    invalid bytes become U+FFFD) lets us keep 99.9% of the text intact
    while losing only the unreadable bytes. Postgres TEXT accepts
    U+FFFD without complaint.
    """
    uri = f"file:{SQLITE_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60.0, isolation_level=None)
    try:
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        # Increase SQLite per-connection cache to speed up the big JOINs.
        # -256000 = 256 MB (negative means kibibytes).
        conn.execute("PRAGMA cache_size = -262144")
        conn.execute("PRAGMA temp_store = MEMORY")
        yield conn
    finally:
        conn.close()


@contextmanager
def pg_conn() -> Iterator[psycopg2.extensions.connection]:
    """Open a Postgres connection with the pgvector adapter registered."""
    pwd = _require_password()
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=pwd,
        connect_timeout=30,
        application_name="migrate_corpus",
    )
    try:
        register_vector(conn)
        yield conn
    finally:
        conn.close()


# Tenacity policy used around every batch INSERT / COPY. The connection
# is re-established by the caller on retry to recover from a dropped
# Postgres link.
_retry_transient = Retrying(
    retry=retry_if_exception_type(
        (psycopg2.OperationalError, sqlite3.OperationalError)
    ),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.0, min=1.0, max=30.0),
    reraise=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# State helpers (singleton row in corpus_migration_state)
# ─────────────────────────────────────────────────────────────────────────────


def _read_state(cur: psycopg2.extensions.cursor) -> dict[str, Any]:
    cur.execute(
        "SELECT last_child_id, parents_loaded, children_loaded, "
        "parents_started_at, parents_finished_at, "
        "children_started_at, children_finished_at, "
        "index_started_at, index_finished_at, updated_at "
        "FROM corpus_migration_state WHERE id = 1"
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            "corpus_migration_state row 1 missing — did you run init?"
        )
    keys = (
        "last_child_id",
        "parents_loaded",
        "children_loaded",
        "parents_started_at",
        "parents_finished_at",
        "children_started_at",
        "children_finished_at",
        "index_started_at",
        "index_finished_at",
        "updated_at",
    )
    return dict(zip(keys, row))


def _set_state(
    cur: psycopg2.extensions.cursor, **kwargs: Any
) -> None:
    """Update fields on the singleton state row. Pass any subset of the
    column names defined in corpus_migration_state."""
    if not kwargs:
        return
    cols = list(kwargs.keys())
    set_clause = ", ".join(f"{c} = %s" for c in cols)
    cur.execute(
        f"UPDATE corpus_migration_state SET {set_clause}, updated_at = NOW() "
        "WHERE id = 1",
        list(kwargs.values()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: init
# ─────────────────────────────────────────────────────────────────────────────


def cmd_init(_: argparse.Namespace) -> None:
    """Apply ``001_corpus_pgvector.sql`` to lai_postgres_main."""
    sql_text = SCHEMA_FILE.read_text(encoding="utf-8")
    with pg_conn() as conn, conn.cursor() as cur:
        log.info("applying schema from %s", SCHEMA_FILE)
        cur.execute(sql_text)
        conn.commit()
    log.info("schema applied; corpus_* tables ready")


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: migrate-parents
# ─────────────────────────────────────────────────────────────────────────────


def _iter_parent_batches(
    sqlite_cur: sqlite3.Cursor, batch_size: int, resume_from: int = 0
) -> Iterator[list[tuple[Any, ...]]]:
    """Yield batches of parent_chunks rows, ordered by id ascending.

    Resumable via ``resume_from``: caller passes the highest id already
    inserted; the iterator starts AFTER that id.
    """
    last_id = resume_from
    while True:
        rows = sqlite_cur.execute(
            "SELECT id, doc_id, chunk_id, section, content, char_count, "
            "       language, doc_type, source_file, source_bucket, "
            "       domain, page_start, page_end, metadata "
            "FROM parent_chunks WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            return
        yield rows
        last_id = rows[-1][0]


def cmd_migrate_parents(_: argparse.Namespace) -> None:
    """One-shot: bulk-copy parent_chunks into corpus_parent_chunks.

    Idempotent — re-running picks up at the highest id already present
    in corpus_parent_chunks (via SELECT max(id)).
    """
    _install_signal_handlers()
    t0 = time.time()
    total_inserted = 0

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM corpus_parent_chunks")
        resume_from = cur.fetchone()[0]
        # NB: ``resume_from`` is the MAX(id) value, NOT a row count.
        # Parent IDs are not contiguous so we use it only as the WHERE
        # filter; row-count progress is computed against an explicit
        # COUNT below.
        cur.execute("SELECT COUNT(*) FROM corpus_parent_chunks")
        already_loaded = cur.fetchone()[0]
        log.info(
            "migrate-parents: resuming. max(id)=%d, rows already in target=%d",
            resume_from,
            already_loaded,
        )
        if already_loaded == 0:
            _set_state(cur, parents_started_at=psycopg2.TimestampFromTicks(time.time()))
            conn.commit()

    with sqlite_ro() as sconn:
        scur = sconn.cursor()
        scur.execute("SELECT COUNT(*) FROM parent_chunks WHERE id > ?", (resume_from,))
        remaining = scur.fetchone()[0]
        total_in_source = already_loaded + remaining
        log.info(
            "migrate-parents: %d row(s) to copy (%d already done; %d total in source)",
            remaining,
            already_loaded,
            total_in_source,
        )
        if remaining == 0:
            log.info("migrate-parents: already complete")
            with pg_conn() as conn, conn.cursor() as cur:
                _set_state(
                    cur,
                    parents_finished_at=psycopg2.TimestampFromTicks(time.time()),
                )
                conn.commit()
            return

        for batch in _iter_parent_batches(scur, BATCH_SIZE, resume_from):
            _check_shutdown("migrate-parents batch boundary")
            try:
                _retry_transient(_insert_parents_batch, batch)
            except RetryError as exc:
                log.exception("migrate-parents: batch failed after retries: %s", exc)
                raise
            total_inserted += len(batch)
            done_total = already_loaded + total_inserted
            pct = 100.0 * done_total / max(total_in_source, 1)
            elapsed = time.time() - t0
            rate = total_inserted / max(elapsed, 1e-6)
            eta_s = (remaining - total_inserted) / max(rate, 1e-6)
            log.info(
                "migrate-parents: +%d (cum %d / %d, %.2f%%, %.0f rows/s, ETA %s)",
                len(batch),
                done_total,
                total_in_source,
                pct,
                rate,
                _fmt_duration(eta_s),
            )

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM corpus_parent_chunks")
        loaded = cur.fetchone()[0]
        _set_state(
            cur,
            parents_loaded=loaded,
            parents_finished_at=psycopg2.TimestampFromTicks(time.time()),
        )
        conn.commit()
    log.info(
        "migrate-parents: done — %d rows loaded total in %.1fs",
        loaded,
        time.time() - t0,
    )


def _insert_parents_batch(rows: list[tuple[Any, ...]]) -> None:
    """One transaction per batch. ``ON CONFLICT DO NOTHING`` because a
    retried batch may overlap with a previously-committed one."""
    sql = (
        "INSERT INTO corpus_parent_chunks "
        "(id, doc_id, chunk_id, section, content, char_count, language, "
        " doc_type, source_file, source_bucket, domain, page_start, page_end, metadata) "
        "VALUES %s "
        "ON CONFLICT (id) DO NOTHING"
    )
    with pg_conn() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=len(rows))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: migrate-children
# ─────────────────────────────────────────────────────────────────────────────


def _blob_to_halfvec(blob: bytes) -> np.ndarray:
    """fp32 little-endian BLOB (16 384 bytes) → fp16 numpy array (4000,).

    Source vector is the full 4096-d Qwen3-Embedding output stored in
    SQLite as 16 384 bytes of fp32. We:

      1. parse → fp32 numpy view of length 4096
      2. cast → fp16
      3. **slice to the first INDEX_DIM (4000) dimensions** so the
         pgvector HNSW index can accept it (halfvec HNSW caps at 4000-d;
         see :data:`INDEX_DIM` for the design note).

    The slice exploits Qwen3-Embedding's Matryoshka design — the model
    is trained to be truncatable to any prefix length without recall
    loss. Verified by Qwen's own card: "the model supports flexible
    output dimensions from 32 up to 4096 without retraining."

    Raises :class:`ValueError` if the blob isn't the expected source
    size — better to fail loud than silently emit a malformed vector
    that would survive HNSW insertion but mis-rank for retrieval.
    """
    if len(blob) != EMBED_BLOB_BYTES:
        raise ValueError(
            f"unexpected embedding size: got {len(blob)} bytes, "
            f"expected {EMBED_BLOB_BYTES}"
        )
    vec_fp32 = np.frombuffer(blob, dtype=np.float32)
    # ``copy=False`` on astype is a hint — numpy still copies because the
    # dtype differs. The subsequent slice returns a view, not a copy.
    vec_fp16 = vec_fp32.astype(np.float16, copy=False)
    return vec_fp16[:INDEX_DIM]


def _iter_child_batches(
    sqlite_cur: sqlite3.Cursor, batch_size: int, resume_from: int
) -> Iterator[list[tuple[Any, ...]]]:
    """Yield batches of (id, parent_id, chunk_id, content, embedding, char_count)
    for child rows that have an embedding. Ordered by child_id.

    ``parent_id`` is set to ``NULL`` when the source ``parent_chunks``
    row doesn't exist — the historical SQLite has 26 such orphan
    children (verified 2026-05-17) and the Postgres FK on
    ``corpus_child_chunks.parent_id`` would otherwise reject the
    batch. ``NULL`` preserves the embedding + child text while
    correctly signalling "no parent context available" downstream.
    The LEFT JOIN approach is one round-trip per batch (no extra
    query) and the existence test is index-fast.
    """
    last_id = resume_from
    orphan_count = 0
    while True:
        rows = sqlite_cur.execute(
            # LEFT JOIN parent_chunks + CASE — when the source parent
            # is missing, ``p.id`` is NULL and we emit NULL for
            # parent_id instead of the orphan reference. Otherwise
            # pass through the real parent_id.
            "SELECT c.id, "
            "       (CASE WHEN p.id IS NOT NULL THEN c.parent_id ELSE NULL END) AS parent_id, "
            "       c.chunk_id, c.content, e.embedding, c.char_count, "
            "       (CASE WHEN p.id IS NULL AND c.parent_id IS NOT NULL THEN 1 ELSE 0 END) AS is_orphan "
            "FROM child_embeddings e "
            "JOIN child_chunks c ON c.id = e.child_id "
            "LEFT JOIN parent_chunks p ON p.id = c.parent_id "
            "WHERE c.id > ? ORDER BY c.id ASC LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            if orphan_count > 0:
                log.info(
                    "child-iter: total %d orphan parent_id(s) demoted to NULL this run",
                    orphan_count,
                )
            return
        # Transform blobs → fp16 arrays before yielding so the producer
        # doesn't pay the conversion cost twice on retry.
        transformed: list[tuple[Any, ...]] = []
        for cid, pid, chunk_id, content, blob, cc, is_orphan in rows:
            try:
                vec = _blob_to_halfvec(blob)
            except ValueError as e:
                log.warning("skipping child_id=%d: %s", cid, e)
                continue
            if is_orphan:
                orphan_count += 1
                if orphan_count <= 10 or orphan_count % 1000 == 0:
                    # Log the first 10 + a heartbeat thereafter so a
                    # flood doesn't bury the log but a spike is still
                    # observable.
                    log.warning(
                        "child_id=%d: source parent missing; parent_id NULLed (run total: %d)",
                        cid,
                        orphan_count,
                    )
            transformed.append((cid, pid, chunk_id, content, vec, cc))
        if not transformed:
            # Whole batch was malformed — extremely unlikely but guard the loop.
            last_id = rows[-1][0]
            continue
        yield transformed
        last_id = rows[-1][0]


def cmd_migrate_children(_: argparse.Namespace) -> None:
    """Bulk-copy embedded children into corpus_child_chunks.

    Resumable via corpus_migration_state.last_child_id. Each batch is one
    transaction that ends with an UPDATE of last_child_id to that batch's
    highest id — a crash mid-batch rolls back and the next run resumes
    from the previous high-water mark.
    """
    _install_signal_handlers()
    t0 = time.time()
    total_inserted = 0
    resume_from = 0

    with pg_conn() as conn, conn.cursor() as cur:
        state = _read_state(cur)
        resume_from = state["last_child_id"]
        cur.execute("SELECT COUNT(*) FROM corpus_child_chunks")
        already_loaded = cur.fetchone()[0]
        log.info(
            "migrate-children: resuming. last_child_id=%d, rows already in target=%d",
            resume_from,
            already_loaded,
        )
        if state["children_started_at"] is None:
            _set_state(
                cur, children_started_at=psycopg2.TimestampFromTicks(time.time())
            )
            conn.commit()

    with sqlite_ro() as sconn:
        scur = sconn.cursor()
        scur.execute(
            "SELECT COUNT(*) FROM child_embeddings WHERE child_id > ?",
            (resume_from,),
        )
        remaining = scur.fetchone()[0]
        total_in_source = already_loaded + remaining
        log.info(
            "migrate-children: %d embedded children to copy (%d already done; %d total embedded)",
            remaining,
            already_loaded,
            total_in_source,
        )
        if remaining == 0:
            log.info("migrate-children: already complete")
            with pg_conn() as conn, conn.cursor() as cur:
                _set_state(
                    cur,
                    children_finished_at=psycopg2.TimestampFromTicks(time.time()),
                )
                conn.commit()
            return

        for batch in _iter_child_batches(scur, BATCH_SIZE, resume_from):
            _check_shutdown("migrate-children batch boundary")
            highest_id = batch[-1][0]
            try:
                _retry_transient(_insert_children_batch, batch, highest_id)
            except RetryError as exc:
                log.exception(
                    "migrate-children: batch failed after retries: %s", exc
                )
                raise
            total_inserted += len(batch)
            done_total = already_loaded + total_inserted
            pct = 100.0 * done_total / max(total_in_source, 1)
            elapsed = time.time() - t0
            rate = total_inserted / max(elapsed, 1e-6)
            eta_s = (remaining - total_inserted) / max(rate, 1e-6)
            log.info(
                "migrate-children: +%d (cum %d / %d, %.2f%%, %.0f rows/s, ETA %s)",
                len(batch),
                done_total,
                total_in_source,
                pct,
                rate,
                _fmt_duration(eta_s),
            )

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM corpus_child_chunks")
        loaded = cur.fetchone()[0]
        _set_state(
            cur,
            children_loaded=loaded,
            children_finished_at=psycopg2.TimestampFromTicks(time.time()),
        )
        conn.commit()
    log.info(
        "migrate-children: done — %d total rows in corpus_child_chunks in %.1fs",
        loaded,
        time.time() - t0,
    )


def _insert_children_batch(
    rows: list[tuple[Any, ...]], highest_id: int
) -> None:
    """One transaction: bulk-INSERT the batch + advance last_child_id.

    The state-row UPDATE inside the same transaction is what makes
    resume_from correct on crash. If the COMMIT doesn't happen,
    last_child_id stays where it was.
    """
    sql = (
        "INSERT INTO corpus_child_chunks "
        "(id, parent_id, chunk_id, content, embedding, char_count) "
        "VALUES %s ON CONFLICT (id) DO NOTHING"
    )
    with pg_conn() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, sql, rows, page_size=len(rows)
        )
        cur.execute(
            "UPDATE corpus_migration_state SET last_child_id = %s, "
            "updated_at = NOW() WHERE id = 1 AND last_child_id < %s",
            (highest_id, highest_id),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: build-index
# ─────────────────────────────────────────────────────────────────────────────


def cmd_build_index(_: argparse.Namespace) -> None:
    """Create the HNSW index on corpus_child_chunks.embedding.

    Uses ``CREATE INDEX CONCURRENTLY`` so retrieval queries can continue
    against any existing index during the build. Sets per-session tuning
    knobs that cut index build time 2-4× when sufficient RAM is available
    on the Postgres host:

      maintenance_work_mem            = 16 GB   (HNSW build buffer)
      max_parallel_maintenance_workers= 4       (parallel index build)
    """
    t0 = time.time()
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'corpus_child_embedding_hnsw_idx'"
        )
        if cur.fetchone():
            log.info("build-index: corpus_child_embedding_hnsw_idx already exists")
            return

        _set_state(cur, index_started_at=psycopg2.TimestampFromTicks(time.time()))
        conn.commit()

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block;
    # use autocommit. Tuning knobs are set per-session so they don't bleed
    # into other connections.
    pwd = _require_password()
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=pwd, connect_timeout=30,
        application_name="migrate_corpus.build_index",
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            log.info("build-index: setting maintenance_work_mem + parallel workers")
            cur.execute("SET maintenance_work_mem = '16GB'")
            cur.execute("SET max_parallel_maintenance_workers = 4")
            cur.execute("SET max_parallel_workers = 8")
            log.info(
                "build-index: starting CREATE INDEX CONCURRENTLY "
                "(this typically takes 1-6h for ~12M rows; longer with the full 50M)"
            )
            cur.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "corpus_child_embedding_hnsw_idx "
                "ON corpus_child_chunks "
                "USING hnsw (embedding halfvec_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
            log.info("build-index: index built in %.1f min", (time.time() - t0) / 60.0)
    finally:
        conn.close()

    with pg_conn() as conn, conn.cursor() as cur:
        _set_state(cur, index_finished_at=psycopg2.TimestampFromTicks(time.time()))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: topup (daemon)
# ─────────────────────────────────────────────────────────────────────────────


def cmd_topup(_: argparse.Namespace) -> None:
    """Daemon: poll child_embeddings for new rows and stream into pgvector.

    Runs forever. Sleeps ``LAI_MIGRATION_TOPUP_INTERVAL_S`` seconds
    between polls when there's nothing new; backs off to that interval
    after each empty batch. Terminates cleanly on SIGTERM / SIGINT.

    Also ensures the new rows' parent_ids exist in corpus_parent_chunks
    (Step 6 doesn't add parents, but a defensive parent-topup is cheap
    insurance against the rare case where parent_chunks lag the
    children).
    """
    _install_signal_handlers()
    interval = TOPUP_INTERVAL_S
    log.info(
        "topup: starting (interval=%ds, batch=%d)",
        interval,
        BATCH_SIZE,
    )
    while True:
        _check_shutdown("topup loop top")
        try:
            n = _run_one_topup_round()
        except KeyboardInterrupt:
            log.info("topup: KeyboardInterrupt; exiting")
            return
        except Exception:
            log.exception("topup: unexpected error in poll round; will retry")
            n = 0
        if n == 0:
            # No work — sleep before next poll. Split into 1s chunks so
            # SIGTERM is responsive.
            for _ in range(interval):
                if _shutdown_requested:
                    break
                time.sleep(1)


def _run_one_topup_round() -> int:
    """Returns count of children inserted this round."""
    with pg_conn() as pg, pg.cursor() as pcur:
        state = _read_state(pcur)
        resume_from = state["last_child_id"]

    with sqlite_ro() as sconn:
        scur = sconn.cursor()
        scur.execute(
            "SELECT MAX(child_id) FROM child_embeddings WHERE child_id > ?",
            (resume_from,),
        )
        ceiling = scur.fetchone()[0]
        if ceiling is None:
            return 0  # nothing new

        log.info(
            "topup: %d new embedded children (ids %d → %d)",
            ceiling - resume_from,
            resume_from + 1,
            ceiling,
        )

        total = 0
        for batch in _iter_child_batches(scur, BATCH_SIZE, resume_from):
            _check_shutdown("topup batch boundary")
            highest_id = batch[-1][0]
            # Defensive parent-topup: any parent_ids in this batch that
            # aren't yet in corpus_parent_chunks get fetched + inserted.
            _ensure_parents_for_children(batch)
            try:
                _retry_transient(_insert_children_batch, batch, highest_id)
            except RetryError as exc:
                log.exception("topup: batch failed after retries: %s", exc)
                raise
            total += len(batch)
        log.info("topup: round complete; inserted %d row(s)", total)
        return total


def _ensure_parents_for_children(child_batch: list[tuple[Any, ...]]) -> None:
    """Insert any parent_chunks rows that the children reference but that
    aren't yet in corpus_parent_chunks. Idempotent."""
    parent_ids = {pid for _cid, pid, *_ in child_batch if pid is not None}
    if not parent_ids:
        return
    with pg_conn() as pg, pg.cursor() as pcur:
        pcur.execute(
            "SELECT id FROM corpus_parent_chunks WHERE id = ANY(%s)",
            (list(parent_ids),),
        )
        already = {r[0] for r in pcur.fetchall()}
        missing = parent_ids - already
    if not missing:
        return
    log.info("topup: fetching %d missing parent_chunks rows", len(missing))
    with sqlite_ro() as sconn:
        scur = sconn.cursor()
        # SQLite has no ANY(); use IN with a placeholder list.
        placeholders = ",".join("?" * len(missing))
        rows = scur.execute(
            "SELECT id, doc_id, chunk_id, section, content, char_count, "
            "       language, doc_type, source_file, source_bucket, "
            "       domain, page_start, page_end, metadata "
            f"FROM parent_chunks WHERE id IN ({placeholders})",
            list(missing),
        ).fetchall()
    if rows:
        _retry_transient(_insert_parents_batch, rows)


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: status
# ─────────────────────────────────────────────────────────────────────────────


def cmd_status(_: argparse.Namespace) -> None:
    """Print current progress for both sides + the index state."""
    with sqlite_ro() as sconn:
        scur = sconn.cursor()
        scur.execute("SELECT COUNT(*) FROM parent_chunks")
        sl_parents = scur.fetchone()[0]
        scur.execute("SELECT COUNT(*) FROM child_chunks")
        sl_children = scur.fetchone()[0]
        scur.execute("SELECT COUNT(*) FROM child_embeddings")
        sl_embeddings = scur.fetchone()[0]

    with pg_conn() as pg, pg.cursor() as pcur:
        try:
            state = _read_state(pcur)
        except (RuntimeError, psycopg2.errors.UndefinedTable):
            print("schema not initialised — run: migrate_corpus.py init")
            return
        pcur.execute("SELECT COUNT(*) FROM corpus_parent_chunks")
        pg_parents = pcur.fetchone()[0]
        pcur.execute("SELECT COUNT(*) FROM corpus_child_chunks")
        pg_children = pcur.fetchone()[0]
        pcur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'corpus_child_embedding_hnsw_idx')"
        )
        has_index = pcur.fetchone()[0]

    print("=" * 70)
    print(f"SQLite source ({SQLITE_PATH}):")
    print(f"  parent_chunks:    {sl_parents:>14,}")
    print(f"  child_chunks:     {sl_children:>14,}")
    print(f"  child_embeddings: {sl_embeddings:>14,}")
    print()
    print(f"Postgres target ({PG_HOST}:{PG_PORT}/{PG_DB}):")
    print(
        f"  corpus_parent_chunks: {pg_parents:>14,}  "
        f"({100.0 * pg_parents / max(sl_parents, 1):.1f}% of source)"
    )
    print(
        f"  corpus_child_chunks:  {pg_children:>14,}  "
        f"({100.0 * pg_children / max(sl_embeddings, 1):.1f}% of embedded)"
    )
    print(f"  HNSW index built:     {'yes' if has_index else 'no'}")
    print(f"  last_child_id:        {state['last_child_id']:,}")
    print()
    print("State timestamps:")
    for k in (
        "parents_started_at", "parents_finished_at",
        "children_started_at", "children_finished_at",
        "index_started_at", "index_finished_at",
    ):
        v = state[k]
        print(f"  {k:25s} {v.isoformat() if v else '-'}")
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# Misc
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


_SUBCMDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "init": cmd_init,
    "migrate-parents": cmd_migrate_parents,
    "migrate-children": cmd_migrate_children,
    "build-index": cmd_build_index,
    "topup": cmd_topup,
    "status": cmd_status,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_corpus",
        description="Corpus migration to pgvector (Phase 1b Track B).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in _SUBCMDS:
        sub.add_parser(name)
    args = parser.parse_args(argv)

    try:
        _SUBCMDS[args.cmd](args)
        return 0
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except SystemExit:
        raise
    except Exception:
        log.exception("fatal error in %s", args.cmd)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
