# LAI Project Status

> Last updated: 2026-04-23

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
| Embedding | Qwen/Qwen3-Embedding-8B (**4096 dims**, max-model-len 32k) via vLLM |
| Reranker | **Qwen/Qwen3-Reranker-8B** via Transformers (multilingual, replaced MiniLM 2026-04) |
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

`src/lai/` is an installable package (`uv sync` / `pip install -e .`) — `from
lai... import ...` works everywhere, no `sys.path` hacks. The **v1 demo
restructure** (2026-05-15) collapsed the previous wide domain layout into a
strict-gated `lai.common` foundation plus the runtime packages that actually
ship. See [`src/lai/README.md`](../src/lai/README.md) for the canonical map.

```
/data/projects/lai/
├── LAI/                              # Application code
│   ├── src/lai/                      # Installable package (`lai`)
│   │   ├── common/                   # Production-grade shared primitives — held to strict
│   │   │   │                         # mypy + ruff + ≥85% coverage + bandit (CONTRIBUTING.md)
│   │   │   ├── llm/                  # LlmClient (async+sync), strip_think, salvage_json, metrics
│   │   │   ├── embedding/            # EmbeddingClient + sync façade
│   │   │   ├── reranker/             # RerankerClient (TEI /rerank)
│   │   │   ├── retrieval/            # RetrievalClient — pgvector/HNSW (Track B); serve_rag's live retriever
│   │   │   ├── pdf/                  # PdfExtractor + OCR fallback
│   │   │   ├── chunk/                # German-legal-aware Chunker
│   │   │   ├── citation/             # [C-n]/[M-n] extract + validate (strips fabricated)
│   │   │   ├── jurisdiction/         # Bundesland detection + JurisdictionWarning
│   │   │   ├── connectors/           # NominatimClient (geocode) + AlkisClient (cadastral WFS)
│   │   │   └── auth/                 # JWT auth + tenant isolation
│   │   ├── api/                      # serve_rag.py (:18000) + auth_router + admin_router
│   │   │                            #   + share_router + upload_tus + metrics + email
│   │   ├── search/                   # eval.py — recall/RAG eval harness (legacy in-RAM retriever)
│   │   ├── analyzer/                 # Qwen3.6-27B contract analyzer (playbooks, prompts, schema)
│   │   ├── pipeline/                 # 6-step corpus build (`python -m lai.pipeline.cli`)
│   │   └── core/                     # Config, logging, exceptions, constants
│   │
│   │   (Deleted on 2026-05-15: old auth/, documents/, extraction/, generation/, infra/, and
│   │    api/main.py + api/pipeline.py — unwired FastAPI scaffolding. Capabilities migrated
│   │    into lai.common; the promised retrieval package shipped as lai.common.retrieval.)
│   │
│   ├── micro-services/               # DDiQ due-diligence report service (:18001, Docker)
│   ├── infra/monitoring/             # Prometheus + Grafana stack (9-panel dashboard)
│   ├── scripts/
│   │   ├── ops/                      # start/stop/status{,-host}.sh, resume_step{5,6}.sh,
│   │   │                             #   migrate_corpus.py (Track B), load_demo_matter.py
│   │   ├── eval/                     # Eval harnesses + golden_retrieval_sanity.py
│   │   ├── db/migrations/            # 001_auth_and_tenant_isolation, 001_corpus_pgvector
│   │   └── archive/                  # Completed one-off migrations, audits, pilots
│   ├── docs/
│   │   ├── adr/                      # Architecture Decision Records (0000-0004 on lai.common.llm)
│   │   ├── LAI_V1_STRATEGY.md        # Master strategy + 10-day roadmap
│   │   ├── DEMO_STATUS.md            # Demo state vs strategy (refreshed during the sprint)
│   │   ├── UI_GUIDE.md               # Per-screen UI design
│   │   └── WORKFLOW.md               # End-to-end data-flow narrative
│   ├── demo-seed/                    # Curated demo matters (input to load_demo_matter.py)
│   ├── training/                     # Model fine-tuning (separate lifecycle)
│   ├── tests/                        # Unit / integration / e2e (strict-gated on lai.common)
│   ├── logs/                         # pipeline/ + host/ + migration/
│   ├── Makefile · CONTRIBUTING.md    # The quality gate contract — `make check`
│   ├── .pre-commit-config.yaml
│   └── pyproject.toml                # `lai` v2.0.0, Python ≥3.13, uv-managed
│
├── .github/
│   ├── CODEOWNERS                    # Per-domain review ownership
│   └── workflows/ci.yml              # ruff + mypy strict + pytest+cov + bandit
└── Docker/                           # Containerized services (one dir per service)
    ├── database/{pgvector,minio,redis}/
    ├── embedding/                    # Qwen3-Embedding-8B vLLM (port 8003, GPU 1)
    ├── reranker/                     # TEI MiniLM (legacy DDiQ-side reranker on :8004)
    ├── llm/                          # Qwen3.6-27B analyzer vLLM (port 8005, GPU 0)
    └── mlflow/                       # Experiment tracking
```

