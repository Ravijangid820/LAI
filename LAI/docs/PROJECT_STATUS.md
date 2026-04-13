# LAI Project Status

> Last updated: 2026-04-12

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
│   │   ├── extraction/               # Location/geo extraction from legal docs (LLM-based)
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
| `POST` | `/extraction/locations/{segment_id}` | Extract geo locations from a segment |
| `POST` | `/extraction/locations/batch` | Batch extract locations by source |
| `GET` | `/extraction/locations/{segment_id}` | Get extracted locations for a segment |
| `GET` | `/extraction/locations/summary` | Location extraction statistics |
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
- [x] Fixed infinite loop in child chunk overlap calculation
- [x] Versioned classification history table (`chunk_classifications`) with audit trail
- [x] Fixed synth-generator docker-compose for Blackwell GPU compatibility (CUDA 13.0)
- [x] `--reclassify` and `--model-version` flags for Step 3
- [x] Step 3 domain classification completed (Phase 1)
- [x] Step 4 contextual enrichment completed (Phase 1, 217K chunks, 4h 51m)
- [x] Step 5 fine-tuning data generation in progress (8 concurrent, ~46h ETA)
- [x] Location/geo extraction module (`lai.extraction`) — LLM-based extraction of geocodable addresses, Flurstücke, coordinates from legal documents
- [x] Extraction API endpoints (single, batch, summary)
- [x] Test script for extraction (`scripts/test_extraction.py`)

---

## Pipeline Execution Progress (Phase 1)

Processing is done in phases due to storage constraints (~613GB free).

### Phase 1 — High-value sources (~20GB)

| Source | Step 1 (Convert) | Step 2 (Chunk) | Step 3 (Classify) | Step 4 (Enrich) | Step 5 (Generate) | Step 6 (Embed) |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| DD Reports (19MB, 18 files) | Done | Done | Done | Done | In progress | - |
| VDRs (6GB, 4.3K files) | Done (103 .xls/.doc failed) | Done | Done | Done | In progress | - |
| de/gesetzes (750MB, 764 files) | Done | Done | Done | Done | In progress | - |

**Step 2 totals:** 12,307 files → 134,474 parent chunks, 217,165 child chunks (2m 35s)
**Step 3:** Done — reclassified with improved JSON parser + versioned history
**Step 4:** Done — 217K child chunks enriched with context prefix (4h 51m, 16 concurrent)
**Step 5:** In progress — **~90K/200K samples** (45%) as of 2026-04-12, container LLM (`lai_synth_generator`), 8 concurrent parents

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

1. **Complete Step 5** — ~90K/200K samples (45%), resumable via [scripts/resume_step5.sh](../scripts/resume_step5.sh)
2. **Run Step 6** (embeddings) — ~1-2 hours, needs Qwen3-Embedding-8B container
3. **Run Phase 2** — hf_cases, openlegaldata, Library (Steps 1-6)
4. **Run Phase 3** — multilegalpile (German subset only)
5. **Fine-tune Qwen2.5-7B** — using generated ~200K training samples
6. **Geocoding integration** — connect extracted locations to map API (Mapbox/Leaflet)
7. **Integration tests** — test full RAG pipeline end-to-end
8. **German reranker** — current MiniLM is English-only
9. **CI/CD pipeline** — automated testing/deployment
10. **Database migrations** — Alembic setup

---

## Known Issues

| Issue | Impact | Status |
|-------|--------|--------|
| Step 5 in progress (90K/200K, ~45%) | Fine-tuning data generation | Resumable via `scripts/resume_step5.sh`; container vLLM on port 8005 |
| GPU contention with shared users | vLLM may OOM if other users overfill GPU | Use `--status` flag to check; resume cleanly via SQLite checkpoint |
| Step 6 not yet started | Embeddings pending | Awaiting Step 5 |
| Phase 2-3 data not yet processed | ~650GB remaining corpus | After Phase 1 completes |
| 103 VDR files failed Step 1 | Mostly legacy .xls/.doc formats | Install LibreOffice for conversion |
| Reranker is English-only (MiniLM) | Suboptimal for German text | Evaluate German alternatives |
| No fine-tuned model yet | Using base Qwen2.5-7B | Generate training data first (Step 5) |
| No CI/CD | Manual testing only | Set up GitHub Actions |
| `LAI/embedding_server/` (2.2GB) | Old BGE-M3 cache, not used by any docker-compose | Safe to delete |

