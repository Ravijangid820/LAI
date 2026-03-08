# LAI Project Status

> Last updated: 2026-03-07

This document explains the current state of the LAI project for developers joining the team.

---

## What Is LAI?

LAI (Legal AI) is a **German legal AI platform for wind energy due diligence**. It answers legal questions by searching a 600GB+ corpus of German laws, court decisions, and legal commentary, then generating cited answers using a locally-hosted LLM.

**Core principle:** Refuse to answer rather than hallucinate. The LLM formats retrieved context — it does not independently reason about law.

**Use case:** Wind park acquisition teams upload contracts, permits, and environmental reports. They ask questions like:
- "Was sind die Immissionsrichtwerte nach TA Larm fur ein Mischgebiet?"
- "Welche Genehmigungsvoraussetzungen gelten nach § 4 BImSchG?"
- "Vergleiche die Kundigungsklauseln in diesen zwei Pachtvertragen."

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| Package manager | `uv` |
| Web framework | FastAPI |
| Database | PostgreSQL 16 + pgvector (HNSW indexes) |
| Cache | Redis 7 |
| Object storage | MinIO |
| LLM | Qwen/Qwen2.5-7B-Instruct via vLLM |
| Embedding | BAAI/bge-m3 (1024 dims) via vLLM |
| Reranker | ms-marco-MiniLM-L-12-v2 via vLLM |
| Experiment tracking | MLflow |
| Monitoring | Prometheus + Grafana |

All ML models are self-hosted — no external API calls. GPU requirement: 2x GPUs (embedding + reranker share GPU 0, LLM on GPU 1).

---

## How It Works (RAG Pipeline)

```
User Query
    |
    v
1. Query Analysis -----> Rule-based: extract § refs, Art. refs, law codes, dates, intent
    |
    v
2. Embed Query --------> BGE-M3 via vLLM (cached in Redis)
    |
    v
3. Hybrid Search ------> Dense (pgvector, weight 0.6) + BM25 (tsvector, weight 0.4)
    |                     Fused with Reciprocal Rank Fusion (RRF)
    |                     Searches public schema + user's private schema
    v
4. Reranking ----------> Cross-encoder rescores top-100 → top-7
    |
    v
5. CRAG Grading -------> LLM grades each chunk relevant/irrelevant (temp=0.0)
    |                     If <2 relevant: rewrite query, re-retrieve (max 2 loops)
    v
6. LLM Generation -----> Qwen2.5-7B generates answer with context (temp=0.2)
    |
    v
7. Citation Verify -----> Regex extracts citations, matches against source chunks
    |
    v
8. Response ------------> Structured JSON with answer, citations, metadata
```

---

## Project Structure

```
/data/projects/lai/
├── LAI/                              # Application code
│   ├── src/lai/                      # Python packages (domain-driven)
│   │   ├── core/                     # Config, logging, exceptions, models, constants
│   │   ├── api/                      # FastAPI app, middleware, RAG pipeline orchestrator
│   │   ├── auth/                     # JWT auth, user CRUD, routes
│   │   ├── documents/                # Upload, parse, chunk, embed, store
│   │   ├── search/                   # Query analysis, hybrid search, reranking
│   │   ├── generation/               # LLM client, prompts, CRAG, citation verification
│   │   └── infra/                    # Database pool, Redis cache, MinIO client
│   ├── training/                     # Model fine-tuning (separate lifecycle)
│   ├── tests/                        # Unit, integration, E2E
│   ├── docs/                         # Documentation
│   └── pyproject.toml
│
└── Docker/                           # Containerized services (one dir per service)
    ├── database/
    │   ├── pgvector/                 # PostgreSQL + pgvector (port 5433)
    │   ├── minio/                    # Object storage (port 9000)
    │   └── redis/                    # Cache (port 6380)
    ├── embedding/                    # BGE-M3 via vLLM (port 8003, GPU 0)
    ├── reranker/                     # MiniLM via vLLM (port 8004, GPU 0)
    ├── llm/                          # Qwen2.5-7B via vLLM (port 8001, GPU 1)
    ├── mlflow/                       # Experiment tracking (port 5000)
    └── monitoring/                   # Prometheus (9090) + Grafana (3001)
```

### Why Domain-Driven?

Packages are organized by **business domain** (documents, search, generation, auth), not by technical layer (retrieval, ingestion, db). This means:

- Each developer owns a package end-to-end (routes → logic → repository)
- Merge conflicts are rare — you mostly work in your own package
- Each package has its own `routes.py` (API), logic files, and `repository.py` (DB)

---

## Team Ownership

| Package | Owner | What You Touch |
|---------|-------|---------------|
| `lai.documents` | Dev A | Document upload, parsing (Docling), chunking, embedding, storage |
| `lai.search` | Dev B | Query analysis, hybrid SQL search, reranker integration |
| `lai.generation` | Dev C | LLM client, prompt templates, CRAG loop, citation verification |
| `lai.auth` | Dev D | JWT tokens, user management, per-user schema creation |
| `lai.core` / `lai.infra` / `lai.api` | Shared | Config, DB pool, Redis, MinIO, pipeline orchestrator |

---

## Running the Project

### Prerequisites
- Python 3.13 + `uv`
- Docker + Docker Compose
- 2x GPUs (for ML services)

### 1. Start Docker services

```bash
# Create shared network (once)
docker network create lai_network

# Start infrastructure
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/redis && docker compose up -d

# Start ML services
cd /data/projects/lai/Docker/embedding && docker compose up -d
cd /data/projects/lai/Docker/reranker && docker compose up -d
cd /data/projects/lai/Docker/llm && docker compose up -d
```