### Why this shape?

`lai.common` is the **discipline layer** — strict mypy + ≥85% branch coverage +
bandit. Every new module enters here under the strict gate; legacy paths
(serve_rag.py, DDiQ, the pipeline) stay permissive and migrate in module-by-
module. This is how the codebase moves from prototype to production without
big-bang rewrites. See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the contract.

---

## Team Ownership

Authoritative ownership is declared in [`.github/CODEOWNERS`](../../.github/CODEOWNERS)
(replace placeholder `@lai/*` handles with real team slugs). Summary:

| Area | Role | What You Touch |
|---------|-------|---------------|
| `lai.common` (all subpackages) | platform/foundation | Strict-gated primitives — every new feature usually starts here. Incl. `retrieval/` (pgvector) + `connectors/` (Nominatim/ALKIS) |
| `lai.pipeline` | data-pipeline | The 6-step corpus build; `python -m lai.pipeline.cli` |
| `lai.search` | retrieval | `eval.py` recall/RAG eval harness; live retrieval is now `lai.common.retrieval` (pgvector) |
| `lai.analyzer` | contract-analyzer | Qwen3.6-27B contract analyzer — playbooks, prompts, schema |
| `lai.api` | api / chat | `serve_rag.py`, `auth_router`, `admin_router`, `share_router`, `upload_tus`, `/metrics`, `/feedback`, `/query/stream` |
| `lai.core` | platform | Config, logging, exceptions, constants |
| `micro-services/` | ddiq | DDiQ due-diligence report service |
| `infra/monitoring/` | platform | Prometheus + Grafana stack |
| `scripts/ops/` | platform / ops | Entry points, migrations, demo seed loader |

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
uv sync                       # installs deps + the `lai` package (editable)

