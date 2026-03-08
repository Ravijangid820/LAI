# LAI Project Restructuring - Production-Grade Directory & Project Structure

---

## The Problem With The Current Structure

### What We Have Now

```
/data/projects/lai/
|-- LAIV1/          # Complete standalone app (18 files, 7.9 GB, own .venv)
|-- LAIV2/          # Complete standalone app (20 files, 759 GB, 3 .venvs)
|-- LAIV3/          # Complete standalone app (96 files, 15 GB, 2 .venvs)
|-- LAIV4/          # Complete standalone app (54 files, 3.4 GB, own .venv)
|-- Docker/         # Shared infra configs (NOT used by any version)
|-- backend/        # Empty directory
|-- data_processing/# Standalone utils (NOT imported by any version)
|-- LAI/            # Legacy data collection (28 GB, abandoned)
|-- models/         # Shared model weights (used by V2/V3/V4 but not linked)
|-- VDRs/           # Raw due diligence documents
|-- ...             # 10+ scattered files, docs, spreadsheets
```

### Why This Is Broken

| Problem | Impact |
|---------|--------|
| **Zero code reuse** | Every version rewrites chunking, embedding, config, retrieval from scratch |
| **10 separate virtual environments** | ~800GB of duplicated Python packages |
| **No shared libraries** | Bug fix in V3's chunker doesn't help V4 |
| **Docker services duplicated** | postgres/redis defined in 4 different docker-compose files |
| **Training mixed with serving** | V2 is 759GB because model weights sit next to app code |
| **No clear "source of truth"** | Which version is production? Which chunker is canonical? |
| **Orphaned directories** | Docker/, backend/, data_processing/ exist but nothing imports them |
| **Models scattered** | Checkpoints in /models/, LAIV2/checkpoints/, LAIV3/finetuning_next_steps/ |
| **Documentation scattered** | .md files at root, in each version, in subdirectories |
| **No monorepo tooling** | No shared linting, testing, CI/CD config |

### The Root Cause

The project grew organically: "V1 didn't work, start fresh in V2. V2 training done, start fresh in V3 for serving. V3 too rigid, start fresh in V4 for agents." Each time, everything was copy-evolved rather than extracted into shared components.

---

## The Proposed Structure

### Design Principles

1. **Separate concerns by function, not by version** - training, serving, data processing, and infrastructure are independent concerns
2. **Shared libraries for shared logic** - chunking, embedding, retrieval, config patterns used by everyone go in one place
3. **Models and data outside the code tree** - weights, datasets, and VDR documents don't belong in the application repository
4. **One infrastructure definition** - Docker services defined once, composed per deployment profile
5. **Version = deployment configuration, not a codebase fork** - V5, V6, etc. should be configuration changes on the same codebase, not new directories

### Proposed Directory Layout

