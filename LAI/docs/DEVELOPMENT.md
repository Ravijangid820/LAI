# LAI - Development Guide

## Project Structure

```
LAI/
├── src/lai/                      # Main Python package (domain-driven)
│   ├── core/                     # Config, constants, models, logging, utils, exceptions
│   ├── api/                      # FastAPI app, middleware, RAG pipeline orchestrator
│   ├── auth/                     # JWT auth, user CRUD, routes
│   ├── documents/                # Chunking, embedding, parsing, document CRUD, routes
│   ├── search/                   # Query analysis, hybrid search, reranking, routes
│   ├── generation/               # LLM client, prompts, CRAG grading, citation verification
│   └── infra/                    # Database pool, Redis cache, MinIO client
│
├── training/                     # Model training (separate lifecycle)
├── tests/                        # Test suites (unit, integration, e2e)
├── docs/                         # Documentation
└── pyproject.toml
```

Each domain package contains its own `routes.py` (API endpoints), business logic, and `repository.py` (database operations).

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
