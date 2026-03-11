# LAI - German Legal AI Platform

Legal AI platform for wind energy due diligence. Answers legal questions using RAG (Retrieval-Augmented Generation) over a 672GB German legal corpus with locally-hosted models. Includes a full data processing pipeline for both RAG retrieval and model fine-tuning.

## Architecture

- **RAG Pipeline:** Query analysis → hybrid search (dense + BM25 + RRF) → cross-encoder reranking → CRAG grading → LLM generation → citation verification
- **Data Pipeline:** Raw documents → segments → parent-child chunks → domain classification → contextual enrichment → fine-tuning data → embeddings
- **Models:**
  - LLM: Qwen2.5-7B-Instruct (inference), Qwen2.5-72B-Instruct-AWQ (pipeline, tensor-parallel 2 GPUs)
  - Embedding: Qwen3-Embedding-8B (1024 dims, #1 MTEB multilingual)
  - Reranker: ms-marco-MiniLM-L-12-v2
- **Infrastructure:** PostgreSQL + pgvector, Redis, MinIO, vLLM — all self-hosted
- **Hardware:** 2x RTX Pro 6000 GPUs (96GB VRAM each)
- **Multi-tenancy:** Per-user PostgreSQL schemas for uploaded documents

## Quick Start

```bash
# 1. Start Docker services
docker network create lai_network
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d  # port 5434
cd /data/projects/lai/Docker/database/redis && docker compose up -d
cd /data/projects/lai/Docker/embedding && docker compose up -d
cd /data/projects/lai/Docker/llm && docker compose up -d

# 2. Run the application
cd /data/projects/lai/LAI
uv sync
uv run python -m lai.api.main
# API at http://localhost:8000, docs at http://localhost:8000/docs
```

## Data Processing Pipeline

6-step pipeline for preparing the 672GB corpus for both RAG and fine-tuning:

```bash
cd /data/projects/lai/LAI

# Step 1: Raw files (MinIO) → normalized text segments
uv run python -m lai.pipeline.cli step1 --source "DD Reports/" --dry-run

# Step 2: Segments → parent-child chunks (PostgreSQL)
uv run python -m lai.pipeline.cli step2 --dry-run

# Step 3: Domain classification via Qwen2.5-72B (parent chunks)
uv run python -m lai.pipeline.cli step3 --batch-size 100

# Step 4: Contextual enrichment (Anthropic's approach, child chunks)
uv run python -m lai.pipeline.cli step4 --batch-size 50

# Step 5: Synthetic fine-tuning data generation (~200K Q&A samples)
uv run python -m lai.pipeline.cli step5 --max-samples 200000

# Step 6: Embeddings → pgvector (Qwen3-Embedding-8B)
uv run python -m lai.pipeline.cli step6 --create-indexes
```

All steps are idempotent, support `--dry-run`, and handle graceful shutdown (SIGINT/SIGTERM).

## Documentation

- [Project Status](docs/PROJECT_STATUS.md) — Start here if you're new to the project
- [Architecture Overview](docs/architecture/overview.md)
- [Infrastructure Guide](docs/INFRASTRUCTURE.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Improvement Roadmap](docs/analysis/LAIV5_IMPROVEMENTS.md)
- [Project History (V1-V4)](docs/analysis/LAI_PROJECT_ANALYSIS.md)

## Project Structure

```
src/lai/                  Domain-driven Python packages
  core/                   Config, logging, exceptions, models
  api/                    FastAPI app, middleware, RAG pipeline orchestrator
  auth/                   JWT authentication, user management
  documents/              Upload, parse, chunk, embed, store
  search/                 Query analysis, hybrid search, reranking
  generation/             LLM client, prompts, CRAG, citation verification
  infra/                  Database pool, Redis cache, MinIO client
  pipeline/               Data processing pipeline (6 steps)
    convert.py            Step 1: Raw → normalized segments
    chunk.py              Step 2: Segments → parent-child chunks
    classify.py           Step 3: Domain classification via LLM
    enrich.py             Step 4: Contextual retrieval prefix
    generate.py           Step 5: Synthetic fine-tuning data
    embed.py              Step 6: Embeddings → pgvector
    cli.py                CLI entry points for all steps
    utils/                Text cleaning, German sentence splitting
training/                 Model fine-tuning (separate lifecycle)
tests/                    Unit, integration, E2E tests
docs/                     Documentation
```