```
lai/
|
|-- libs/                           # SHARED LIBRARIES (the core reusable code)
|   |-- lai-core/                   # Core domain models, constants, types
|   |   |-- pyproject.toml
|   |   |-- lai_core/
|   |   |   |-- __init__.py
|   |   |   |-- config.py           # Base Pydantic settings (shared patterns)
|   |   |   |-- constants.py        # German law codes, legal patterns, regex
|   |   |   |-- models.py           # Shared Pydantic models (Document, Chunk, Query, Response)
|   |   |   |-- exceptions.py       # Exception hierarchy
|   |   |   |-- legal_refs.py       # SS/Art/law extraction (used by chunker, analyzer, verifier)
|   |   |   |-- types.py            # Enums: DocType, CourtLevel, RiskStatus, QueryIntent
|   |   |   |-- logging.py          # Structured logging setup
|   |   |   |-- utils.py            # Date parsing, text normalization, NUL sanitization
|   |   |-- tests/
|   |
|   |-- lai-retrieval/              # Search & retrieval logic
|   |   |-- pyproject.toml
|   |   |-- lai_retrieval/
|   |   |   |-- __init__.py
|   |   |   |-- hybrid_search.py    # Dense + BM25 + RRF (THE canonical implementation)
|   |   |   |-- query_analyzer.py   # Legal ref extraction, intent detection
|   |   |   |-- reranker.py         # Cross-encoder reranking
|   |   |   |-- metadata_filter.py  # Build SQL filters from parsed query
|   |   |   |-- quality_checker.py  # Min chunks, min similarity validation
|   |   |   |-- deduplicator.py     # Result dedup by (doc_id, section)
|   |   |   |-- cache.py            # Redis query result caching
|   |   |-- tests/
|   |
|   |-- lai-ingestion/              # Document processing & embedding
|   |   |-- pyproject.toml
|   |   |-- lai_ingestion/
|   |   |   |-- __init__.py
|   |   |   |-- chunker.py          # Legal-aware semantic chunking (THE canonical one)
|   |   |   |-- embedder.py         # Embedding client (TEI / vLLM)
|   |   |   |-- pdf_processor.py    # Docling PDF/DOCX extraction
|   |   |   |-- deduplicator.py     # MinHash near-duplicate detection
|   |   |   |-- ner_stripper.py     # Clean vs tagged text
|   |   |   |-- metadata_enricher.py# Court level, law refs, entity extraction
|   |   |   |-- date_extractor.py   # Temporal field population
|   |   |-- tests/
|   |
|   |-- lai-generation/             # LLM interaction
|   |   |-- pyproject.toml
|   |   |-- lai_generation/
|   |   |   |-- __init__.py
|   |   |   |-- llm_client.py       # vLLM OpenAI-compatible client
|   |   |   |-- prompt_builder.py   # Context + query prompt assembly
|   |   |   |-- citation_verifier.py# Regex + exact match verification
|   |   |   |-- response_formatter.py
|   |   |   |-- response_validator.py# Disclaimer removal, quality checks
|   |   |   |-- prompts/
|   |   |   |   |-- system_de.py    # German-first system prompts
|   |   |   |   |-- system_en.py    # English system prompts
|   |   |   |   |-- few_shot.py     # Domain-balanced examples
|   |   |   |   |-- cot_templates.py# Chain-of-thought templates
|   |   |-- tests/
|   |
|   |-- lai-db/                     # Database layer
|   |   |-- pyproject.toml
|   |   |-- lai_db/
|   |   |   |-- __init__.py
|   |   |   |-- connection.py       # asyncpg pool management
|   |   |   |-- repositories/       # UserRepo, DocumentRepo, ChunkRepo, AuditRepo
|   |   |   |-- schema_manager.py   # Multi-tenancy (user_{uuid} schemas)
|   |   |   |-- feedback_store.py   # Feedback CRUD + quality score updates
|   |   |-- migrations/             # Alembic (ONE migration history for the whole project)
|   |   |   |-- alembic.ini
|   |   |   |-- versions/
|   |   |-- tests/
|   |
|   |-- lai-storage/                # Object storage
|   |   |-- pyproject.toml
|   |   |-- lai_storage/
|   |   |   |-- __init__.py
|   |   |   |-- minio_client.py     # MinIO async operations
|   |   |   |-- file_storage.py     # Upload, download, list
|   |   |-- tests/
|
|-- services/                       # DEPLOYABLE SERVICES (what actually runs)
|   |
|   |-- api/                        # FastAPI REST API (the main product)
|   |   |-- pyproject.toml          # Depends on: lai-core, lai-retrieval, lai-generation, lai-db
|   |   |-- api/
|   |   |   |-- __init__.py
|   |   |   |-- main.py             # FastAPI app creation, middleware
|   |   |   |-- config.py           # API-specific settings (extends lai-core config)
|   |   |   |-- endpoints/
|   |   |   |   |-- rag.py          # POST /api/v1/rag
|   |   |   |   |-- search.py       # POST /api/v1/search
|   |   |   |   |-- documents.py    # Document CRUD + upload + download + analytics
|   |   |   |   |-- analysis.py     # Document analysis, contract comparison
|   |   |   |   |-- auth.py         # JWT login/refresh/logout
|   |   |   |   |-- health.py       # Health checks
|   |   |   |   |-- feedback.py     # Feedback submission
|   |   |   |   |-- admin.py        # Admin endpoints
|   |   |   |-- pipeline/
|   |   |   |   |-- orchestrator.py # RAG pipeline (linear 8-step from V3)
|   |   |   |   |-- crag.py         # CRAG loop (from V4, optional)
|   |   |   |-- auth/
|   |   |   |   |-- jwt_handler.py
|   |   |   |   |-- models.py
|   |   |   |-- middleware/
|   |   |   |   |-- rate_limiter.py
|   |   |   |   |-- circuit_breaker.py
|   |   |-- Dockerfile
|   |   |-- tests/
|   |
|   |-- worker/                     # Celery async worker
|   |   |-- pyproject.toml          # Depends on: lai-core, lai-ingestion, lai-db, lai-storage
|   |   |-- worker/
|   |   |   |-- __init__.py
|   |   |   |-- celery_app.py       # Celery config (queues: document.process, batch.ingest)
|   |   |   |-- tasks/
|   |   |   |   |-- document_tasks.py  # Process, chunk, embed uploaded docs
|   |   |   |   |-- batch_tasks.py     # Large-scale corpus ingestion
|   |   |   |   |-- backfill_tasks.py  # Embedding/BM25 backfill
|   |   |-- Dockerfile
|   |   |-- tests/
|   |
|   |-- web-search/                 # Web search fallback (optional microservice)
|   |   |-- pyproject.toml          # Depends on: lai-core, lai-generation
|   |   |-- web_search/
|   |   |   |-- __init__.py
|   |   |   |-- brave_client.py     # Brave Search API
|   |   |   |-- legal_domains.py    # German legal domain filtering
|   |   |-- tests/
|
|-- training/                       # MODEL TRAINING (completely separate lifecycle)
|   |
|   |-- fine-tuning/                # SFT + DPO training scripts
|   |   |-- pyproject.toml          # Depends on: torch, transformers, peft, trl
|   |   |-- configs/
|   |   |   |-- qwen25_7b_lora.yaml     # Current production training config
|   |   |   |-- qwen25_14b_lora.yaml    # Experimental
|   |   |   |-- leo_7b_unsloth.yaml     # Legacy V2 config (archived)
|   |   |-- scripts/
|   |   |   |-- train_sft.py            # Supervised fine-tuning
|   |   |   |-- train_dpo.py            # Direct Preference Optimization
|   |   |   |-- merge_lora.py           # Merge adapter into base model
|   |   |   |-- evaluate.py             # Perplexity, BLEU, legal accuracy
|   |   |-- tests/
|   |
|   |-- data-pipeline/              # Training data preparation
|   |   |-- pyproject.toml          # Depends on: lai-core (for legal_refs, constants)
|   |   |-- scripts/
|   |   |   |-- step1_quality_filter.py
|   |   |   |-- step2_deduplicate.py
|   |   |   |-- step3_domain_classify.py
|   |   |   |-- step4_chunk_for_training.py
|   |   |   |-- step5_generate_synthetic.py
|   |   |   |-- step6_merge_datasets.py
|   |   |   |-- step7_tokenize.py
|   |   |-- configs/
|   |   |   |-- synthetic_generation.yaml
|   |   |   |-- domain_keywords.yaml
|   |   |-- tests/
|   |
|   |-- evaluation/                 # Model evaluation & benchmarking
|   |   |-- benchmarks/
|   |   |   |-- german_legal_qa.jsonl   # Held-out test set
|   |   |   |-- citation_accuracy.jsonl
|   |   |-- scripts/
|   |   |   |-- run_benchmark.py
|   |   |   |-- compare_models.py
|
|-- infra/                          # INFRASTRUCTURE (defined once, used everywhere)
|   |
|   |-- docker/
|   |   |-- docker-compose.yml      # THE one compose file (with profiles)
|   |   |-- docker-compose.dev.yml  # Dev overrides (adminer, debug ports)
|   |   |-- Dockerfile.api          # API service
|   |   |-- Dockerfile.worker       # Celery worker
|   |   |-- init-scripts/
|   |   |   |-- init-db.sql         # pgvector extension, initial schema
|   |   |   |-- init-minio.sh       # Bucket creation
|   |
|   |-- monitoring/
|   |   |-- prometheus.yml
|   |   |-- grafana/
|   |   |   |-- dashboards/
|   |   |   |-- provisioning/
|   |   |-- alerting/
|   |   |   |-- rules.yml
|   |
|   |-- deploy/                     # Deployment configs
|   |   |-- .env.example            # Template with all env vars documented
|   |   |-- .env.dev                # Dev defaults
|   |   |-- .env.staging
|   |   |-- .env.prod
|
|-- docs/                           # ALL DOCUMENTATION (one place)
|   |-- architecture/
|   |   |-- overview.md             # Current system architecture
|   |   |-- decisions/              # Architecture Decision Records (ADRs)
|   |   |   |-- 001-local-llm.md
|   |   |   |-- 002-pgvector-over-qdrant.md
|   |   |   |-- 003-hybrid-search.md
|   |   |   |-- 004-qwen-over-leo.md
|   |   |   |-- ...
|   |   |-- diagrams/               # Mermaid/draw.io diagrams
|   |-- training/
|   |   |-- training-architecture.md
|   |   |-- data-pipeline.md
|   |   |-- model-evaluation.md
|   |-- operations/
|   |   |-- deployment-guide.md
|   |   |-- runbook.md
|   |   |-- embedding-migration.md
|   |-- api/
|   |   |-- openapi-spec.yaml       # Or auto-generated
|   |-- analysis/
|   |   |-- LAI_PROJECT_ANALYSIS.md # The analysis document we already created
|   |   |-- LAIV5_IMPROVEMENTS.md   # The improvements roadmap
|
|-- scripts/                        # ONE-OFF & OPERATIONAL SCRIPTS
|   |-- backfill_embeddings.py
|   |-- backfill_search_vector.py
|   |-- sanitize_nul_bytes.py
|   |-- export_feedback_for_training.py
|   |-- benchmark_retrieval.py
|
|-- tests/                          # TOP-LEVEL TEST CONFIG
|   |-- conftest.py                 # Shared fixtures
|   |-- pytest.ini
|   |-- e2e/                        # End-to-end tests (full pipeline)
|   |   |-- test_rag_pipeline.py
|   |   |-- test_document_upload.py
|   |   |-- test_api_endpoints.py
|
|-- pyproject.toml                  # ROOT: workspace config, shared dev deps (ruff, mypy, pytest)
|-- uv.lock                         # Single lock file for the workspace
|-- .python-version                 # 3.13
|-- CLAUDE.md                       # AI assistant instructions
|-- README.md
```

