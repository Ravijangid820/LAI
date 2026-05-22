"""Sync pgvector retrieval client for the migrated corpus.

Replaces the in-RAM numpy mat-mul in ``lai.search.eval`` (the 144 GB
``Corpus.embs`` matrix) with HNSW ANN queries against
``corpus_child_chunks.embedding halfvec(4000)`` in Postgres.

Why sync-primary (not async like :class:`lai.common.embedding.EmbeddingClient`):
the only consumers — ``serve_rag`` route handlers and the DDiQ engine —
are synchronous functions that FastAPI runs in its threadpool. A
thread-safe :class:`psycopg2.pool.ThreadedConnectionPool` matches that
execution model exactly; introducing asyncpg would add a dependency and
an event-loop bridge for zero current benefit. An async variant can be
added alongside this one later if a fully-async caller appears, mirroring
the embedding package's dual-class layout.

Distance metric: the HNSW index is built ``USING hnsw (embedding
halfvec_cosine_ops)``, so the cosine-distance operator ``<=>`` is what
hits the index. Similarity returned to callers is ``1 - distance`` so
"higher is better" matches the reranker's convention.

Query-vector handling mirrors the migration's write path
(``migrate_corpus._blob_to_halfvec``): truncate to the first
:data:`~lai.common.retrieval.config.INDEX_DIM` (4000) dimensions — safe
because Qwen3-Embedding is Matryoshka-trained — and bind as a
``::halfvec`` literal so the operand types match the indexed column.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any

import psycopg2
import psycopg2.pool
from pydantic import BaseModel

from lai.common.exceptions import (
    RetrievalConnectionError,
    RetrievalDimensionError,
    RetrievalError,
    RetrievalQueryError,
    RetrievalRetryExhaustedError,
)
from lai.common.retrieval.config import RetrievalConfig
from lai.common.retrieval.metrics import RetrievalMetrics, default_retrieval_metrics

__all__ = ["RetrievalClient", "RetrievedChunk", "RetrievedMatterChunk"]


class RetrievedMatterChunk(BaseModel):
    """One passage retrieved from a Matter's uploaded documents.

    Unlike :class:`RetrievedChunk` (the shared legal corpus), matter
    chunks are scoped to a single session ("Matter") and carry the
    provenance the citation UI needs: which uploaded document
    (``doc_index`` → the ``[M-n]`` document, openable as a PDF) and which
    page the passage was OCR'd from.
    """

    id: int
    doc_index: int
    filename: str
    page: int | None
    content: str
    similarity: float


class RetrievedChunk(BaseModel):
    """One child chunk returned by a dense search.

    Attributes:
        child_id: ``corpus_child_chunks.id``.
        parent_id: ``corpus_child_chunks.parent_id`` — ``None`` for the
            handful of orphan children the migration NULLed (no parent
            context available).
        content: The child chunk text (the embedded passage).
        similarity: Cosine similarity in ``[-1, 1]``, computed as
            ``1 - (embedding <=> query)``. Higher is more relevant.
    """

    child_id: int
    parent_id: int | None
    content: str
    similarity: float


def _format_halfvec_literal(vector: Sequence[float], index_dim: int) -> str:
    """Truncate to ``index_dim`` and format as a pgvector text literal.

    Returns a string like ``[0.1,0.2,...]`` suitable for binding with a
    ``::halfvec`` cast. Truncation mirrors the migration's write path so
    the query operand occupies the same Matryoshka prefix the index was
    built on.

    Raises:
        RetrievalDimensionError: If the vector is shorter than
            ``index_dim`` — we can pad neither meaningfully nor safely,
            so a too-short vector is a hard configuration error.
    """
    n = len(vector)
    if n < index_dim:
        raise RetrievalDimensionError(
            f"query vector has {n} dims but the index expects at least "
            f"{index_dim}; cannot truncate up",
            expected=index_dim,
            actual=n,
        )
    truncated = vector[:index_dim]
    # ``repr(float)`` round-trips precisely; pgvector parses it and casts
    # to fp16 on the way into the halfvec comparison.
    return "[" + ",".join(repr(float(x)) for x in truncated) + "]"


class RetrievalClient:
    """Thread-safe sync pgvector retrieval client.

    Holds a :class:`psycopg2.pool.ThreadedConnectionPool` opened lazily on
    first use. Construct once at process start (e.g. in serve_rag's
    lifespan) and share across requests — the pool serialises access so a
    single instance is safe to call from many threads.

    Example::

        client = RetrievalClient()
        hits = client.dense_search(query_vector, top_k=30)
        for h in hits:
            print(h.child_id, h.similarity, h.content[:80])
    """

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        *,
        metrics: RetrievalMetrics | None = None,
        max_retries: int = 2,
    ) -> None:
        """Build the client.

        Args:
            config: Settings. Defaults to :class:`RetrievalConfig` read
                from the environment.
            metrics: Prometheus bundle. Defaults to the module-level
                :data:`default_retrieval_metrics`.
            max_retries: Number of *additional* attempts on a transient
                connection error (total attempts = ``max_retries + 1``).
                Query-level errors (bad SQL, dimension mismatch) are NOT
                retried.

        Raises:
            ValueError: If ``max_retries`` is negative.
        """
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self._config = config if config is not None else RetrievalConfig()
        self._metrics = metrics if metrics is not None else default_retrieval_metrics
        self._max_retries = max_retries
        self._pool: psycopg2.pool.ThreadedConnectionPool | None = None
        self._pool_lock = threading.Lock()

    # ── Pool lifecycle ───────────────────────────────────────────────────

    def _ensure_pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """Lazily open the connection pool (double-checked locking)."""
        if self._pool is not None:
            return self._pool
        with self._pool_lock:
            if self._pool is not None:
                return self._pool
            cfg = self._config
            try:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=cfg.pool_min_size,
                    maxconn=cfg.pool_max_size,
                    host=cfg.host,
                    port=cfg.port,
                    dbname=cfg.dbname,
                    user=cfg.user,
                    password=cfg.password.get_secret_value(),
                    connect_timeout=cfg.connect_timeout_s,
                    application_name="lai_retrieval",
                )
            except psycopg2.Error as exc:
                raise RetrievalConnectionError(
                    f"failed to open pgvector connection pool to "
                    f"{cfg.host}:{cfg.port}/{cfg.dbname}: {exc}",
                ) from exc
            return self._pool

    def close(self) -> None:
        """Close all pooled connections. Idempotent."""
        with self._pool_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

    def __enter__(self) -> RetrievalClient:
        self._ensure_pool()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── Health ───────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Cheap connectivity check — ``SELECT 1``. Returns True on success.

        Used by serve_rag's ``/health`` to report retrieval-backend
        reachability. Never raises; a failure returns False so the health
        probe can degrade gracefully rather than 500.
        """
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except (RetrievalError, psycopg2.Error):
            return False

    # ── Connection borrow/return ─────────────────────────────────────────

    class _BorrowedConn:
        """Context manager that returns the connection to the pool on exit.

        Rolls back any open transaction before returning so a failed query
        never leaves a poisoned connection in the pool.
        """

        def __init__(self, client: RetrievalClient) -> None:
            self._client = client
            self._conn: Any = None

        def __enter__(self) -> Any:
            pool = self._client._ensure_pool()
            try:
                self._conn = pool.getconn()
            except psycopg2.pool.PoolError as exc:
                self._client._metrics.pool_exhausted_total.inc()
                raise RetrievalConnectionError(
                    f"connection pool exhausted: {exc}",
                ) from exc
            return self._conn

        def __exit__(self, exc_type: object, *_rest: object) -> None:
            if self._conn is None:
                return
            pool = self._client._pool
            if pool is None:
                return
            try:
                # Always roll back: a successful SELECT leaves an idle
                # transaction open under psycopg2's default autocommit=off,
                # and an exception may have left an aborted one. Either way
                # we want a clean connection back in the pool.
                self._conn.rollback()
            except psycopg2.Error:
                # Connection is unusable — discard it rather than return.
                pool.putconn(self._conn, close=True)
                self._conn = None
                return
            pool.putconn(self._conn)
            self._conn = None

    def _borrow(self) -> RetrievalClient._BorrowedConn:
        return RetrievalClient._BorrowedConn(self)

    # ── Dense search ─────────────────────────────────────────────────────

    def dense_search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int | None = None,
        ef_search: int | None = None,
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` nearest child chunks by cosine similarity.

        Args:
            query_vector: The query embedding. Must be at least
                ``config.index_dim`` (4000) dimensions; truncated to the
                Matryoshka prefix the index was built on.
            top_k: Number of chunks to return. Defaults to
                ``config.default_top_k``.
            ef_search: pgvector ``hnsw.ef_search`` override for this query.
                Defaults to ``config.hnsw_ef_search``. Higher = better
                recall, slower.

        Returns:
            Child chunks ordered by descending similarity.

        Raises:
            RetrievalDimensionError: Query vector too short.
            RetrievalQueryError: Non-transient SQL failure.
            RetrievalRetryExhaustedError: Transient connection errors
                exhausted the retry budget.
        """
        cfg = self._config
        k = top_k if top_k is not None else cfg.default_top_k
        ef = ef_search if ef_search is not None else cfg.hnsw_ef_search
        if k < 1:
            raise RetrievalQueryError(f"top_k must be >= 1, got {k}")

        # Format (and dimension-check) the query vector BEFORE entering the
        # retry loop — a dimension error is deterministic and must not be
        # retried.
        vec_literal = _format_halfvec_literal(query_vector, cfg.index_dim)

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._max_retries:
            attempt += 1
            if attempt > 1:
                self._metrics.retries_total.inc()
            t0 = time.perf_counter()
            try:
                rows = self._run_dense_query(vec_literal, k, ef)
            except RetrievalConnectionError as exc:
                # Transient — retry until budget exhausted.
                last_exc = exc
                continue
            except RetrievalQueryError:
                # Non-transient — record the failure metric and re-raise.
                self._metrics.queries_total.labels(status="error").inc()
                self._metrics.query_duration_seconds.labels(
                    status="error",
                ).observe(time.perf_counter() - t0)
                raise
            else:
                elapsed = time.perf_counter() - t0
                self._metrics.queries_total.labels(status="success").inc()
                self._metrics.query_duration_seconds.labels(
                    status="success",
                ).observe(elapsed)
                self._metrics.rows_returned.observe(len(rows))
                return rows

        # Retry budget exhausted on transient errors.
        self._metrics.queries_total.labels(status="error").inc()
        raise RetrievalRetryExhaustedError(
            f"dense_search failed after {attempt} attempt(s): {last_exc}",
            attempts=attempt,
        ) from last_exc

    def _run_dense_query(
        self, vec_literal: str, k: int, ef: int,
    ) -> list[RetrievedChunk]:
        """Execute one dense ANN query. Maps psycopg2 errors to our types."""
        cfg = self._config
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    # Per-query session knobs. ef_search drives HNSW recall;
                    # statement_timeout bounds a pathological scan.
                    cur.execute("SET LOCAL hnsw.ef_search = %s", (ef,))
                    if cfg.statement_timeout_ms > 0:
                        cur.execute(
                            "SET LOCAL statement_timeout = %s",
                            (cfg.statement_timeout_ms,),
                        )
                    cur.execute(
                        "SELECT id, parent_id, content, "
                        "       1 - (embedding <=> %s::halfvec) AS similarity "
                        "FROM corpus_child_chunks "
                        "ORDER BY embedding <=> %s::halfvec "
                        "LIMIT %s",
                        (vec_literal, vec_literal, k),
                    )
                    fetched = cur.fetchall()
        except psycopg2.OperationalError as exc:
            # Connection-level: server gone, timeout, dropped socket.
            raise RetrievalConnectionError(
                f"pgvector query connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            # SQL-level: missing table/extension, type error, bad cast.
            raise RetrievalQueryError(
                f"pgvector query failed: {exc}",
            ) from exc

        return [
            RetrievedChunk(
                child_id=int(r[0]),
                parent_id=int(r[1]) if r[1] is not None else None,
                content=r[2] or "",
                similarity=float(r[3]),
            )
            for r in fetched
        ]

    # ── Parent-text fetch (for rerank context) ───────────────────────────

    def fetch_parent_texts(self, parent_ids: Sequence[int]) -> dict[int, str]:
        """Return ``{parent_id: content}`` for the given parents.

        The dense search returns child chunks; the RAG prompt and the
        reranker want the *parent* passage (the larger unit). This batches
        the lookup into one ``= ANY(...)`` query. Missing parents are
        simply absent from the returned dict.

        Args:
            parent_ids: Parent ids to fetch. Duplicates and ``None`` are
                ignored.

        Raises:
            RetrievalQueryError: Non-transient SQL failure.
            RetrievalRetryExhaustedError: Transient errors exhausted retries.
        """
        unique_ids = sorted({int(p) for p in parent_ids if p is not None})
        if not unique_ids:
            return {}

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._max_retries:
            attempt += 1
            if attempt > 1:
                self._metrics.retries_total.inc()
            try:
                return self._run_parent_query(unique_ids)
            except RetrievalConnectionError as exc:
                last_exc = exc
                continue
            except RetrievalQueryError:
                raise
        raise RetrievalRetryExhaustedError(
            f"fetch_parent_texts failed after {attempt} attempt(s): {last_exc}",
            attempts=attempt,
        ) from last_exc

    def fetch_children_by_id(
        self, child_ids: Sequence[int],
    ) -> dict[int, RetrievedChunk]:
        """Return ``{child_id: RetrievedChunk}`` for the given child ids.

        Used to hydrate BM25-only hybrid candidates — child chunks that
        BM25 (FTS5) surfaced but the dense ANN query did not, so they
        carry no ``parent_id`` / ``content`` yet. ``similarity`` on the
        returned chunks is ``0.0`` (not computed; these came from the
        lexical side). Missing ids are simply absent from the dict.

        Args:
            child_ids: Child ids to fetch. Duplicates ignored.

        Raises:
            RetrievalQueryError: Non-transient SQL failure.
            RetrievalRetryExhaustedError: Transient errors exhausted retries.
        """
        unique_ids = sorted({int(c) for c in child_ids})
        if not unique_ids:
            return {}

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._max_retries:
            attempt += 1
            if attempt > 1:
                self._metrics.retries_total.inc()
            try:
                return self._run_children_query(unique_ids)
            except RetrievalConnectionError as exc:
                last_exc = exc
                continue
            except RetrievalQueryError:
                raise
        raise RetrievalRetryExhaustedError(
            f"fetch_children_by_id failed after {attempt} attempt(s): {last_exc}",
            attempts=attempt,
        ) from last_exc

    def _run_children_query(self, child_ids: list[int]) -> dict[int, RetrievedChunk]:
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, parent_id, content FROM corpus_child_chunks "
                        "WHERE id = ANY(%s)",
                        (child_ids,),
                    )
                    fetched = cur.fetchall()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"pgvector child fetch connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"pgvector child fetch failed: {exc}",
            ) from exc
        return {
            int(r[0]): RetrievedChunk(
                child_id=int(r[0]),
                parent_id=int(r[1]) if r[1] is not None else None,
                content=r[2] or "",
                similarity=0.0,
            )
            for r in fetched
        }

    def _run_parent_query(self, parent_ids: list[int]) -> dict[int, str]:
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, content FROM corpus_parent_chunks "
                        "WHERE id = ANY(%s)",
                        (parent_ids,),
                    )
                    fetched = cur.fetchall()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"pgvector parent fetch connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"pgvector parent fetch failed: {exc}",
            ) from exc
        return {int(r[0]): (r[1] or "") for r in fetched}

    # ── Matter (per-session uploaded-document) index ─────────────────────
    #
    # A "data room" is just a small private corpus scoped to one session.
    # We store its passages in ``matter_chunks`` and retrieve with EXACT
    # KNN filtered by ``session_id`` — not the shared HNSW index the corpus
    # uses. Rationale: a single Matter holds at most a few thousand chunks,
    # so an exact scan over just that session's rows is both fast (<50 ms)
    # and perfect-recall, whereas an HNSW index shared across all sessions
    # would post-filter by session_id and silently lose recall. The btree
    # on ``session_id`` keeps each query's scan confined to one Matter.

    def ensure_matter_table(self) -> None:
        """Create ``matter_chunks`` and its session index if absent.

        Idempotent; safe to call at every startup (mirrors the corpus
        migration's CREATE ... IF NOT EXISTS pattern)."""
        ddl = (
            "CREATE TABLE IF NOT EXISTS matter_chunks ("
            "  id          BIGSERIAL PRIMARY KEY,"
            "  session_id  TEXT NOT NULL,"
            "  doc_index   INT  NOT NULL,"
            "  filename    TEXT,"
            "  page        INT,"
            "  ord         INT  NOT NULL,"
            "  content     TEXT NOT NULL,"
            "  embedding   halfvec(4000) NOT NULL,"
            "  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()"
            ");",
            "CREATE INDEX IF NOT EXISTS idx_matter_chunks_session "
            "  ON matter_chunks(session_id);",
        )
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    for stmt in ddl:
                        cur.execute(stmt)
                conn.commit()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"ensure_matter_table connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"ensure_matter_table failed: {exc}",
            ) from exc

    def index_matter_document(
        self,
        session_id: str,
        doc_index: int,
        filename: str,
        passages: Sequence[tuple[int | None, str, Sequence[float]]],
    ) -> int:
        """(Re)index one uploaded document's passages for a Matter.

        ``passages`` is ``[(page, content, embedding), ...]`` in document
        order. Any existing rows for this ``(session_id, doc_index)`` are
        deleted first, so re-uploading a document is idempotent. Embeddings
        are truncated to the index dimension (Matryoshka prefix) to match
        the corpus write path. Returns the number of rows inserted.
        """
        cfg = self._config
        rows = [
            (
                session_id,
                doc_index,
                filename,
                page,
                ordinal,
                content,
                _format_halfvec_literal(emb, cfg.index_dim),
            )
            for ordinal, (page, content, emb) in enumerate(passages)
        ]
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM matter_chunks WHERE session_id = %s AND doc_index = %s",
                        (session_id, doc_index),
                    )
                    if rows:
                        cur.executemany(
                            "INSERT INTO matter_chunks "
                            "(session_id, doc_index, filename, page, ord, content, embedding) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s::halfvec)",
                            rows,
                        )
                conn.commit()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"index_matter_document connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"index_matter_document failed: {exc}",
            ) from exc
        return len(rows)

    def matter_dense_search(
        self,
        session_id: str,
        query_vector: Sequence[float],
        *,
        top_k: int | None = None,
    ) -> list[RetrievedMatterChunk]:
        """Exact-KNN search over ONE Matter's uploaded passages.

        Scoped to ``session_id`` so a query never leaks across matters.
        Returns passages ordered by descending cosine similarity.
        """
        cfg = self._config
        k = top_k if top_k is not None else cfg.default_top_k
        if k < 1:
            raise RetrievalQueryError(f"top_k must be >= 1, got {k}")
        vec_literal = _format_halfvec_literal(query_vector, cfg.index_dim)

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._max_retries:
            attempt += 1
            if attempt > 1:
                self._metrics.retries_total.inc()
            try:
                return self._run_matter_query(session_id, vec_literal, k)
            except RetrievalConnectionError as exc:
                last_exc = exc
                continue
            except RetrievalQueryError:
                raise
        raise RetrievalRetryExhaustedError(
            f"matter_dense_search failed after {attempt} attempt(s): {last_exc}",
            attempts=attempt,
        ) from last_exc

    def _run_matter_query(
        self, session_id: str, vec_literal: str, k: int,
    ) -> list[RetrievedMatterChunk]:
        cfg = self._config
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    if cfg.statement_timeout_ms > 0:
                        cur.execute(
                            "SET LOCAL statement_timeout = %s",
                            (cfg.statement_timeout_ms,),
                        )
                    cur.execute(
                        "SELECT id, doc_index, filename, page, content, "
                        "       1 - (embedding <=> %s::halfvec) AS similarity "
                        "FROM matter_chunks "
                        "WHERE session_id = %s "
                        "ORDER BY embedding <=> %s::halfvec "
                        "LIMIT %s",
                        (vec_literal, session_id, vec_literal, k),
                    )
                    fetched = cur.fetchall()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"matter query connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"matter query failed: {exc}",
            ) from exc
        return [
            RetrievedMatterChunk(
                id=int(r[0]),
                doc_index=int(r[1]),
                filename=r[2] or "",
                page=int(r[3]) if r[3] is not None else None,
                content=r[4] or "",
                similarity=float(r[5]),
            )
            for r in fetched
        ]

    def delete_matter_chunks(
        self, session_id: str, doc_index: int | None = None,
    ) -> int:
        """Delete a Matter's indexed passages (whole session, or one doc).

        Called when a document or session is deleted so the pgvector index
        doesn't outlive the source files. Returns rows deleted."""
        try:
            with self._borrow() as conn:
                with conn.cursor() as cur:
                    if doc_index is None:
                        cur.execute(
                            "DELETE FROM matter_chunks WHERE session_id = %s",
                            (session_id,),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM matter_chunks WHERE session_id = %s AND doc_index = %s",
                            (session_id, doc_index),
                        )
                    deleted = cur.rowcount
                conn.commit()
        except psycopg2.OperationalError as exc:
            raise RetrievalConnectionError(
                f"delete_matter_chunks connection error: {exc}",
            ) from exc
        except psycopg2.Error as exc:
            raise RetrievalQueryError(
                f"delete_matter_chunks failed: {exc}",
            ) from exc
        return int(deleted)