### 2. Start the application

```bash
cd /data/projects/lai/LAI
uv sync
uv run python -m lai.api.main
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 3. Run tests

```bash
uv run pytest                    # All tests
uv run pytest tests/unit/        # Unit only
uv run pytest -m "not slow"      # Skip slow tests
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Ask a legal question (full RAG pipeline) |
| `POST` | `/documents/upload` | Upload PDF/DOCX (parse → chunk → embed → store) |
| `GET` | `/documents` | List user's uploaded documents |
| `DELETE` | `/documents/{id}` | Delete a document and its chunks |
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Get JWT tokens |
| `GET` | `/auth/me` | Current user info |
| `GET` | `/health` | Health check (DB + Redis status) |

---

## Multi-Tenancy

Each user gets a private PostgreSQL schema (`user_{uuid}`). When a user uploads documents, chunks are stored in their schema. When they query, the system searches both:
- **Public schema** — 600GB+ legal corpus (shared by all users)
- **User schema** — their uploaded documents (private)

Results are merged using RRF (Reciprocal Rank Fusion).

---

## Key Configuration

All settings are in `src/lai/core/config.py`, loaded from environment variables.

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `min_similarity` | 0.5 | Minimum vector similarity to return a chunk |
| `initial_k` | 100 | Chunks retrieved before reranking |
| `final_k` | 7 | Chunks after reranking (sent to LLM) |
| `llm_max_tokens` | 4096 | Max generation length |
| `chunk_size` | 512 tokens | Target chunk size |
| `chunk_overlap` | 100 tokens | Overlap between consecutive chunks |
| `crag.max_loops` | 2 | Max query-rewrite cycles |
| `crag.enabled` | true | Toggle CRAG grading on/off |

---

## Experiment Tracking (MLflow)

Every training run is logged to MLflow (http://localhost:5000):
- **Parameters:** model, learning rate, epochs, LoRA config, dataset
- **Metrics:** train/eval loss, NDCG, MRR, legal ref recall/precision
- **Artifacts:** model checkpoints, configs, evaluation reports

MLflow uses PostgreSQL for metadata and MinIO for artifact storage.

```bash
# Start MLflow
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/minio && docker compose up -d
cd /data/projects/lai/Docker/mlflow && docker compose up -d
# Open http://localhost:5000
```

---

## Versioning

- **Code versions:** Git tags (`v5.0.0`, `v5.1.0`) — no version directories
- **Model versions:** MLflow run IDs — every training run is logged
- **Feature flags:** Config toggles (`crag.enabled`, etc.) control what's active
- **Rollback:** `git checkout v5.0.0` for code, MLflow artifact download for models

---

## What's Done (v5.0.0)

- [x] Domain-driven package architecture
- [x] Full RAG pipeline (8 steps + CRAG loop)
- [x] Hybrid search (dense + BM25 + RRF)
- [x] Cross-encoder reranking
- [x] CRAG grading with query rewrite
- [x] Citation verification
- [x] Multi-tenancy (per-user schemas)
- [x] JWT authentication
- [x] Document upload pipeline (parse → chunk → embed → store)
- [x] Redis embedding cache
- [x] German legal query analyzer (regex-based)
- [x] Domain-specific prompt templates (5 variants)
- [x] Modular Docker services
- [x] MLflow experiment tracking
- [x] Prometheus + Grafana monitoring
- [x] Infrastructure documentation

## What's Next

See [LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) for the full roadmap. Priority items:

1. **Embedding backfill** — only ~1% of 19.2M chunks have embeddings
2. **BM25 population** — tsvector column is empty, sparse search doesn't work yet
3. **Integration tests** — no test coverage yet
4. **CI/CD pipeline** — no automated testing/deployment
5. **Database migrations** — no Alembic setup yet
6. **Re-chunking** — 66.9% of chunks are too large, need 800-1200 char target
7. **German reranker** — current MiniLM is English-only

---

## Known Issues

| Issue | Impact | Status |
|-------|--------|--------|
| Only ~1% chunks have embeddings | Search barely works | Needs backfill script |
| BM25 search_vector column empty | Hybrid search is dense-only | Needs SQL batch update |
| 66.9% chunks exceed size target | Retrieval quality degraded | Needs re-chunking |
| Reranker is English-only (MiniLM) | Suboptimal for German text | Evaluate German alternatives |
| Best model is checkpoint-400, not final | Using suboptimal weights | Redeploy correct checkpoint |
| contract + land domains have 0 training data | LLM weak on these topics | Generate training data |
| No CI/CD | Manual testing only | Set up GitHub Actions |

---

## Where to Find Things

| What | Where |
|------|-------|
| App config | [src/lai/core/config.py](../src/lai/core/config.py) |
| RAG pipeline | [src/lai/api/pipeline.py](../src/lai/api/pipeline.py) |
| Hybrid search SQL | [src/lai/search/hybrid_search.py](../src/lai/search/hybrid_search.py) |
| Prompt templates | [src/lai/generation/prompt_builder.py](../src/lai/generation/prompt_builder.py) |
| Docker services | [/data/projects/lai/Docker/](../../../Docker/) |
| Infrastructure docs | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
| Architecture overview | [architecture/overview.md](architecture/overview.md) |
| Improvement roadmap | [analysis/LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) |
| Project history (V1-V4) | [analysis/LAI_PROJECT_ANALYSIS.md](analysis/LAI_PROJECT_ANALYSIS.md) |