### What Lives OUTSIDE the Repo (Data & Models)

```
/data/
|-- models/                         # MODEL WEIGHTS (never in git, never in app code)
|   |-- base/
|   |   |-- qwen25-7b-instruct/    # Base model from HuggingFace
|   |   |-- leo-hessianai-7b/      # Legacy base
|   |-- fine-tuned/
|   |   |-- qwen25-7b-legal-merged/ # Current production model
|   |   |-- qwen25-7b-legal-lora/   # LoRA adapter checkpoints
|   |-- embeddings/
|   |   |-- bge-m3/                 # Embedding model cache
|   |   |-- gte-qwen2-1.5b/        # Future embedding model
|   |-- rerankers/
|   |   |-- ms-marco-minilm/       # Reranker model cache
|
|-- datasets/                       # TRAINING DATA (never in git)
|   |-- raw/                        # 640GB raw legal datasets
|   |-- filtered/                   # Post quality-filtering
|   |-- deduplicated/               # Post dedup
|   |-- domain-classified/          # 7 domain splits
|   |-- training-ready/             # Final SFT/DPO formatted data
|   |-- tokenized/                  # Pre-tokenized shards
|
|-- vdrs/                           # VIRTUAL DATA ROOMS (client documents)
|   |-- wp-33-34/
|   |-- wp-altmark/
|   |-- wp-beppener-bruch/
|   |-- ...
|
|-- minio-data/                     # MinIO persistent storage
|-- postgres-data/                  # PostgreSQL persistent storage
|-- redis-data/                     # Redis persistent storage
```

