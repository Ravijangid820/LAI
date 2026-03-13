# LAI Project Status

> Last updated: 2026-03-10

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
| LLM (inference) | Qwen/Qwen2.5-7B-Instruct via vLLM |
| LLM (pipeline) | Qwen/Qwen2.5-72B-Instruct-AWQ via vLLM (tensor-parallel, 2 GPUs) |
| Embedding | Qwen/Qwen3-Embedding-8B (1024 dims) via vLLM |
| Reranker | ms-marco-MiniLM-L-12-v2 via vLLM |
| Experiment tracking | MLflow |
| Monitoring | Prometheus + Grafana |

All ML models are self-hosted — no external API calls. Hardware: 2x RTX Pro 6000 GPUs (96GB VRAM each).

---

## How It Works (RAG Pipeline)

```
User Query
    |
    v
1. Query Analysis -----> Rule-based: extract § refs, Art. refs, law codes, dates, intent
    |
    v
2. Embed Query --------> Qwen3-Embedding-8B via vLLM (cached in Redis)
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
│   │   ├── infra/                    # Database pool, Redis cache, MinIO client
│   │   └── pipeline/                 # Data processing pipeline (Steps 1-6)
│   ├── training/                     # Model fine-tuning (separate lifecycle)
│   ├── tests/                        # Unit, integration, E2E
│   ├── logs/pipeline/                # Auto-generated logs per step
│   │   ├── step1/                   #   step1_<source>_<timestamp>.log
│   │   ├── step2/                   #   step2_<timestamp>.log
│   │   └── ...
│   ├── docs/                         # Documentation
│   └── pyproject.toml
│
└── Docker/                           # Containerized services (one dir per service)
    ├── database/
    │   ├── pgvector/                 # PostgreSQL + pgvector (port 5434)
    │   ├── minio/                    # Object storage (port 9000)
    │   └── redis/                    # Cache (port 6380)
    ├── embedding/                    # Qwen3-Embedding-8B via vLLM (port 8003, GPU 0)
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
| `chunking.parent_target_chars` | 3072 | Parent chunk target (fine-tuning context) |
| `chunking.parent_max_chars` | 6144 | Parent chunk max |
| `chunking.child_target_chars` | 1536 | Child chunk target (RAG retrieval) |
| `chunking.child_overlap_chars` | 384 | Overlap between child chunks |
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

## Data Processing Pipeline (v2)

The `lai.pipeline` package processes the 672GB raw corpus into both RAG-ready embeddings and fine-tuning data.

### Data Sources (MinIO `lai-raw`, 672GB)

| Source | Size | Files | Value |
|--------|------|-------|-------|
| multilegalpile | 643GB | 132K | ~96% non-German, filtered to `de` only |
| hf_cases | 14GB | 14K | German court decisions |
| openlegaldata | 1.5GB | 4K | Overlaps ~30-50% with hf_cases |
| VDRs | 6GB | 4.3K | **HIGH** — wind park data rooms |
| Library | 5.4GB | 2.1K | PDFs |
| de/gesetzes | 750MB | 764 | German statutes |
| DD Reports | 19MB | 18 | **HIGH** — due diligence reports |

### Pipeline Steps

```bash
# All steps via: python -m lai.pipeline.cli stepN [--dry-run] [--batch-size N]
```

| Step | Module | Input | Output | Engine |
|------|--------|-------|--------|--------|
| 1 | `convert.py` | Raw files (MinIO) | Normalized segments (MinIO) | Docling, custom parsers |
| 2 | `chunk.py` | Segments | Parent + child chunks (PostgreSQL) | German-aware splitting |
| 3 | `classify.py` | Parent chunks | Domain labels (12 domains) | Qwen2.5-72B |
| 4 | `enrich.py` | Child chunks | Context prefix per chunk | Qwen2.5-72B |
| 5 | `generate.py` | Parent chunks | ~200K ChatML training samples | Qwen2.5-72B |
| 6 | `embed.py` | Child chunks | 1024-dim vectors + tsvector | Qwen3-Embedding-8B |

### Chunking Strategy (German legal text, ~3 chars/token)

- **Parent chunks:** 3072 target, 6144 max chars (1024-2048 tokens) — for fine-tuning context + domain classification
- **Child chunks:** 1536 target, 1800 max, 384 overlap chars (~512 tokens) — for RAG retrieval with embeddings

### Legal Domains (12)

immissionsschutzrecht, energierecht, baurecht, umweltrecht, vertragsrecht, gesellschaftsrecht, grundstuecksrecht, arbeitsrecht, steuerrecht, verwaltungsrecht, prozessrecht, allgemein

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
- [x] Data processing pipeline v2 (6-step, `lai.pipeline`)
- [x] Parent-child chunking (German-aware, dual-purpose for RAG + fine-tuning)
- [x] Domain classification module (12 wind-energy legal domains)
- [x] Contextual enrichment (Anthropic's approach for child chunks)
- [x] Synthetic fine-tuning data generation (~200K target, 7 task types)
- [x] Embedding pipeline (Qwen3-Embedding-8B, 1024 dims)
- [x] Upgraded embedding model: BGE-M3 → Qwen3-Embedding-8B
- [x] Switched OCR from RapidOCR (Chinese PP-OCR) to Tesseract with German language pack
- [x] German legal OCR post-processing ($ → §, hyphenation fixes)
- [x] Automatic file logging for all pipeline steps (`LAI/logs/pipeline/<step>/`)
- [x] Graceful shutdown with signal handling (first Ctrl+C finishes work, second force-exits)
- [x] Step 2 parallelized (ThreadPoolExecutor + batch `execute_values` inserts, atomic DB writes)
- [x] DB config fix: default port 5433 → 5434 (main PostgreSQL container)

---

## Pipeline Execution Progress (Phase 1)

Processing is done in phases due to storage constraints (~613GB free).

### Phase 1 — High-value sources (~20GB)

| Source | Step 1 (Convert) | Step 2 (Chunk) | Step 3 (Classify) | Step 4 (Enrich) | Step 5 (Generate) | Step 6 (Embed) |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| DD Reports (19MB, 18 files) | Done (16 ok, 1 .DOC fail, 2 corrupt) | In progress | - | - | - | - |
| VDRs (6GB, 4.3K files) | Done (5,469 ok, 103 failed) | In progress | - | - | - | - |
| de/gesetzes (750MB, 764 files) | Done | In progress | - | - | - | - |

### Phase 2 — Medium sources (~20GB)

| Source | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 | Step 6 |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| hf_cases (14GB, 14K files) | - | - | - | - | - | - |
| openlegaldata (1.5GB, 4K files) | - | - | - | - | - | - |
| Library (5.4GB, 2.1K PDFs) | - | - | - | - | - | - |

### Phase 3 — Large source (~30-50GB German subset)

| Source | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 | Step 6 |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| multilegalpile (643GB, filter to `de`) | - | - | - | - | - | - |

---

## What's Next

Priority items:

1. **Complete Phase 1 Steps 2-6** — finish chunking, classify, enrich, generate, embed on DD Reports + VDRs + de/gesetzes
2. **Run Phase 2** — hf_cases, openlegaldata, Library
3. **Run Phase 3** — multilegalpile (German subset only)
4. **Fine-tune Qwen2.5-7B** — using generated ~200K training samples
5. **Integration tests** — test pipeline steps, RAG pipeline
6. **CI/CD pipeline** — automated testing/deployment
7. **German reranker** — current MiniLM is English-only
8. **Database migrations** — no Alembic setup yet

---

## Known Issues

| Issue | Impact | Status |
|-------|--------|--------|
| Phase 1 Step 2-6 in progress | Chunking/classification/embedding not yet done | Running Step 2 now |
| Phase 2-3 data not yet processed | ~650GB remaining corpus | After Phase 1 completes |
| 103 VDR files failed Step 1 | Mostly legacy .xls/.doc formats | Install LibreOffice for conversion |
| OCR § vs $ confusion (some docs) | 20 instances in one Jahresabschluss | Post-processing fix in text cleaner |
| Reranker is English-only (MiniLM) | Suboptimal for German text | Evaluate German alternatives |
| No fine-tuned model yet | Using base Qwen2.5-7B | Generate training data first (Step 5) |
| No CI/CD | Manual testing only | Set up GitHub Actions |
| `LAI/embedding_server/` (2.2GB) | Old BGE-M3 cache, not used by any docker-compose | Safe to delete |

---

## Where to Find Things

| What | Where |
|------|-------|
| App config | [src/lai/core/config.py](../src/lai/core/config.py) |
| Data pipeline | [src/lai/pipeline/](../src/lai/pipeline/) — Steps 1-6 |
| Pipeline CLI | `python -m lai.pipeline.cli step1 --help` |
| RAG pipeline | [src/lai/api/pipeline.py](../src/lai/api/pipeline.py) |
| Hybrid search SQL | [src/lai/search/hybrid_search.py](../src/lai/search/hybrid_search.py) |
| Prompt templates | [src/lai/generation/prompt_builder.py](../src/lai/generation/prompt_builder.py) |
| Docker services | [/data/projects/lai/Docker/](../../../Docker/) |
| Infrastructure docs | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
| Architecture overview | [architecture/overview.md](architecture/overview.md) |
| Improvement roadmap | [analysis/LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) |
| Project history (V1-V4) | [analysis/LAI_PROJECT_ANALYSIS.md](analysis/LAI_PROJECT_ANALYSIS.md) |
