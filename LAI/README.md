# LAI - German Legal AI Platform

Legal AI platform for wind energy due diligence. Answers legal questions using RAG (Retrieval-Augmented Generation) over a 600GB+ German legal corpus with locally-hosted models.

## Architecture

- **RAG Pipeline:** Query analysis → hybrid search (dense + BM25 + RRF) → cross-encoder reranking → CRAG grading → LLM generation → citation verification
- **Models:** Qwen2.5-7B-Instruct (LLM), BGE-M3 (embedding), ms-marco-MiniLM-L-12-v2 (reranker)
- **Infrastructure:** PostgreSQL + pgvector, Redis, MinIO, vLLM — all self-hosted
- **Multi-tenancy:** Per-user PostgreSQL schemas for uploaded documents

## Quick Start

```bash
# 1. Start Docker services
docker network create lai_network
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/redis && docker compose up -d
cd /data/projects/lai/Docker/embedding && docker compose up -d
cd /data/projects/lai/Docker/llm && docker compose up -d

# 2. Run the application
cd /data/projects/lai/LAI
uv sync
uv run python -m lai.api.main
# API at http://localhost:8000, docs at http://localhost:8000/docs
```

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
training/                 Model fine-tuning (separate lifecycle)
tests/                    Unit, integration, E2E tests
docs/                     Documentation
```