---

## How This Solves Each Problem

### Problem: Zero code reuse
**Solution:** `libs/` contains 6 shared libraries. Every service depends on them via workspace dependencies.

```toml
# services/api/pyproject.toml
[project]
dependencies = [
    "lai-core",
    "lai-retrieval",
    "lai-generation",
    "lai-db",
]

# In uv workspace (root pyproject.toml)
[tool.uv.workspace]
members = ["libs/*", "services/*", "training/*"]

[tool.uv.sources]
lai-core = { workspace = true }
lai-retrieval = { workspace = true }
lai-generation = { workspace = true }
lai-db = { workspace = true }
lai-ingestion = { workspace = true }
lai-storage = { workspace = true }
```

Fix the chunker once in `libs/lai-ingestion/chunker.py` -> every service gets the fix.

### Problem: 10 separate virtual environments (800GB+ duplication)
**Solution:** One `uv` workspace with a single `.venv` at root. All packages share the same environment.

```bash
# One command to install everything
uv sync

# Or install only what you need
uv sync --package lai-api
uv sync --package lai-worker
```

### Problem: Docker services duplicated across versions
**Solution:** One `docker-compose.yml` with profiles.

```yaml
# infra/docker/docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    profiles: [infra, dev, prod]
    ports: ["5433:5432"]

  redis:
    image: redis:7-alpine
    profiles: [infra, dev, prod]

  minio:
    image: minio/minio:latest
    profiles: [infra, dev, prod]

  embedding:
    image: vllm/vllm-openai:latest
    profiles: [ml, prod]
    command: --model BAAI/bge-m3 --task embedding

  reranker:
    image: vllm/vllm-openai:latest
    profiles: [ml, prod]
    command: --model cross-encoder/ms-marco-MiniLM-L-12-v2 --task score

  llm:
    image: vllm/vllm-openai:latest
    profiles: [ml, prod]
    command: --model /models/fine-tuned/qwen25-7b-legal-merged

  api:
    build: { dockerfile: Dockerfile.api }
    profiles: [app, prod]

  worker:
    build: { dockerfile: Dockerfile.worker }
    profiles: [app, prod]

  prometheus:
    profiles: [monitoring, prod]

  grafana:
    profiles: [monitoring, prod]

# Usage:
#   docker compose --profile infra up -d          # Just databases
#   docker compose --profile infra --profile ml up -d   # DB + ML models
#   docker compose --profile prod up -d            # Everything
```

