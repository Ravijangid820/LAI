"""Hybrid dense + sparse search with RRF fusion.

Executes search in one or more PostgreSQL schemas (public + user_*)
using pgvector HNSW for dense and tsvector for BM25.
"""

from lai.core.config import get_settings
from lai.core.exceptions import RetrievalError
from lai.core.logging import get_logger, trace_operation
from lai.infra.database import get_pool

logger = get_logger("lai.search.hybrid_search")


async def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int | None = None,
    filters: dict | None = None,
    schema: str = "public",
) -> list[dict]:
    """Execute hybrid dense+sparse search with RRF fusion in a single schema."""
    settings = get_settings().retrieval
    pool = get_pool()
    final_k = top_k or settings.final_k
    initial_k = settings.initial_k
    rrf_k = settings.rrf_k
    dense_w = settings.dense_weight
    sparse_w = settings.sparse_weight
    table = f"{schema}.chunks"

    where_clauses: list[str] = []
    params: list = [query_embedding, query_text, initial_k, initial_k]
    idx = 5

    if filters:
        if filters.get("law_refs"):
            where_clauses.append(f"law_refs && ${idx}")
            params.append(filters["law_refs"])
            idx += 1
        if filters.get("doc_types"):
            where_clauses.append(f"doc_type = ANY(${idx})")
            params.append(filters["doc_types"])
            idx += 1
        if filters.get("is_current_only"):
            where_clauses.append("is_current = true")

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    query = f"""
    WITH dense AS (
        SELECT id, 1 - (embedding <=> $1) AS score
        FROM {table} {where_sql}
        ORDER BY embedding <=> $1
        LIMIT $3
    ),
    sparse AS (
        SELECT id, ts_rank_cd(search_vector, plainto_tsquery('german', $2)) AS score
        FROM {table} {where_sql}
        ORDER BY ts_rank_cd(search_vector, plainto_tsquery('german', $2)) DESC
        LIMIT $4
    ),
    dense_ranked AS (
        SELECT id, score AS dense_score, ROW_NUMBER() OVER (ORDER BY score DESC) AS rank FROM dense
    ),
    sparse_ranked AS (
        SELECT id, score AS sparse_score, ROW_NUMBER() OVER (ORDER BY score DESC) AS rank FROM sparse
    ),
    fused AS (
        SELECT
            COALESCE(d.id, s.id) AS id,
            d.dense_score, s.sparse_score,
            COALESCE({dense_w} / (d.rank + {rrf_k}), 0) + COALESCE({sparse_w} / (s.rank + {rrf_k}), 0) AS rrf_score
        FROM dense_ranked d
        FULL OUTER JOIN sparse_ranked s ON d.id = s.id
    )
    SELECT
        c.id, c.document_id, c.user_id,
        c.text_clean, c.text_tagged,
        c.section, c.subsection, c.chunk_index,
        c.paragraph_refs, c.article_refs, c.law_refs,
        c.doc_type, c.court_level,
        c.effective_date, c.decision_date, c.is_current,
        c.entities, c.metadata,
        f.dense_score, f.sparse_score, f.rrf_score
    FROM fused f
    JOIN {table} c ON c.id = f.id
    ORDER BY f.rrf_score DESC
    LIMIT {final_k};
    """

    async with trace_operation("hybrid_search", extra={"schema": schema, "top_k": final_k}) as ctx:
        try:
            async with pool.acquire() as conn:
                await conn.execute(f"SET hnsw.ef_search = {settings.hnsw_ef_search}")
                rows = await conn.fetch(query, *params)
        except Exception as e:
            logger.error("Hybrid search failed in schema %s: %s", schema, e)
            raise RetrievalError(f"Search failed: {e}") from e

    results = []
    for rank, row in enumerate(rows, 1):
        results.append({
            "chunk_id": row["id"],
            "document_id": row["document_id"],
            "user_id": row["user_id"],
            "text_clean": row["text_clean"],
            "text_tagged": row["text_tagged"],
            "section": row["section"],
            "subsection": row["subsection"],
            "chunk_index": row["chunk_index"],
            "paragraph_refs": row["paragraph_refs"] or [],
            "article_refs": row["article_refs"] or [],
            "law_refs": row["law_refs"] or [],
            "doc_type": row["doc_type"],
            "court_level": row["court_level"],
            "effective_date": row["effective_date"],
            "decision_date": row["decision_date"],
            "is_current": row["is_current"],
            "entities": row["entities"] or {},
            "metadata": row["metadata"] or {},
            "dense_score": float(row["dense_score"]) if row["dense_score"] else None,
            "sparse_score": float(row["sparse_score"]) if row["sparse_score"] else None,
            "hybrid_score": float(row["rrf_score"]),
            "final_rank": rank,
        })

    logger.info("Hybrid search returned %d results from %s", len(results), schema)
    return results


async def hybrid_search_multi_schema(
    query_embedding: list[float],
    query_text: str,
    schemas: list[str],
    top_k: int | None = None,
    filters: dict | None = None,
) -> list[dict]:
    """Search across multiple schemas and re-rank with cross-schema RRF."""
    settings = get_settings().retrieval
    final_k = top_k or settings.final_k

    if len(schemas) == 1:
        results = await hybrid_search(query_embedding, query_text, final_k, filters, schemas[0])
        for r in results:
            r["source_schema"] = schemas[0]
        return results

    all_candidates: list[dict] = []
    per_schema_k = final_k * 3
    for schema in schemas:
        results = await hybrid_search(query_embedding, query_text, per_schema_k, filters, schema)
        for r in results:
            r["source_schema"] = schema
        all_candidates.extend(results)

    if not all_candidates:
        return []

    # Re-rank across schemas
    rrf_k = settings.rrf_k
    dense_sorted = sorted(all_candidates, key=lambda x: x.get("dense_score") or 0, reverse=True)
    for rank, item in enumerate(dense_sorted, 1):
        item["_dr"] = rank
    sparse_sorted = sorted(all_candidates, key=lambda x: x.get("sparse_score") or 0, reverse=True)
    for rank, item in enumerate(sparse_sorted, 1):
        item["_sr"] = rank
    for item in all_candidates:
        item["hybrid_score"] = settings.dense_weight / (item["_dr"] + rrf_k) + settings.sparse_weight / (item["_sr"] + rrf_k)

    all_candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)
    top = all_candidates[:final_k]
    for rank, item in enumerate(top, 1):
        item.pop("_dr", None)
        item.pop("_sr", None)
        item["final_rank"] = rank

    logger.info("Multi-schema search: %d schemas, %d total candidates, %d returned", len(schemas), len(all_candidates), len(top))
    return top