# Runtime today — the two-service split (chat + DDiQ):
bash scripts/ops/start.sh                 # Docker services + serve_rag + Vite
#   or, Docker-free (host processes, no root):
bash scripts/ops/start-host.sh
```

### 3. Run tests

```bash
uv run pytest                    # All tests
uv run pytest tests/unit/        # Unit only
uv run pytest -m "not slow"      # Skip slow tests
```

---

## API Endpoints

The runtime is a two-service split: `serve_rag` (the conversational legal
assistant on `:18000`) and `lai-backend` (the DDiQ multi-doc due-diligence
microservice on `:18001`). Below is the surface area as of the v1 demo build.

### Runtime: serve_rag (`:18000`) — conversational chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Ask a legal question. Response includes `answer`, `chunks` with `[C-n]`/`[M-n]` handles, `citation_validation` (count of fabricated handles stripped), `jurisdiction_warning` (when Bundesland mismatch detected), `timings`, `tokens`. Injects rolling 32-message memory + pinned session facts. |
| `POST` | `/query/stream` | Same as `/query` but **SSE streaming** — token-by-token output for the UI. Trailing event carries the same validation + warning payload. |
| `POST` | `/upload` | Upload PDF/DOCX (OCR via Tesseract de+en, segment, embed, store) and bind to a session |
| `GET`  | `/sessions/{id}/document` | Native PDF stream of an uploaded doc (used by the UI's `<object>` PDF preview when a `[M-n]` chip is clicked) |
| `POST` | `/feedback` | Lawyer thumbs-up / thumbs-down per message (persisted; rehydrates on reload) |
| `GET`  | `/sessions` | List recent chat sessions for the sidebar (light payload, auto-derived titles) |
| `GET`  | `/sessions/{id}` | Full session payload — metadata + last analysis + message history |
| `POST` | `/sessions/{id}/messages` | Append a user/assistant message |
| `PATCH`| `/sessions/{id}` | Set a user-facing title (rename) |
| `DELETE`| `/sessions/{id}` | Delete a session |
| `POST` | `/auth/login`, `/auth/register`, `/auth/me` | JWT auth (via `auth_router`). Tenant isolation enforced per-request. |
| `*`    | `/admin/*` | Org + super-admin endpoints (`admin_router`; org tenancy, invitations — migrations 002–004) |
| `*`    | `/share/*` | Per-session view-only sharing (`share_router`; resource shares — migration 005) |
| `POST` | `/upload` (tus 1.0) | Resumable upload server (`upload_tus`) for VDR-scale documents |
| `GET`  | `/metrics` | Prometheus instrumentation (request latency, token usage, citation-validation counters) — scraped by `infra/monitoring/` |
| `GET`  | `/health` | Liveness — reports `loaded`, `retrieval_backend` (pgvector), `retrieval_ready` |

### Runtime: lai-backend / DDiQ microservice (`:18001`) — multi-doc due-diligence

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ddiq/documents/upload` | Upload PDF (Tesseract OCR + 4096-dim Qwen3-Embedding-8B chunks → pgvector) |
| `GET`  | `/ddiq/documents` | List uploaded DDiQ documents |
| `POST` | `/ddiq/report/generate` | Sync report generation — kept for back-compat; blocks for the entire 30-90 min runtime |
| `POST` | `/ddiq/report/generate/async` | **Preferred.** Returns `{report_id, status:"queued", cached?}` immediately; backend executor runs the pipeline. Request-fingerprint dedup (sorted doc_ids + preset + project_name) returns the cached row instantly when matched. |
| `GET`  | `/ddiq/report/{id}/status` | Cheap status poll (status, step, percent, error) |
| `GET`  | `/ddiq/report/{id}` | Full report payload (sections, WEAs, parcels, findings, timeline, crossDocFindings, grundbuchChecks, rueckbauBond, geojson) |
| `GET`  | `/ddiq/report/{id}/export.docx` | DOCX export of findings — client-deliverable (placeholders for firm letterhead) |
| `GET`  | `/ddiq/report/{id}/geojson` | GeoJSON FeatureCollection for QGIS / ArcGIS / MapBox |
| `DELETE`| `/ddiq/documents/{id}` | Remove an uploaded DDiQ document |
| `GET`  | `/ddiq/reports?limit=N` | Lightweight summary list for the Past Reports browser (no full report_data) |
| `DELETE`| `/ddiq/report/{id}` | Hard-delete a report and cascade through `ddiq_classified_parcels` / `ddiq_contracts` / `ddiq_project_areas` in one transaction |
| `GET`  | `/ddiq/config/map-tiles` | Map tile layer config for the frontend Leaflet map |

> **Auth status (v2.0.0):** JWT auth + tenant isolation are built and merged to `master` (`lai.common.auth`, `api/auth_router.py` + `admin_router.py` + `share_router.py`, migrations `001`–`005`). Org tenancy, super-admin, invitations and per-session sharing all landed in the v2 cut. Confirm per-route enforcement against the deep-review checklist before external production use.

> **Deferred to v1.1 — now shipped.** The retrieval package that the dead-stack note (`8431797`, 2026-05-15) promised "returns in v1.1" shipped as **`lai.common.retrieval`** (pgvector/HNSW). A unified `lai.api.main` FastAPI app consolidating all routers is still the longer-term design target; today the runtime is `serve_rag.py` + its mounted routers. See [`docs/TECHNICAL_DOCUMENTATION.md`](TECHNICAL_DOCUMENTATION.md) for the current authoritative technical reference.

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

- **Code versions:** Git tags on the `v1.x` lineage (`v1.0.0-pre-split`, `v2.0.0`, ...) — no version directories. `pyproject.toml` `version` tracks the same scheme.
- **Model versions:** MLflow run IDs — every training run is logged
- **Feature flags:** Config toggles (`crag.enabled`, etc.) control what's active
- **Rollback:** `git checkout v2.0.0` for code, MLflow artifact download for models

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

## What's Done (v2.0.0)

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
- [x] Location/geo extraction prototype (was `lai.extraction`) — LLM-based extraction of geocodable addresses, Flurstücke, coordinates from legal documents. Package was deleted on 2026-05-15 along with the dead FastAPI stack; the live geocoding path is now inside DDiQ (`cadastral_pipeline.py` + Nominatim). A revived `lai.extraction`-equivalent returns in v1.1.
- [x] Extraction API endpoints (single, batch, summary)
- [x] Extraction smoke-tested (the ad-hoc `scripts/test_extraction.py` was removed in the v2 restructure — superseded by the `lai.extraction` package + `scripts/eval/smoke_test_analyzer.py`)

---

## Pipeline Execution Progress (Phase 1)

Processing is done in phases due to storage constraints (~613GB free).

### Phase 1 — High-value sources (~20GB)

| Source | Step 1 (Convert) | Step 2 (Chunk) | Step 3 (Classify) | Step 4 (Enrich) | Step 5 (Generate) | Step 6 (Embed) |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| DD Reports (19MB, 18 files) | Done | Done | Done | Done | Done | Done |
| VDRs (6GB, 4.3K files) | Done (103 .xls/.doc failed) | Done | Done | Done | Done | Done |
| de/gesetzes (750MB, 764 files) | Done | Done | Done | Done | Done | Done |

**Phase 1 all steps complete (as of 2026-04-22):**
- **Step 2:** 12,307 files → 134,474 parent chunks, 217,165 child chunks (2m 35s)
- **Step 3:** reclassified with improved JSON parser + versioned history
- **Step 4:** 217K child chunks enriched with context prefix (4h 51m, 16 concurrent)
- **Step 5:** 200,006 fine-tuning samples generated (target: 200,000; overshoot by 6)
- **Step 6:** 217,165 child chunks embedded with Qwen3-Embedding-8B (4096-dim, halfvec on PG, fp32 BLOB in SQLite; 1h 36m)

**Embedding storage change (2026-04-22):**
- Dimension **1024 → 4096** (Qwen3-Embedding-8B's native, no Matryoshka support)
- Schema **`vector(1024)` → `halfvec(4096)`** on PostgreSQL (migration [`02_migrate_halfvec.sql`](../../Docker/database/pgvector/init/02_migrate_halfvec.sql))
- No HNSW index (4096 dims exceeds pgvector's 4000 halfvec limit) — use exact cosine search with pre-filters
- In `--local` mode, embeddings live in a dedicated `child_embeddings(child_id PK, embedding BLOB)` SQLite table (INSERT is ~100× faster than UPDATEing a BLOB column on the main `child_chunks` table)

### Phase 2 — Court decisions + legal reference (~20 GB)

Original size estimates were off; the corpora are much bigger than docs said:

| Source | Actual size | Cases | Step 1 | Step 2 | Step 6 | Notes |
|--------|---|---|:-:|:-:|:-:|---|
| **hf_cases** | 13 GB | **251,038** | ✅ done (2026-04-23) | pending | pending | Custom processor: `scripts/archive/temp/process_court_decisions.py` |
| **openlegaldata** | 1.5 GB | **41,740** | ✅ done (2026-04-23) | pending | pending | Same processor; 0.2% overlap with hf_cases, dedupe by ECLI/slug |
| **Library** | 5.4 GB | 2,326 PDFs | pending | pending | pending | Use existing Step 1 Docling path |

**Phase 2 Step 1 results** (court decisions only, PDFs pending):
- **292,486 emitted** from 292,778 seen (99.9%)
- 284 skipped (empty content), 8 dedupes (ECLI collisions)
- Runtime: 191 s total → 6.8 GB of segments JSONL in 586 batch files
- Doc types: urteil 157K / beschluss 132K / gerichtsbescheid 1.3K / sonstige 1.7K
- Courts cover: OLG, VG, OVG, BGH, LG, LAG, LSG, FG, AG, SG, VGH + all Bundesgerichte

Pipeline steps 3-5 (classify/enrich/synth) **deliberately skipped for Phase 2** —
they depend on the 72B teacher which we found fabricates citations in
15.8% of samples (see *Known Issues*). RAG retrieval is now the focus.

### Phase 3 — Large corpus (deferred)

| Source | Size | Notes |
|--------|---|---|
| multilegalpile | 643 GB | 96% non-German; filter to `de` (~30-50 GB) before processing |

Deferred until Phase 2 is fully embedded and retrieval quality re-measured.

---

## Fine-tuning (complete 2026-04-23 — shelved for now)

Qwen2.5-7B-Instruct was LoRA-fine-tuned on the 200K synthetic samples
from Step 5. Best checkpoint: **checkpoint-23000, eval_loss 0.553,
token_accuracy 85.6%** (from 0.977 / 76% at step 1000). Merged adapter
at `/data/projects/lai/models/qwen25-7b-legal-lora-v2-merged` (14.2 GB).

**Why shelved**: a quality audit (`scripts/archive/audit_training_data.py`)
revealed **15.8% of legal citations in training answers are fabricated**
by the 72B teacher. `rag_qa` (our core task) has an 18.8% citation
fabrication rate. End-to-end RAG testing confirmed the FT model still
hallucinates list-type items. Decision: improve RAG first, revisit
training later with cleaner synthetic-data generation (stricter prompts
+ post-generation verification loop).

**Data prep** — [training/fine_tuning/scripts/export_training_data.py](../training/fine_tuning/scripts/export_training_data.py)
exports `training_samples` from the local SQLite to ChatML JSONL with a
95/5 stratified split by task_type:

| Task            | Train   | Val   |
|-----------------|--------:|------:|
| rag_qa          | 64,163  | 3,377 |
| classify_qa     | 28,120  | 1,479 |
| compare         | 27,957  | 1,471 |
| summarize       | 27,344  | 1,439 |
| explain         | 26,832  | 1,412 |
| extract         | 9,354   |   492 |
| refusal         | 6,238   |   328 |
| **Total**       | **190,008** | **9,998** |

**Trainer** — [training/fine_tuning/scripts/run_lora.py](../training/fine_tuning/scripts/run_lora.py)
uses TRL SFTTrainer + PEFT LoRA on a 4-bit-quantized base (bnb NF4, double
quant, bf16 compute, paged_adamw_8bit). No Unsloth dep. Best checkpoint
is kept automatically (`load_best_model_at_end`).

**Config in use:**
- LoRA r=128, α=256, dropout 0.05 on all 7 Qwen projection matrices
- effective batch = 16 (per-device 2 × grad-accum 8)
- eval batch = 8 (no gradients → safe to be larger; 4× faster eval)
- cosine LR 2e-4, warmup 3%, 2 epochs, max_seq_len 4096
- gradient_checkpointing OFF (RTX Pro 6000 has headroom; ~30% faster)
- `PYTORCH_ALLOC_CONF=expandable_segments:True` to avoid fragmentation OOM
- eval + save every 1000 steps (≈20 evals over a ~24K-step run)

**Expected:** ~14h for 2 epochs (with load_best picking the lowest eval_loss checkpoint).

**Lessons learned during tuning** (documented here so nobody repeats them):
- `per_device_batch=4` triggers a 3.8 GB logits-tensor spike inside TRL's
  loss path (`shift_logits.contiguous()`) and OOMs even when baseline is 89 GB.
  Stick to 2 for training, 8 for eval.
- `eval_strategy="steps"` with `per_device_eval_batch_size=per_device_batch`
  ends up spending 50% of wall time on eval (10K val samples × 2 bs ≈ 10 min/eval).
  Use a separate, larger `--eval-batch` and crank up `--eval-steps`.
- `attn_implementation="flash_attention_2"` hard-fails if flash-attn isn't
  installed. `run_lora.py::_pick_attn_impl` auto-downgrades to SDPA.

## RAG Retrieval (measured 2026-04-23)

Best pipeline today:

```
Query  →  Qwen3-Embedding-8B (with query prefix)
           + BM25 over SQLite FTS5
           → RRF fusion (top 50 candidates)
           → Qwen3-Reranker-8B (top 10)
```

### 100-query smoke-test results (2026-04-22, initial read)

| Mode | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| dense baseline | 26% | 46% | 58% | 65% | 0.381 |
| dense + Qwen3 query prefix | 30% | 48% | 55% | 63% | 0.407 |
| bm25 only | 29% | 39% | 47% | 52% | 0.360 |
| hybrid (dense + bm25) | 33% | 55% | 61% | 66% | 0.447 |
| hybrid + prefix | 38% | 56% | 61% | 68% | 0.480 |
| hybrid + prefix + Qwen3-Reranker-8B | 40% | 61% | 75% | 80% | 0.531 |

### 500-query audit (2026-04-23, honest baseline)

| Mode | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| **hybrid + prefix + Qwen3-Reranker-8B** | **23.2%** | **46.2%** | **58.0%** | **70.0%** | **0.373** |

**The larger sample revealed the first 100 queries were an easier subset.**
The real baseline is weaker: R@5 = 58% and R@1 = 23% — meaning we miss
the right chunk in the top 5 on nearly half of queries. This is where
**metadata filtering** and **more corpus coverage** (Phase 2) matter
most. Reranker still helps — raw dense baseline would be worse — but
the ceiling at R@10 = 70% means ~30% of queries genuinely can't find
their gold chunk in the current corpus even with the best retriever.

Phase 2 (290K court decisions being added) should lift this substantially
for queries whose gold answer exists in court decisions.

**Key module**: [`lai.search.eval`](../src/lai/search/eval.py) — `python -m
lai.search.eval` runs any of the 6 modes above on N val queries; per-query
and aggregated metrics saved to `scripts/eval/rag_eval_results/`.

## What's Next

Ordered by leverage:

1. **Chunk + embed Phase 2** — run existing Step 2 and Step 6 on the
   586 court-decisions batch files already written. Expect +~1M chunks
   added to `pipeline_local.db` (overnight: Step 6 embedding at ~25/s).
2. **Process Library PDFs** via existing Step 1 (Docling) with
   `--source Libary/` — 2,326 files, ~2-3 h.
3. **Metadata filter at query time** — `child_chunks` now has rich
   metadata (court_name, court_level, jurisdiction, decision_date,
   ecli, file_number). Add pre-filters for "since 2020", "BGH only",
   "Verwaltungsrecht only" before retrieval — highest-leverage quality
   win we haven't pulled.
4. **Re-measure retrieval** on the bigger corpus. Expect R@5 → 85%+.
5. **Citation verifier at query time** — regex-extract §§ / case IDs
   from the generated answer, confirm each appears in the retrieved
   chunks; reject + retry if fabricated.
6. **Regenerate training data with verification loop** (when we come
   back to fine-tuning) — stricter prompts and post-gen check that
   every citation is grounded.
7. **Phase 3** — multilegalpile German subset (~30-50 GB after filter).
8. **Geocoding, German reranker, CI/CD, Alembic** — unchanged priorities
   from before.

---

## Known Issues

| Issue | Impact | Status |
|-------|--------|--------|
| Phase 1 Steps 1-6 complete; fine-tune complete | Baseline RAG works | Shelved fine-tune, focusing on RAG quality |
| **15.8% of training citations are fabricated** | FT model hallucinates §§/clauses | Captured in `scripts/archive/audit_training_data.py`; regenerate with verification loop before retraining |
| GPU contention with shared users | Training/eval may OOM | `./scripts/ops/resume_step5.sh --status` to diagnose; resume cleanly via SQLite checkpoint |
| No HNSW index on embeddings | 4096 dims > halfvec HNSW limit of 4000 | Use exact cosine search with metadata pre-filters |
| `openlegaldata_api_dump/` has 4,174 legacy pre-V5 segment files | Will be picked up by Step 2 alongside our 84 new batches — may create noise | Inspect before Step 2; either delete or verify schema match |
| Phase 2 chunk+embed pending | 290K court decisions processed but not yet in DB | Steps 2 + 6 next |
| Phase 3 (multilegalpile 643 GB) not processed | Low priority; 96% non-German | Defer until Phase 2 retrieval measured |
| 103 VDR files failed Step 1 | Mostly legacy .xls/.doc formats | Install LibreOffice for conversion |
| Reranker **fixed** | Was English-only MiniLM | Now Qwen3-Reranker-8B (multilingual SOTA) |
| No CI/CD | Manual testing only | Set up GitHub Actions |
| `LAI/embedding_server/` (2.2GB) | Old BGE-M3 cache, not used | Safe to delete |

---

## Docker-free Operation (added 2026-04-12)

The pipeline can run with **only the LLM container** (no PostgreSQL, no MinIO, no Redis). All pipeline state lives in SQLite.

### Resume the running pipeline (one command)
```bash
./scripts/ops/resume_step5.sh           # starts vLLM container + Step 5
./scripts/ops/resume_step5.sh --status  # show progress
./scripts/ops/resume_step5.sh --stop    # stop Step 5 (keeps LLM up)
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
python scripts/db/export_to_sqlite.py all
```

---

## Where to Find Things

| What | Where |
|------|-------|
| App config | [src/lai/core/config.py](../src/lai/core/config.py) |
| Data pipeline | [src/lai/pipeline/](../src/lai/pipeline/) — Steps 1-6 |
| Shared primitives (v1 foundation) | [src/lai/common/](../src/lai/common/) — `llm`, `embedding`, `reranker`, `pdf`, `chunk`, `citation`, `jurisdiction`, `auth` |
| Chat backend (serve_rag) | [src/lai/api/serve_rag.py](../src/lai/api/serve_rag.py) — `python -m lai.api.serve_rag` |
| Retrieval eval harness | [src/lai/search/eval.py](../src/lai/search/eval.py) — `python -m lai.search.eval` |
| Geocoding / Flurstück extraction | DDiQ — [micro-services/cadastral_pipeline.py](../micro-services/cadastral_pipeline.py) (Nominatim + ALKIS WFS) |
| Per-domain ownership | [.github/CODEOWNERS](../../.github/CODEOWNERS) + per-package `README.md` under `src/lai/` |
| Pipeline progress report | [PIPELINE_PROGRESS_REPORT.md](PIPELINE_PROGRESS_REPORT.md) |
| Pipeline CLI | `python -m lai.pipeline.cli step1 --help` |
| Local mode (no PostgreSQL) | `python -m lai.pipeline.cli step2 --local` — see [local_storage.py](../src/lai/pipeline/local_storage.py) |
| Resume Step 5 (one-shot) | `./scripts/ops/resume_step5.sh` — auto-starts vLLM container + Step 5 |
| SQLite export of all DB data | `python scripts/db/export_to_sqlite.py all` — creates portable `.db` files |
| SQLite exports (location) | `LAI/processed/db_export/pipeline.db` (1GB) and `app.db` (284GB) |
| Export training data to JSONL | `python -m training.fine_tuning.scripts.export_training_data` |
| Run LoRA fine-tune | `python -m training.fine_tuning.scripts.run_lora --epochs 2` (see script for all flags) |
| Training outputs | `training/fine_tuning/output/qwen25-7b-legal-lora/` (adapter + best checkpoint) |
| **Process court decisions** | `python scripts/archive/temp/process_court_decisions.py --source all` (handles hf_cases + openlegaldata, writes Step-1-compatible segments) |
| **Training-data quality audit** | `python scripts/archive/audit_training_data.py` (citations verified against parent chunks; found 15.8% fabrication rate) |
| **Retrieval eval harness** | `python -m lai.search.eval --mode hybrid_rerank --n 500` (6 modes; writes results to `scripts/eval/rag_eval_results/`) |
| **Retrieval failure analysis** | `python scripts/eval/rag_audit_analysis.py <results.json>` (breaks down recall by task, specificity, doc_type) |
| **End-to-end RAG test** | `python scripts/eval/rag_generate_test.py --n 5` (retrieve + generate with base + FT, side-by-side) |
| **Raw corpus layout** | `LAI/data/lai-raw/` (671 GB source docs) + `LAI/data/lai-segments/` (1.7 GB Step-1 output) — moved from `minio-backup/` 2026-04-23 |
| RAG pipeline (chat path) | [src/lai/api/serve_rag.py](../src/lai/api/serve_rag.py) — orchestrates retrieve → rerank → generate → validate citations → jurisdiction check |
| Retrieval kernel (Corpus + dense + BM25 + RRF + Reranker) | [src/lai/search/eval.py](../src/lai/search/eval.py) |
| LLM client / prompt building / JSON salvage / think-strip | [src/lai/common/llm/](../src/lai/common/llm/) |
| Citation handle validation | [src/lai/common/citation/](../src/lai/common/citation/) |
| Jurisdiction (Bundesland) check | [src/lai/common/jurisdiction/](../src/lai/common/jurisdiction/) |
| Docker services | [/data/projects/lai/Docker/](../../../Docker/) |
| Infrastructure docs | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
| Architecture overview | [architecture/overview.md](architecture/overview.md) |
| Improvement roadmap | [analysis/LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) |
| Project history (V1-V4) | [analysis/LAI_PROJECT_ANALYSIS.md](analysis/LAI_PROJECT_ANALYSIS.md) |