### Problem: Training mixed with serving code (759GB in V2)
**Solution:** `training/` is completely separate from `services/`. Training has its own dependencies (torch, transformers, unsloth) that never pollute the serving environment.

```toml
# training/fine-tuning/pyproject.toml
[project]
dependencies = [
    "torch>=2.9",
    "transformers>=4.57",
    "peft>=0.18",
    "trl>=0.26",
    "lai-core",           # Only shared lib it needs (for constants, types)
]
```

Model weights live in `/data/models/`, mounted as Docker volumes. Never in the code tree.

### Problem: No clear source of truth
**Solution:** Each shared library has ONE canonical implementation.

| Function | Canonical Location | Used By |
|----------|-------------------|---------|
| Legal-aware chunking | `libs/lai-ingestion/chunker.py` | worker, training/data-pipeline |
| Hybrid search | `libs/lai-retrieval/hybrid_search.py` | api |
| Embedding client | `libs/lai-ingestion/embedder.py` | worker, api (for query embedding) |
| LLM client | `libs/lai-generation/llm_client.py` | api |
| Citation verifier | `libs/lai-generation/citation_verifier.py` | api |
| DB connection pool | `libs/lai-db/connection.py` | api, worker |
| Multi-tenancy | `libs/lai-db/schema_manager.py` | api, worker |
| Legal ref extraction | `libs/lai-core/legal_refs.py` | everyone |

### Problem: Orphaned directories
**Solution:** Everything has a clear home. No orphans.

| Old Location | New Location | Status |
|-------------|-------------|--------|
| `Docker/` | `infra/docker/` | Consolidated |
| `backend/` | Deleted (was empty) | Removed |
| `data_processing/` | `libs/lai-ingestion/` | Absorbed |
| `LAI/` | Archived | Legacy, not needed |
| `LAIV1/` | Archived | Analysis modules -> `libs/lai-core/types.py` for risk models |
| `LAIV2/` | `training/` | Training scripts extracted |
| `LAIV3/` | `services/api/` + `services/worker/` + `libs/` | Decomposed |
| `LAIV4/` | `services/api/pipeline/crag.py` + `libs/lai-db/schema_manager.py` | Cherry-picked |
| `models/` | `/data/models/` (outside repo) | Externalized |
| `VDRs/` | `/data/vdrs/` (outside repo) | Externalized |
| `qdrant/` | Deleted | Deprecated in favor of pgvector |

