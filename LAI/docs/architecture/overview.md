# LAI v2 - Architecture Overview

## What LAI Does

LAI is a German legal AI platform for wind energy due diligence. It answers legal questions by searching a 600GB+ corpus of German law, court decisions, and legal commentary, then generating cited answers using a locally-hosted LLM.

**Core principle:** Refuse to answer rather than hallucinate. The LLM formats retrieved context - it does not independently reason about law.

---

## System Architecture

```
                    +-------------------+
                    |    FastAPI API     |  <- User queries, document uploads
                    |  (services/api)   |
                    +--------+----------+
                             |
                    +--------v----------+
                    |  RAG Pipeline     |
                    |  (Orchestrator)   |
                    +--------+----------+
                             |
          +------------------+------------------+
          |                  |                  |
   +------v------+   +------v------+   +------v------+
   |   Retrieval  |   | Generation  |   |  Ingestion  |
   | hybrid_search|   | llm_client  |   |   chunker   |
   | reranker     |   | prompts     |   |   embedder  |
   | query_analyzer|  | citation    |   |   parser    |
   +------+------+   +------+------+   +------+------+
          |                  |                  |
   +------v------------------v------------------v------+
   |                    Database                       |
   |  PostgreSQL + pgvector (HNSW) + BM25 (tsvector)  |
   |  Multi-tenancy: public + user_{uuid} schemas      |
   +---------------------------------------------------+
```

## Packages (Domain-Driven)

`src/lai/` is an installable package (`uv sync` / `pip install -e .`). Each
subpackage is one domain with its own `README.md`; review ownership is in
[`.github/CODEOWNERS`](../../../.github/CODEOWNERS) (owner column below uses
its team scheme).

| Package | Owner | Purpose | Key Files |
|---------|-------------|---------|-----------|
| `lai.core` | platform | Config, models, logging, utils, exceptions, constants | config.py, models.py, logging.py, constants.py, utils.py, exceptions.py |
| `lai.infra` | platform | Infrastructure clients: DB pool, MinIO, Redis | database.py, minio.py, redis.py |
| `lai.api` | platform | FastAPI app shell + `serve_rag.py` (the :18000 chat backend) | main.py, serve_rag.py, pipeline.py |
| `lai.auth` | platform | Auth: JWT, users, sessions, per-user schema creation | jwt.py, repository.py, routes.py |
| `lai.documents` | ingestion | Document ingestion: parsing, chunking, embedding, CRUD | chunker.py, embedder.py, parser.py, repository.py, routes.py |
| `lai.extraction` | ingestion | Location/geo extraction from legal docs | location.py, models.py, repository.py, routes.py |
| `lai.search` | retrieval | Hybrid search, reranking, query analysis + `eval.py` (retrieval eval) | hybrid_search.py, query_analyzer.py, reranker.py, eval.py, repository.py, routes.py |
| `lai.generation` | generation | LLM: prompt building, CRAG grading, citation verification | llm_client.py, prompt_builder.py, citation_verifier.py, crag.py |
| `lai.analyzer` | contract-analyzer | Qwen3.6-27B contract analyzer — playbooks, prompts, schema | pipeline.py, playbooks.py, prompts.py, schema.py, reconciler.py |
| `lai.pipeline` | data-pipeline | The 6-step corpus build (`python -m lai.pipeline.cli`) | cli.py, convert.py, chunk.py, classify.py, enrich.py, generate.py, embed.py |

## RAG Pipeline (8 steps + CRAG loop)

```
1. Query Analysis       Rule-based: extract SS refs, Art. refs, law codes, dates, intent
2. Metadata Filter      Build SQL filters from parsed query
3. Hybrid Retrieval     Dense (0.6) + BM25 (0.4) + RRF fusion, initial_k=100
4. Reranking            Cross-encoder rescoring, top-100 -> top-7
5. CRAG Grading         LLM grades each chunk (temp=0.0, "ja"/"nein")
                        If <2 relevant: rewrite query, re-retrieve (max 2 loops)
6. Generation           LLM generates answer with context (temp=0.2, max 4096 tokens)
7. Citation Verify      Regex + exact match against source chunks
8. Response Format      Structured output with citations + metadata
```

## Models

| Component | Model | Hosting | Details |
|-----------|-------|---------|---------|
| LLM | Qwen/Qwen2.5-7B-Instruct | vLLM (GPU 1) | temp=0.2, max 4096 tokens |
| Embedding | BAAI/bge-m3 | vLLM (GPU 0) | 1024 dims, cached in Redis |
| Reranker | ms-marco-MiniLM-L-12-v2 | vLLM (GPU 0) | top-100 -> top-7 |

## Infrastructure

| Service | Image | Port |
|---------|-------|------|
| PostgreSQL + pgvector | pgvector/pgvector:pg16 | 5433 |
| Redis | redis:7-alpine | 6380 |
| MinIO | minio/minio:latest | 9002 |
| Embedding (BGE-M3) | vllm/vllm-openai:latest | 8003 |
| Reranker | vllm/vllm-openai:latest | 8004 |
| LLM (Qwen2.5-7B) | vllm/vllm-openai:latest | 8001 |
| API | custom Dockerfile | 8000 |
| Worker | custom Dockerfile | - |
| Prometheus | prom/prometheus | 9090 |
| Grafana | grafana/grafana | 3000 |

## Multi-Tenancy

Each user gets an isolated PostgreSQL schema (`user_{uuid}`) for uploaded documents.
The retriever searches both the public schema (600GB legal corpus) and the user's
personal schema, merging results via RRF.

## Key Parameters

| Parameter | Value | Why |
|-----------|-------|-----|
| Min similarity | 0.5 | Eliminates ~80% noise (raised from V4's 0.3) |
| Final K | 7 | German legal text needs more context (raised from V3's 5) |
| LLM max tokens | 4096 | Full answers for complex legal questions (raised from 2048) |
| Chunk size | 512 tokens | Balanced context per chunk |
| Chunk overlap | 100 tokens | German compound words need more overlap (raised from 50) |
| CRAG max loops | 2 | Bound latency while improving retrieval quality |
| Session expiry | 7 days | Legal research spans multiple days (raised from 1) |
| HNSW ef_search | 100 | Good recall without excessive latency |

## Logging

All logging uses `lai.core.logging`:
- **Development:** Human-readable structured format
- **Production:** JSON output for log aggregation
- **Tracing:** `trace_operation()` context manager captures per-step timing and token usage
- **Noise reduction:** Third-party loggers (httpx, asyncpg, uvicorn) set to WARNING

```python
from lai.core.logging import get_logger, trace_operation

logger = get_logger("lai.retrieval.hybrid_search")

async with trace_operation("hybrid_search", request_id) as ctx:
    results = await search(query)
    ctx.record_tokens(usage)
    logger.info("Found %d results", len(results))
# ctx.metrics now has duration_ms, token_usage, success
```

## Error Handling

Exception hierarchy in `lai.core.exceptions`:
```
LAIError
  +-- ServiceUnavailableError
  |     +-- EmbeddingError
  |     +-- LLMError
  |     +-- RerankerError
  +-- RetrievalError
  |     +-- EmptyRetrievalError
  +-- DatabaseError
  |     +-- SchemaError
  +-- DocumentProcessingError
  |     +-- UnsupportedFormatError
  |     +-- FileTooLargeError
  +-- InputValidationError
        +-- QueryTooLongError
```
