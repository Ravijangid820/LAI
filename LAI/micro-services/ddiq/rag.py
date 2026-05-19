"""Retrieval helpers for the DDiQ pipeline (H-5 phase 2).

Pgvector search + reranker call + the two context-rendering helpers
(:func:`rag_context` / :func:`rag_context_with_meta`) plus
:func:`evidence_from_chunks` (resolve LLM-cited chunk indices back to
:class:`~ddiq.models.Evidence` records).

Layering:

* Depends on :mod:`ddiq.db` (``get_conn``) and :mod:`ddiq.llm`
  (``embed_single`` + ``RERANKER_URL``).
* Depends on :mod:`ddiq.models` for the :class:`Evidence` shape.
* No dependency on ``ddiq_report`` — safe to import from anywhere.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import psycopg2.extras
import requests

from ddiq.db import get_conn
from ddiq.llm import RERANKER_URL, embed_single
from ddiq.models import Evidence

__all__ = [
    "evidence_from_chunks",
    "get_all_text_for_docs",
    "rag_context",
    "rag_context_with_meta",
    "rerank",
    "search_doc_chunks",
]


_log = logging.getLogger("ddiq")


# ── DB-backed retrieval ──────────────────────────────────────────────


def search_doc_chunks(
    doc_ids: list[str],
    query_embedding: list[float],
    top_k: int = 15,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pgvector search over ``doc_ids`` chunks, scoped to ``user_id``.

    When ``user_id`` is supplied (every protected route does), the
    join filter also enforces tenant isolation at the SQL layer — even
    if a caller bypassed
    :func:`ddiq_report._assert_owns_documents`, no chunks belonging to
    another user can leak.
    """
    if not doc_ids:
        return []
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    ph = ",".join(["%s"] * len(doc_ids))
    user_clause = " AND d.user_id = %s" if user_id is not None else ""
    sql = f"""SELECT c.text, c.doc_id, d.filename,
              1-(c.embedding<=>%s::vector) AS similarity
              FROM ddiq_doc_chunks c JOIN ddiq_documents d ON d.id=c.doc_id
              WHERE c.doc_id::text IN ({ph})
              AND c.embedding IS NOT NULL{user_clause}
              ORDER BY c.embedding<=>%s::vector LIMIT %s"""
    params: tuple[Any, ...] = (emb_str, *doc_ids)
    if user_id is not None:
        params = (*params, str(user_id))
    params = (*params, emb_str, top_k)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_all_text_for_docs(
    doc_ids: list[str],
    user_id: Optional[str] = None,
) -> str:
    """Concatenate ``full_text`` from the given documents, scoped to ``user_id``."""
    conn = get_conn()
    cur = conn.cursor()
    ph = ",".join(["%s"] * len(doc_ids))
    if user_id is not None:
        cur.execute(
            f"SELECT full_text FROM ddiq_documents "
            f"WHERE id::text IN ({ph}) AND user_id = %s",
            (*doc_ids, str(user_id)),
        )
    else:
        cur.execute(
            f"SELECT full_text FROM ddiq_documents WHERE id::text IN ({ph})",
            tuple(doc_ids),
        )
    texts = [row[0] for row in cur.fetchall() if row[0]]
    cur.close()
    conn.close()
    return "\n\n---\n\n".join(texts)


# ── Reranker call ────────────────────────────────────────────────────


def rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Re-rank ``chunks`` by relevance to ``query``.

    Falls back to ``chunks[:top_k]`` on any reranker failure — losing
    the rerank step is an acceptable downgrade compared to crashing
    the pipeline mid-report.
    """
    texts = [c["text"] for c in chunks]
    try:
        resp = requests.post(
            f"{RERANKER_URL}/rerank",
            json={"query": query, "texts": texts, "truncate": True},
            timeout=30,
        )
        resp.raise_for_status()
        ranked = sorted(resp.json(), key=lambda x: x["score"], reverse=True)[:top_k]
        return [chunks[item["index"]] for item in ranked]
    except Exception:
        return chunks[:top_k]


# ── Context rendering ────────────────────────────────────────────────


def rag_context(
    doc_ids: list[str],
    question: str,
    top_k: int = 5,
) -> str:
    """Run the embed → vector-search → rerank pipeline and render text
    chunks ready to paste into an LLM prompt. Single string return —
    callers that need chunk metadata for evidence use
    :func:`rag_context_with_meta` instead.
    """
    emb = embed_single(question)
    chunks = search_doc_chunks(doc_ids, emb, top_k=20)
    if not chunks:
        return "(No relevant content found)"
    reranked = rerank(question, chunks, top_k=top_k)
    return "\n\n".join(
        [f"[Doc: {c.get('filename','?')}]\n{c['text'][:800]}" for c in reranked]
    )


def rag_context_with_meta(
    doc_ids: list[str],
    question: str,
    top_k: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """Same retrieval as :func:`rag_context`, but also returns the chunk
    metadata so callers can attach :class:`Evidence` pointers
    (``{doc_id, doc_filename, excerpt}``) to whatever facts the LLM
    extracts. Format mirrors :func:`rag_context` with a ``[#1]``,
    ``[#2]`` ... numbering so the LLM can cite chunks back by index,
    which we then resolve to :class:`Evidence`.
    """
    emb = embed_single(question)
    chunks = search_doc_chunks(doc_ids, emb, top_k=20)
    if not chunks:
        return "(No relevant content found)", []
    reranked = rerank(question, chunks, top_k=top_k)
    parts = []
    for i, c in enumerate(reranked, 1):
        parts.append(f"[#{i} | Doc: {c.get('filename','?')}]\n{c['text'][:800]}")
    return "\n\n".join(parts), reranked


def evidence_from_chunks(
    reranked: list[dict[str, Any]],
    indices: list[Any],
) -> list[Evidence]:
    """Resolve LLM-cited chunk indices (1-based) to :class:`Evidence`
    records. Tolerates strings (``'1'``, ``'#1'``, ``'chunk_1'``) and
    out-of-range silently.
    """
    out: list[Evidence] = []
    if not reranked:
        return out
    for idx in indices or []:
        try:
            n = int(re.sub(r"[^0-9]", "", str(idx)))
        except Exception:
            continue
        if 1 <= n <= len(reranked):
            c = reranked[n-1]
            out.append(Evidence(
                doc_id=str(c.get("doc_id")) if c.get("doc_id") else None,
                doc_filename=c.get("filename"),
                excerpt=(c.get("text", "") or "")[:300],
            ))
    return out