---

## How Versioning Works Going Forward

### No More LAIV1, LAIV2, LAIV3, ...

Instead of copying the whole project for each version, use **git tags and feature flags**.

```
# Version = git tag on the same codebase
git tag v3.0.0  # What was LAIV3
git tag v4.0.0  # What was LAIV4
git tag v5.0.0  # Next release

# Breaking changes = branches, not directories
git checkout -b feat/crag-loop        # Add CRAG
git checkout -b feat/multi-tenancy    # Add user schemas
# Merge into main when ready, tag a release
```

### Feature Flags for Optional Capabilities

```python
# services/api/config.py
class PipelineSettings(BaseSettings):
    enable_crag: bool = False              # V4 feature, off by default
    enable_web_search_fallback: bool = False
    enable_self_rag: bool = False
    enable_document_analysis: bool = True
    crag_max_loops: int = 2
    self_rag_max_retries: int = 1
```

This means V5 is just: `enable_crag=True, enable_web_search_fallback=True` in the same codebase.

---

## Migration Path: Current -> New Structure

### Phase 1: Extract shared libraries (Week 1-2)

1. Create `libs/lai-core/` from V3's `config/constants.py`, `config/settings.py` base classes, and V4's `models/`
2. Create `libs/lai-retrieval/` from V3's `pipeline/retrieval/`
3. Create `libs/lai-ingestion/` from V3's `pipeline/ingestion/`
4. Create `libs/lai-generation/` from V3's `pipeline/generation/` + Docker/inference_engine prompts
5. Create `libs/lai-db/` from V3's `storage/` + V4's `schema_manager.py`
6. Create `libs/lai-storage/` from V3's `storage/minio_client.py`
7. Set up `uv` workspace with all libs

### Phase 2: Build services on top of libs (Week 3-4)

1. Create `services/api/` - import from libs, add FastAPI endpoints and pipeline orchestrator
2. Create `services/worker/` - import from libs, add Celery tasks
3. Create `services/web-search/` - import from libs, add Brave Search client
4. Verify all V3 API endpoints work with new structure
5. Run V3's existing tests against new code

### Phase 3: Consolidate infrastructure (Week 3)

1. Create `infra/docker/docker-compose.yml` with profiles
2. Move monitoring configs to `infra/monitoring/`
3. Create environment templates in `infra/deploy/`
4. Delete all per-version docker-compose files

### Phase 4: Extract training (Week 4)

1. Create `training/fine-tuning/` from V3's `finetuning_next_steps/` + V2's `training_v2/`
2. Create `training/data-pipeline/` from V2's `finetuning_pipeline/` + V3's step scripts
3. Move model weights to `/data/models/`
4. Move raw datasets to `/data/datasets/`

### Phase 5: Archive and clean up (Week 5)

1. Move LAIV1-LAIV4 to `_archive/` (keep for reference, not active development)
2. Delete orphaned directories (backend/, qdrant/, LAI/)
3. Move VDRs to `/data/vdrs/`
4. Consolidate all docs into `docs/`
5. Tag the final state as `v5.0.0`

---

## Dependency Graph

```
libs/lai-core          (0 internal deps - foundation)
    |
    +-- libs/lai-db             (depends on: lai-core)
    +-- libs/lai-storage        (depends on: lai-core)
    +-- libs/lai-ingestion      (depends on: lai-core)
    +-- libs/lai-retrieval      (depends on: lai-core, lai-db)
    +-- libs/lai-generation     (depends on: lai-core)
         |
         +-- services/api       (depends on: lai-core, lai-retrieval, lai-generation, lai-db, lai-storage)
         +-- services/worker    (depends on: lai-core, lai-ingestion, lai-db, lai-storage)
         +-- services/web-search(depends on: lai-core, lai-generation)
         |
         +-- training/data-pipeline  (depends on: lai-core only)
         +-- training/fine-tuning    (depends on: lai-core only, + torch/transformers)
```