---

## Docker-free Operation (added 2026-04-12)

The pipeline can run with **only the LLM container** (no PostgreSQL, no MinIO, no Redis). All pipeline state lives in SQLite.

### Resume the running pipeline (one command)
```bash
./scripts/resume_step5.sh           # starts vLLM container + Step 5
./scripts/resume_step5.sh --status  # show progress
./scripts/resume_step5.sh --stop    # stop Step 5 (keeps LLM up)
```

The script auto-detects whichever container is publishing port 8005
(`lai-teacher-llm-gpu0`, `lai_synth_generator`, etc.). All Step 5 progress is checkpointed to `processed/pipeline_local.db` after every batch — safe to interrupt at any time.

### Local-mode CLI
Every pipeline step accepts `--local`:
```bash
python -m lai.pipeline.cli step2 --local
python -m lai.pipeline.cli step5 --local
```
Local mode uses [local_storage.py](../src/lai/pipeline/local_storage.py) to:
- Read MinIO objects directly from `/data/projects/lai/Docker/database/minio/data/`
- Use SQLite (`processed/pipeline_local.db`) instead of PostgreSQL

### Portable database exports
SQLite exports of both PG databases live at `LAI/processed/db_export/`:
- `pipeline.db` (1 GB) — chunks, training samples, classifications
- `app.db` (284 GB) — chunks with embeddings as binary BLOBs (1024 floats per row)

Decode an embedding in pure Python (no PostgreSQL needed):
```python
import sqlite3, struct
conn = sqlite3.connect('LAI/processed/db_export/app.db')
blob = conn.execute("SELECT embedding FROM chunks LIMIT 1").fetchone()[0]
embedding = list(struct.unpack('1024f', blob))  # 1024-dim vector
```

Regenerate exports anytime PostgreSQL is up:
```bash
python scripts/export_to_sqlite.py all
```

---

## Where to Find Things

| What | Where |
|------|-------|
| App config | [src/lai/core/config.py](../src/lai/core/config.py) |
| Data pipeline | [src/lai/pipeline/](../src/lai/pipeline/) — Steps 1-6 |
| Location extraction | [src/lai/extraction/](../src/lai/extraction/) — LLM-based geo extraction |
| Extraction test script | [scripts/test_extraction.py](../scripts/test_extraction.py) |
| Pipeline progress report | [PIPELINE_PROGRESS_REPORT.md](PIPELINE_PROGRESS_REPORT.md) |
| Pipeline CLI | `python -m lai.pipeline.cli step1 --help` |
| Local mode (no PostgreSQL) | `python -m lai.pipeline.cli step2 --local` — see [local_storage.py](../src/lai/pipeline/local_storage.py) |
| Resume Step 5 (one-shot) | `./scripts/resume_step5.sh` — auto-starts vLLM container + Step 5 |
| SQLite export of all DB data | `python scripts/export_to_sqlite.py all` — creates portable `.db` files |
| SQLite exports (location) | `LAI/processed/db_export/pipeline.db` (1GB) and `app.db` (284GB) |
| RAG pipeline | [src/lai/api/pipeline.py](../src/lai/api/pipeline.py) |
| Hybrid search SQL | [src/lai/search/hybrid_search.py](../src/lai/search/hybrid_search.py) |
| Prompt templates | [src/lai/generation/prompt_builder.py](../src/lai/generation/prompt_builder.py) |
| Docker services | [/data/projects/lai/Docker/](../../../Docker/) |
| Infrastructure docs | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
| Architecture overview | [architecture/overview.md](architecture/overview.md) |
| Improvement roadmap | [analysis/LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) |
| Project history (V1-V4) | [analysis/LAI_PROJECT_ANALYSIS.md](analysis/LAI_PROJECT_ANALYSIS.md) |
