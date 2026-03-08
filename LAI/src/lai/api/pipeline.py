"""RAG pipeline orchestrator.

Wires search → generation domains into the full RAG flow:
1. Query analysis
2. Embedding
3. Hybrid search (multi-schema)
4. Reranking
5. CRAG grading (optional)
6. LLM generation
7. Citation verification
"""

import time

from lai.core.config import get_settings
from lai.core.exceptions import EmptyRetrievalError
from lai.core.logging import get_logger, trace_operation
from lai.documents.embedder import get_embedder
from lai.generation.citation_verifier import get_citation_verifier
from lai.generation.crag import filter_relevant, grade_chunks, rewrite_query
from lai.generation.llm_client import get_llm_client
from lai.generation.prompt_builder import build_prompt, build_refusal
from lai.search.hybrid_search import hybrid_search_multi_schema
from lai.search.query_analyzer import QueryAnalyzer
from lai.search.reranker import get_reranker
from lai.search.repository import get_user_schemas

logger = get_logger("lai.api.pipeline")
_analyzer = QueryAnalyzer()


async def run_rag_pipeline(
    query: str,
    user_id: str | None = None,
    top_k: int | None = None,
    filters: dict | None = None,
) -> dict:
    """Execute the full RAG pipeline and return a structured response."""
    start = time.perf_counter()
    settings = get_settings()

    async with trace_operation("rag_pipeline") as ctx:
        # 1. Query analysis
        parsed = _analyzer.analyze(query)
        logger.info("Query intent: %s, law_codes: %s", parsed.intent.value, parsed.law_codes)

        # Build filters from parsed query
        search_filters = filters or {}
        if parsed.law_codes and "law_refs" not in search_filters:
            search_filters["law_refs"] = parsed.law_codes

        # 2. Embed query
        embedder = get_embedder()
        query_embedding = await embedder.embed(parsed.normalized_text or query)

        # 3. Determine schemas
        schemas = ["public"]
        if user_id:
            schemas = await get_user_schemas(user_id)

        # 4. Hybrid search
        chunks = await hybrid_search_multi_schema(
            query_embedding=query_embedding,
            query_text=query,
            schemas=schemas,
            top_k=settings.retrieval.initial_k,
            filters=search_filters,
        )

        if not chunks:
            raise EmptyRetrievalError("No chunks found for query")

        # 5. Reranking
        reranker = get_reranker()
        chunks = reranker.rerank(query, chunks, top_k=top_k or settings.retrieval.final_k)

        # 6. CRAG grading + potential query rewrite
        if settings.crag.enabled:
            crag_loop = 0
            while crag_loop < settings.crag.max_loops:
                chunks = await grade_chunks(query, chunks)
                relevant = filter_relevant(chunks)

                if len(relevant) >= settings.crag.min_relevant_chunks:
                    chunks = relevant
                    break

                # Rewrite and re-retrieve
                query = await rewrite_query(query)
                query_embedding = await embedder.embed(query)
                chunks = await hybrid_search_multi_schema(query_embedding, query, schemas, top_k=settings.retrieval.initial_k, filters=search_filters)
                chunks = reranker.rerank(query, chunks, top_k=top_k or settings.retrieval.final_k)
                crag_loop += 1
                logger.info("CRAG loop %d: rewritten query, re-retrieved", crag_loop)

        if not chunks:
            raise EmptyRetrievalError("No relevant chunks after CRAG filtering")

        # 7. LLM generation
        system_prompt, user_message = build_prompt(query, chunks, parsed)
        llm = get_llm_client()
        llm_response = llm.generate_safe(system_prompt, user_message)

        if not llm_response.success:
            answer = build_refusal("LLM-Dienst nicht verfügbar")
            citations = []
        else:
            # 8. Citation verification
            verifier = get_citation_verifier()
            verification = verifier.verify(llm_response.text, chunks)

            if verification.passed:
                answer = llm_response.text
                citations = [{"text": c.text, "status": c.status} for c in verification.citations]
            else:
                answer = build_refusal(f"Nicht verifizierte Zitate: {', '.join(verification.unverified[:3])}")
                citations = [{"text": c.text, "status": c.status} for c in verification.citations]

    latency = (time.perf_counter() - start) * 1000
    logger.info("RAG pipeline completed in %.1fms (chunks=%d)", latency, len(chunks))

    return {
        "answer": answer,
        "citations": citations,
        "chunks_used": len(chunks),
        "query_intent": parsed.intent.value,
        "latency_ms": round(latency, 1),
    }
