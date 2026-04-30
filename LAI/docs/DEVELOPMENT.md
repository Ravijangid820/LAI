# LAI - Development Guide

> **What's runtime today.** The MVP runtime is a two-service split: the conversational chat (`scripts/serve_rag.py`, `:18000`) and the DDiQ due-diligence microservice (`micro-services/`, `:18001` Docker). The frontend is in its own repo, [LAI-UI](https://github.com/Ravijangid820/LAI-UI), conventionally cloned to `/data/projects/lai/lai-ui/`. The `src/lai/` domain-driven backend below is the v5 design target — its FastAPI app (`lai.api.main`) is not yet wired into the runtime that ships. See [`MVP_DELIVERY.md`](MVP_DELIVERY.md) and [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Project Structure

```
LAI/
├── src/lai/                      # Planned-v5 Python package (domain-driven)
│   ├── core/                     # Config, constants, models, logging, utils, exceptions
│   ├── api/                      # FastAPI app, middleware, RAG pipeline orchestrator
│   ├── auth/                     # JWT auth, user CRUD, routes
│   ├── documents/                # Chunking, embedding, parsing, document CRUD, routes
│   ├── search/                   # Query analysis, hybrid search, reranking, routes
│   ├── generation/               # LLM client, prompts, CRAG grading, citation verification
│   ├── infra/                    # Database pool, Redis cache, MinIO client
│   └── pipeline/                 # Data processing pipeline (6 steps, CLI) — runtime today
│
├── scripts/
│   └── serve_rag.py              # Runtime conversational chat backend (:18000)
├── micro-services/               # Runtime DDiQ microservice (lai-backend, :18001)
│   ├── api.py                    # FastAPI app with the /ddiq/* routes
│   ├── ddiq_report.py            # Pipeline + extraction passes (Evidence, Timeline, Grundbuch, Rückbau, ...)
│   ├── cadastral_pipeline.py     # 13-step parcel pipeline + 10H rule
│   ├── docker-compose.yml        # Container definition
│   └── Dockerfile
│
├── training/                     # Model training (separate lifecycle)
├── tests/                        # Test suites (unit, integration, e2e)
├── docs/                         # Documentation
└── pyproject.toml

# Sibling clone (its own repo — not under LAI/)
/data/projects/lai/lai-ui/        # Frontend (Vite + React) — github.com/Ravijangid820/LAI-UI
```

Each domain package under `src/lai/` contains its own `routes.py` (API endpoints), business logic, and `repository.py` (database operations).

## Quick Start

### 1. Set up environment
```bash
cd /data/projects/lai/LAI
uv venv
uv sync
```

### 2. Start infrastructure
```bash
docker network create lai_network
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/redis && docker compose up -d
cd /data/projects/lai/Docker/embedding && docker compose up -d
cd /data/projects/lai/Docker/reranker && docker compose up -d
cd /data/projects/lai/Docker/llm && docker compose up -d
```

### 3. Run the API
```bash
cd /data/projects/lai/LAI
uv run python -m lai.api.main
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 4. Run tests
```bash
uv run pytest                            # All tests
uv run pytest tests/unit/                # Unit tests only
uv run pytest -m "not slow"              # Skip slow tests
uv run pytest --cov=src/lai              # With coverage
```

## Configuration

All settings are in `src/lai/core/config.py`, loaded from environment variables.

```python
from lai.core.config import get_settings

settings = get_settings()
print(settings.db.host)           # Database host
print(settings.llm.max_tokens)    # LLM max tokens (4096)
print(settings.retrieval.final_k) # Final retrieval count (7)
```

Settings are cached. To reload: `get_settings.cache_clear()`

## Logging

```python
from lai.core.logging import get_logger, setup_logging, trace_operation

# Get a logger for your module
logger = get_logger("lai.search.hybrid_search")
logger.info("Search completed in %.1fms", elapsed)

# Trace an operation with timing
async with trace_operation("retrieve", request_id) as ctx:
    results = await hybrid_search(query)
    ctx.record_tokens(token_usage)
# ctx.metrics now has duration_ms, token_usage, success
```

## Data Processing Pipeline

The `lai.pipeline` package processes the raw legal corpus (MinIO) into RAG-ready embeddings and fine-tuning data. Run via CLI:

```bash
# Each step is idempotent and supports --dry-run
python -m lai.pipeline.cli step1 --source "DD Reports/" --dry-run  # Raw → segments
python -m lai.pipeline.cli step2                                    # Segments → parent-child chunks
python -m lai.pipeline.cli step3 --batch-size 100                   # Domain classification (LLM)
python -m lai.pipeline.cli step4 --batch-size 50                    # Contextual enrichment (LLM)
python -m lai.pipeline.cli step5 --max-samples 200000               # Fine-tuning data (LLM)
python -m lai.pipeline.cli step6 --create-indexes                   # Embeddings → pgvector
```

Pipeline modules are in `src/lai/pipeline/`:
- `convert.py` — Docling for PDF/DOCX, custom parsers for JSON/JSONL datasets
- `chunk.py` — Parent-child chunking with German legal sentence splitting
- `classify.py` — 12 wind-energy legal domains via Qwen2.5-72B
- `enrich.py` — Contextual retrieval prefix (Anthropic's approach)
- `generate.py` — Synthetic Q&A in ChatML format (7 task types)
- `embed.py` — Qwen3-Embedding-8B vectors + German tsvector for BM25
- `cli.py` — CLI orchestrator with progress logging and timing

All steps log extensively to aid debugging. Logs include step timing, batch progress, API errors, and per-document statistics.

## Adding a New Feature

1. Add code to the appropriate domain package (`documents/`, `search/`, `generation/`, `auth/`)
2. Each package is self-contained: routes, logic, and repository in one place
3. Shared utilities go in `core/`, infrastructure clients in `infra/`
4. Add tests in `tests/unit/` or `tests/integration/`
5. If it needs a new config value: add to the appropriate settings class in `config.py`
6. If it needs a feature flag: add a `bool` field with default `False`

## Versioning

- Versions are git tags, not directories: `git tag v5.0.0`
- Feature flags in config control what's active: `crag.enabled = true`
- Model versions tracked in MLflow (http://localhost:5000)

## Code Style

- Python 3.13+
- `uv` for package management
- Ruff for linting: `uv run ruff check src/`
- Type hints on all public functions
- Docstrings on modules and classes (not every method)
- German for user-facing text (prompts, error messages)
- English for code, comments, and logs