Key property: **no circular dependencies**. Each layer only depends on the layer below it.

---

## Workspace Configuration

### Root pyproject.toml

```toml
[project]
name = "lai"
version = "5.0.0"
description = "German Legal AI Platform"
requires-python = ">=3.13"

[tool.uv.workspace]
members = [
    "libs/lai-core",
    "libs/lai-db",
    "libs/lai-storage",
    "libs/lai-ingestion",
    "libs/lai-retrieval",
    "libs/lai-generation",
    "services/api",
    "services/worker",
    "services/web-search",
    "training/fine-tuning",
    "training/data-pipeline",
    "training/evaluation",
]

[tool.uv.sources]
lai-core = { workspace = true }
lai-db = { workspace = true }
lai-storage = { workspace = true }
lai-ingestion = { workspace = true }
lai-retrieval = { workspace = true }
lai-generation = { workspace = true }

# Shared dev dependencies
[tool.uv.dev-dependencies]
ruff = ">=0.8.0"
mypy = ">=1.13.0"
pytest = ">=8.3.0"
pytest-asyncio = ">=0.24.0"
pytest-cov = ">=6.0.0"

[tool.ruff]
target-version = "py313"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version = "3.13"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "libs/*/tests", "services/*/tests"]
markers = ["unit", "integration", "e2e", "slow"]
```

### Example Library pyproject.toml

```toml
# libs/lai-retrieval/pyproject.toml
[project]
name = "lai-retrieval"
version = "5.0.0"
requires-python = ">=3.13"
dependencies = [
    "lai-core",
    "lai-db",
    "httpx>=0.28.0",
    "numpy>=1.26.0",
    "redis>=5.2.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Example Service pyproject.toml

```toml
# services/api/pyproject.toml
[project]
name = "lai-api"
version = "5.0.0"
requires-python = ">=3.13"
dependencies = [
    "lai-core",
    "lai-retrieval",
    "lai-generation",
    "lai-db",
    "lai-storage",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "python-jose[cryptography]>=3.3.0",
    "passlib[bcrypt]>=1.7.4",
    "slowapi>=0.1.9",
    "prometheus-client>=0.21.0",
    "structlog>=24.4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## What This Enables

### For Development
- `uv sync` once, everything works
- Change `libs/lai-retrieval/hybrid_search.py` -> both API and worker use the fix immediately
- Run `pytest libs/lai-retrieval/tests/` to test search in isolation
- Run `pytest tests/e2e/` to test full pipeline

### For Deployment
- `docker compose --profile prod up -d` -> everything runs
- `docker compose --profile infra up -d` -> just databases for local dev
- Environment files control behavior: `ENABLE_CRAG=true` in prod, `false` in dev

### For Training
- `cd training/fine-tuning && uv run python scripts/train_sft.py --config configs/qwen25_7b_lora.yaml`
- Completely independent from serving code
- Only shared dependency is `lai-core` (for legal constants and types)

### For Future Versions
- V6 = new features merged into main, tagged as `v6.0.0`
- No more directory copying
- Feature flags control what's active in each deployment

---

## Comparison: Current vs Proposed

| Metric | Current | Proposed |
|--------|---------|----------|
| Virtual environments | 10 (~800GB) | 1 (~5GB) |
| docker-compose files | 5+ (duplicated services) | 2 (base + dev override) |
| Copies of chunker logic | 3 (V2, V3, V4 variants) | 1 (libs/lai-ingestion) |
| Copies of retrieval logic | 3 (LAI/, V3, V4) | 1 (libs/lai-retrieval) |
| Copies of postgres config | 4 (V1, V3, V4, Docker/) | 1 (infra/docker) |
| Where to fix a bug | "which version?" | One canonical location |
| How to add a feature | Copy a version directory | Feature branch + merge |
| Model weights in repo | ~800GB scattered | 0 (external /data/models) |
| Clear entry point | No (4 competing services) | Yes (services/api) |
| Test command | Per-version, if tests exist | `pytest` from root |
| Onboarding time | "Read 4 codebases" | "Read libs/ + services/api/" |
