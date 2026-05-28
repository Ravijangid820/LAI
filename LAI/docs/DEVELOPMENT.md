# LAI - Development Guide

> **What's runtime today.** The MVP runtime is a two-service split: the conversational chat (`lai.api.serve_rag`, `:18000`) and the DDiQ due-diligence microservice (`micro-services/`, `:18001` Docker). The frontend is in its own repo, [LAI-UI](https://github.com/Ravijangid820/LAI-UI), conventionally cloned to `/data/projects/lai/LAI-UI/`. `src/lai/` is an installable package (`uv sync` / `pip install -e .`); the **v1 demo restructure** (2026-05-15) collapsed it to a strict-gated `lai.common` foundation plus the runtime packages that actually ship. See [`LAI_V1_STRATEGY.md`](LAI_V1_STRATEGY.md), [`DEMO_STATUS.md`](DEMO_STATUS.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md), and [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Project Structure

`src/lai/` is an installable package — `uv sync` (or `pip install -e .`)
makes `from lai... import ...` work everywhere, with no `sys.path` hacks.
Each subpackage is one **domain** with its own `README.md`; ownership is in
[`.github/CODEOWNERS`](../../.github/CODEOWNERS). See
[`src/lai/README.md`](../src/lai/README.md) for the full package map.

```
LAI/
├── src/lai/                      # Installable package (`lai`)
│   ├── common/                   # Strict-gated shared primitives (the foundation)
│   │   ├── llm/                  # LlmClient (async + sync), strip_think, salvage_json, metrics
│   │   ├── embedding/            # EmbeddingClient + sync façade
│   │   ├── reranker/             # RerankerClient (TEI /rerank)
│   │   ├── retrieval/            # RetrievalClient — pgvector/HNSW (Track B); serve_rag's live retriever
│   │   ├── pdf/                  # PdfExtractor with OCR fallback
│   │   ├── chunk/                # German-legal-aware Chunker
│   │   ├── citation/             # Extract + validate [C-n]/[M-n] handles (strips fabricated)
│   │   ├── jurisdiction/         # Bundesland detection + JurisdictionWarning
│   │   ├── connectors/          # NominatimClient (geocode) + AlkisClient (cadastral WFS), secure XML
│   │   └── auth/                 # JWT auth + tenant isolation
│   ├── api/                      # serve_rag.py (:18000) + auth_router + admin_router
│   │                            #   + share_router + upload_tus (resumable) + metrics + email
│   ├── search/                   # eval.py — recall/RAG eval harness (legacy in-RAM retriever)
│   ├── analyzer/                 # Qwen3.6-27B contract analyzer (playbooks, prompts, schema)
│   ├── pipeline/                 # Offline 6-step corpus build (CLI)
│   └── core/                     # Config, constants, logging, utils, exceptions
│   #
│   # Removed on 2026-05-15 (commit 8431797): old auth/, documents/, extraction/,
│   # generation/, infra/, api/main.py, api/pipeline.py — unwired FastAPI scaffolding.
│   # Capabilities migrated into lai.common; the promised retrieval package shipped
│   # as lai.common.retrieval (pgvector/HNSW). See src/lai/README.md.
│
├── micro-services/               # DDiQ microservice (lai-backend, :18001, Docker)
│   ├── api.py                    # FastAPI app with the /ddiq/* routes
│   ├── ddiq_report.py            # Pipeline + 8 LLM extraction passes
│   ├── cadastral_pipeline.py     # 13-step parcel pipeline + 10H rule
│   ├── _guardrail.py             # validation/guardrail layer (v1)
│   ├── _reconcile.py             # deterministic cross-source reconciler (v1)
│   └── auth_dep.py               # JWT verification dependency (v1)
│
├── infra/monitoring/             # Prometheus + Grafana stack (9-panel lai-rag dashboard)
├── scripts/
│   ├── ops/                      # Entry points: start/stop/status{,-host}.sh, resume_step5/6.sh,
│   │                             # migrate_corpus.py (Track B), load_demo_matter.py
│   ├── eval/                     # Eval harnesses + golden_retrieval_sanity.py
│   ├── db/migrations/            # SQL migrations (auth + tenant; corpus → pgvector)
│   └── archive/                  # Completed one-offs
│
├── tests/                        # Strict-gated unit / integration / e2e
├── docs/
│   ├── adr/                      # Architecture Decision Records 0000–0004
│   ├── LAI_V1_STRATEGY.md        # Master strategy + 10-day roadmap
│   ├── DEMO_STATUS.md            # Live state vs strategy
│   └── UI_GUIDE.md, WORKFLOW.md, PROJECT_STATUS.md, ...
├── demo-seed/                    # Curated demo matters (input to load_demo_matter.py)
├── training/                     # Model fine-tuning (separate lifecycle)
├── Makefile · CONTRIBUTING.md    # `make check` — the single quality gate
├── .pre-commit-config.yaml
└── pyproject.toml                # `lai` v2.0.0, Python ≥ 3.13, uv-managed

# Sibling clone (its own repo — not under LAI/)
/data/projects/lai/LAI-UI/        # Frontend (Vite + React) — github.com/Ravijangid820/LAI-UI
```

**The strict-gated foundation (`lai.common`)** — every new module enters
here under `mypy --strict`, full ruff rule set, ≥85 % branch coverage, and a
clean bandit scan. Legacy paths (serve_rag.py internals, DDiQ, the pipeline)
stay permissive and migrate in module-by-module. See [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

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

1. Prefer landing reusable primitives in **`lai.common`** — that's where the strict gate (`mypy --strict`, ≥85 % branch coverage, full ruff, bandit) lives, and the building blocks (`LlmClient`, `EmbeddingClient`, `RerankerClient`, `Chunker`, `PdfExtractor`, citation/jurisdiction utils) get reused across `serve_rag`, DDiQ, and the pipeline.
2. Wire from the live consumer — `lai.api.serve_rag` for chat; `micro-services/` for DDiQ; `lai.pipeline.cli` for batch. Don't duplicate logic across consumers — pull the shared piece up into `lai.common` instead.
3. If the new code introduces a tunable, add it to `lai.common.<sub>/config.py` (pydantic-settings) and document any environment variables in `.env.example.auth` style.
4. Add tests in `tests/unit/common/<sub>/` (covered by the gate). Integration tests under `tests/integration/`.
5. Architecturally novel decisions get a new ADR under [`docs/adr/`](adr/) (see ADR 0000 for the template).
6. **Run `make check` locally before committing** — CI runs the same. [`CONTRIBUTING.md`](../CONTRIBUTING.md) has the contract.

## Versioning

- Versions are git tags, not directories: `git tag v2.0.0` (the `v1.x` lineage —
  see `pyproject.toml` `version` and the existing `v1.0.0-pre-split` tag)
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
