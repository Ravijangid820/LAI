# LAI — Complete Technology & Implementation Inventory

**Date:** 2026-05-14
**Purpose:** Exhaustive list of every technology, framework, model, service, and
dependency actually present in the LAI implementation — cross-checked against
source, manifests, Docker config, and the live running system. Where the
documented/configured state differs from what is actually running, that gap is
called out explicitly (see §14).

**Evidence basis:** `LAI/pyproject.toml`, `LAI/uv.lock`, `LAI/micro-services/requirements.txt`,
`LAI-UI/package.json`, all `docker-compose.yml` files, all `.env`/`.env.example`
files, source imports across `LAI/src/lai/` + `LAI/micro-services/`, live
`docker ps` / `ss -tlnp` / `nvidia-smi`.

---

## 1. System at a glance

LAI is a German-language legal-AI platform for wind-energy due diligence. It is
built from **three deployable units** plus a shared model/data layer:

| Unit | Language | Runtime | Purpose | Port |
|------|----------|---------|---------|------|
| `serve_rag` (RAG/chat backend) | Python 3.13 | host process | Conversational legal assistant, document upload, contract analyzer | 18000 |
| `lai-backend` / DDiQ microservice | Python 3.11 | Docker container | Async multi-document due-diligence report generation + cadastral pipeline | 18001 |
| `LAI-UI` (frontend) | TypeScript / React 19 | Vite dev server / Vercel / Cloudflare | Web UI | 5173 (dev) |
| Data pipeline (`lai.pipeline`) | Python 3.13 | host process / CLI | 6-step corpus ingestion + fine-tuning data prep | n/a (batch) |

Supporting services: vLLM model servers (LLM, embedding), PostgreSQL+pgvector,
Redis, MinIO, SQLite, plus an in-process cross-encoder reranker.

> **Note:** `serve_rag` and `lai.pipeline` both live in the `LAI/src/lai/`
> package (Python 3.13, managed by `uv`). The DDiQ microservice is a **separate
> codebase** (`LAI/micro-services/`, Python 3.11, plain `pip`/`requirements.txt`,
> not part of the `lai` package). A third, **legacy** RAG service exists at
> `Docker/inference_engine/` — see §6.

---

## 2. Languages & runtimes

| Technology | Version | Where | Evidence |
|-----------|---------|-------|----------|
| Python | **3.13** (`>=3.13`) | `LAI/` core package (`lai` — serve_rag + pipeline) | `LAI/pyproject.toml`, `LAI/.python-version` |
| Python | **3.11-slim** | DDiQ microservice container | `LAI/micro-services/Dockerfile` |
| Python | 3.11/3.12 | legacy `Docker/inference_engine/` | `Docker/inference_engine/requirements.txt` |
| TypeScript | **5.8.3** | frontend | `LAI-UI/package.json` |
| Node.js | (via Vite 7 / Wrangler 4) | frontend build/dev | `LAI-UI/package.json` |
| Bash | — | ops scripts (`LAI/scripts/ops/*.sh`, `LAI/ops/*.sh`) | repo |
| SQL | PostgreSQL 16 dialect + SQLite dialect | schemas | `persistence.py`, `ddiq_report.py` |

**Package managers:** `uv` (core Python package, lockfile `uv.lock`), `pip`
(DDiQ microservice + legacy engine, unpinned `requirements.txt`), `npm`
(frontend, lockfile `package-lock.json`), `hatchling` (Python build backend).

---

## 3. Backend — RAG / chat service (`serve_rag`)

**Entry point:** `LAI/src/lai/api/serve_rag.py` — launched as
`python -m lai.api.serve_rag --port 18000` (host process, not Docker).

### Frameworks & libraries (actually imported)

| Technology | Resolved version | Role |
|-----------|------------------|------|
| FastAPI | 0.135.1 | HTTP API framework |
| Uvicorn | 0.41.0 | ASGI server (single worker) |
| Pydantic | 2.12.5 | request/response models |
| httpx | 0.28.1 | async HTTP client to vLLM endpoints |
| NumPy | 2.2.6 | embedding math (in-RAM corpus matrix, cosine search) |
| PyTorch | 2.10.0 | GPU tensors for the reranker / optional local LLM |
| Transformers | 4.57.6 | `AutoModelForCausalLM`, `AutoTokenizer` (optional local LLM path) |
| Docling | 2.77.0 | PDF/DOCX/HTML → text+tables ingestion |
| SQLite (`sqlite3`, stdlib) | — | session/message persistence + 8M-embedding corpus store |

### Sub-modules used by `serve_rag`

- `lai.search.eval` — dense retrieval, BM25, RRF fusion, in-process `Reranker`.
- `lai.analyzer` — V2 contract analyzer (`pipeline.py`, `llm_client.py`,
  `prompts.py`, `playbooks.py`, `reconciler.py`, `cadastral_ner.py`, `schema.py`).
- `lai.persistence` — SQLite-backed `sessions` + `messages` tables.

### Endpoints (12)

`GET /health`, `POST /query`, `POST /upload`, `POST /analyze-contract`,
`GET /analyze-contract/progress`, `GET /analyze-contract/full`,
`GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/messages`,
`POST /sessions/{id}/messages`, `DELETE /sessions/{id}`, `PATCH /sessions/{id}`.

### Key implementation facts

- Loads ~8M child embeddings into RAM (~127 GB) as a NumPy float32 matrix at
  startup; retrieval is exact cosine (`corpus.embs @ q_vec`).
- Reranker: `Qwen/Qwen3-Reranker-8B` loaded **in-process on GPU** via
  Transformers (`serve_rag.py:852`).
- LLM: defaults to the **remote** Qwen3.6-27B analyzer vLLM endpoint
  (`LLM_MODEL=qwen3.6-27b`); an optional local-model path via Transformers
  exists but is not the default.
- Conversational memory: rolling 32-message window + LLM-extracted "pinned"
  stable facts, with vLLM prefix caching.
- `CORS allow_origins=["*"]`; **no authentication on any endpoint.**

---

## 4. Backend — DDiQ microservice (`lai-backend`)

**Entry point:** `LAI/micro-services/api.py` (`uvicorn api:app --workers 2`),
containerized. `api.py` mounts `ddiq_report.py`'s router under `/ddiq`.

**Files:** `api.py` (~22 KB), `ddiq_report.py` (~129 KB / 2,463 lines),
`cadastral_pipeline.py` (~54 KB).

### Frameworks & libraries (own `requirements.txt`, unpinned `>=`)

| Technology | Declared | Role |
|-----------|----------|------|
| FastAPI | `>=0.104.0` | HTTP API |
| Uvicorn[standard] | `>=0.24.0` | ASGI server (2 workers) |
| Pydantic | `>=2.5.0` | ~18 data models (`Finding`, `WEAStatus`, `DDiQReportData`, …) |
| python-dotenv | `>=1.0.0` | env loading |
| python-multipart | `>=0.0.9` | file-upload parsing |
| psycopg2-binary | `>=2.9.9` | **synchronous** PostgreSQL driver + connection pool |
| requests | `>=2.31.0` | synchronous HTTP to LLM / embedding / reranker / WFS |
| PyMuPDF (`fitz`) | `>=1.23.0` | PDF text extraction |
| pytesseract | `>=0.3.10` | OCR fallback for scanned PDFs |
| Pillow | `>=10.0.0` | image handling for OCR |
| NumPy | `>=1.24.0` | embedding math, reranking |
| Shapely | `>=2.0.0` | polygon/parcel spatial operations (cadastral) |

**System packages in the image:** `tesseract-ocr` + `tesseract-ocr-deu` +
`tesseract-ocr-eng` (OCR), `libgeos-dev` (Shapely C backend), `curl`.

### Endpoints (15, under `/ddiq` prefix + 3 on `api.py` root)

`api.py` root: `GET /health`, `POST /upload`, `POST /query`.
`ddiq_report.py` router: `GET /documents`, `POST /documents/upload`,
`POST /report/generate/async`, `GET /report/{id}/status`,
`POST /report/generate`, `GET /reports`, `GET /report/{id}`,
`DELETE /report/{id}`, `GET /report/{id}/geojson`, `GET /report/{id}/validate`,
`POST /project-area`, `GET /config/map-tiles`, plus startup/shutdown hooks.

### Key implementation facts

- Async report generation via a `ThreadPoolExecutor` worker; request-fingerprint
  dedup; incremental JSONB checkpointing for crash recovery; `reap_orphans()` on
  startup.
- 8 LLM extraction passes (section analysis, timeline, cross-doc consistency,
  Rückbau bond, Grundbuch match, WEA status, infrastructure, findings).
- Talks to Qwen3.6-27B analyzer (`LLM_URL`), embedding service
  (`EMBEDDING_URL`), and the host's in-process reranker (`RERANKER_URL` via
  `host.docker.internal`).
- **No authentication, no tenant isolation** — all reports/documents globally
  visible.

---

## 5. Data pipeline (`lai.pipeline`)

**Entry point:** `python -m lai.pipeline.cli step{1..6}` (host process / CLI).
Part of the `LAI/` Python 3.13 package — shares its dependency set (§9).

| Step | Module | Technology used |
|------|--------|-----------------|
| 1 — convert | `convert.py` | Docling, `ProcessPoolExecutor`, Tesseract OCR |
| 2 — chunk | `chunk.py` | custom German sentence splitter, parent/child chunking |
| 3 — classify | `classify.py` | Qwen2.5-72B-Instruct-AWQ via vLLM (HTTP) |
| 4 — enrich | `enrich.py` | LLM contextual-retrieval prefixes (HTTP) |
| 5 — generate | `generate.py` | LLM synthetic Q&A (~200K samples) (HTTP) |
| 6 — embed | `embed.py` / `cli.py` | Qwen3-Embedding-8B via vLLM, pgvector inserts, NPZ backups |

**Storage modes:** PostgreSQL+pgvector (default) **or** SQLite
(`processed/pipeline_local.db`) for Docker-free operation
(`pipeline/local_storage.py`). Utilities: `utils/german_splitter.py`
(legal-abbreviation-aware), `utils/text_cleaner.py` (OCR-artifact repair).

---

## 6. Legacy RAG service — `Docker/inference_engine/` (NOT LIVE)

A previous-generation RAG engine (~5,800 LOC; files dated Dec 2025 – Feb 2026).
Its compose file is explicitly `restart: "no" # DEAKTIVIERT`.

- Frameworks: FastAPI, Uvicorn, **gunicorn**, requests, Pydantic, psycopg2,
  python-dotenv, email-validator.
- Model (configured): `meta-llama/Meta-Llama-3-8B-Instruct`.
- Files: `smart_rag_engine.py` (58 KB), `query_classifier.py`,
  `response_validator.py`, `retrieval_client.py`, `user_document_client.py`,
  `llm_client.py`, etc.
- **Contains a real HuggingFace token in `.env`** — see §14.

This is documented here for completeness; it is **not part of the current
runtime**.

---

## 7. Frontend — `LAI-UI`

**Build:** `tsc -b && vite build`. **Dev:** `vite` (port 5173).
**Deploy targets:** Vercel (primary, `vercel.json`) and/or Cloudflare Workers
(`wrangler.json`, `@cloudflare/vite-plugin`).

### Runtime dependencies

| Technology | Version | Role |
|-----------|---------|------|
| React | 19.0.0 | UI framework |
| react-dom | 19.0.0 | DOM renderer |
| react-router | ^7.5.3 | client-side routing |
| radix-ui | ^1.4.3 | headless UI primitives (shadcn-style) |
| lucide-react | ^0.510.0 | icon set |
| react-markdown | ^10.1.0 | markdown rendering (chat answers) |
| leaflet + react-leaflet | ^1.9.4 / ^5.0.0 | project-location maps |
| zod | ^3.24.3 | schema validation |
| @hono/zod-validator | ^0.5.0 | validation (worker) |
| hono | 4.7.7 | Cloudflare Worker framework (worker is a 5-line stub) |
| class-variance-authority, clsx, tailwind-merge | — | styling utilities |

### Dev / build tooling

| Technology | Version | Role |
|-----------|---------|------|
| Vite | ^7.1.3 | bundler / dev server |
| TypeScript | 5.8.3 | language (strict mode, zero `any`) |
| Tailwind CSS | ^3.4.17 (+ `tailwindcss-animate`) | styling |
| PostCSS + autoprefixer | ^8.5.3 / ^10.4.21 | CSS pipeline |
| ESLint | 9.25.1 (+ typescript-eslint, react-hooks, react-refresh) | linting |
| knip | ^5.51.0 | dead-code detection |
| Wrangler | ^4.33.0 | Cloudflare Workers CLI |
| @cloudflare/vite-plugin | ^1.12.0 | CF integration |
| cross-env | ^10.1.0 | cross-platform env vars |

### Key implementation facts

- `worker/index.ts` is a 5-line empty Hono stub; `wrangler.json` declares unused
  D1 + R2 bindings.
- Auth (`contexts/AuthContext.tsx`, `utils/jwt.ts`) is **fake** — accepts any
  credentials, mints an unsigned base64 "token", never sends it to a backend.
- API clients: `lib/ragApi.ts` (→ `:18000`), `lib/ddiqApi.ts` (→ `:18001`),
  backend URLs from `VITE_BACKEND_URL` / `VITE_DDIQ_URL` (currently a private
  LAN IP, `192.168.178.82`).

---

## 8. AI / ML models

| Model | Role | Served by | Where configured |
|-------|------|-----------|-------------------|
| **Qwen3.6-27B** (`qwen3.6-27b`, bfloat16, thinking mode, prefix caching) | Analyzer / generation LLM for serve_rag V2 + DDiQ | vLLM, GPU 0, port 8005 | `LAI/docker-compose.yml`, `Docker/llm-analyzer/` |
| **Qwen3-Embedding-8B** (4096-dim, `dtype auto`, max-len 32768) | Query + document embeddings | vLLM, GPU 1, port 8003 | `LAI/docker-compose.yml`, `Docker/embedding/` |
| **Qwen3-Reranker-8B** | Cross-encoder reranking | in-process (Transformers + PyTorch) inside `serve_rag.py` on GPU | `serve_rag.py:852` |
| **Qwen2.5-72B-Instruct-AWQ** (tensor-parallel) | Pipeline classification / enrichment / synthetic-data teacher | vLLM | README, pipeline configs |
| **Qwen2.5-7B-Instruct** | Older/alt generation LLM | vLLM | `Docker/llm/`, `Docker/services/.env` |
| `qwen25-7b-legal-merged` | LoRA fine-tune of Qwen2.5-7B (merged) — **shelved** (15.8% fabricated citations) | — | `LAI/training/`, README |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | Reranker in `Docker/reranker/` + `Docker/services/` variants | vLLM / TEI | `Docker/reranker/`, `Docker/services/.env` |
| `BAAI/bge-m3` (1024-dim) | Embedding model in legacy `Docker/embedding_server/` + `Docker/services/` | TEI / vLLM | `Docker/embedding_server/.env`, `Docker/services/.env` |
| `meta-llama/Meta-Llama-3-8B-Instruct` | LLM in legacy `inference_engine` | vLLM | `Docker/inference_engine/.env` |

**Comparison-only models** (registered in `multi_model_compare.py`, not in the
runtime path): `Qwen3.5-27B`, `gemma-4-E4B-it`, `leo-hessianai-7b`,
`Saul-7B-Instruct-v1`.

**Inference engine:** **vLLM 0.19.0** (declared in `pyproject.toml`; containers
run `vllm/vllm-openai:latest`). Reasoning parser `qwen3`, prefix caching enabled.

**Hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB VRAM each.

---

## 9. Data stores

| Technology | Version / image | Role | Driver / client |
|-----------|-----------------|------|-----------------|
| **PostgreSQL + pgvector** | `pgvector/pgvector:pg16` | Corpus chunks + embeddings (`parent_chunks`/`child_chunks`, `halfvec(4096)`), pipeline state, DDiQ tables | `asyncpg` 0.31.0 (core pkg), `psycopg2-binary` (DDiQ + pipeline), `pgvector` 0.3+ |
| **Redis** | `redis:7-alpine` | Cache & Celery broker | `redis` 6.4.0 (Python), `celery[redis]` 5.6.2 |
| **MinIO** | `minio/minio:latest` | Object storage (raw corpus, artifacts) | `minio` 7.2+, `miniopy-async` 1.21+ |
| **SQLite** | stdlib `sqlite3` | serve_rag `sessions`+`messages`; Docker-free pipeline state (`pipeline_local.db`); portable DB snapshots | stdlib |
| **Neo4j** | `neo4j:5.15-community` | **Running live** (ports 7474/7687) — origin not in any tracked compose file (see §14) | — |

### DDiQ PostgreSQL schema (`ddiq_report.py` SCHEMA_SQL — 9 tables)

`ddiq_documents`, `ddiq_doc_chunks`, `ddiq_reports`, `ddiq_geocode_cache`,
`ddiq_parcel_cache`, `ddiq_project_areas`, `ddiq_contracts`,
`ddiq_contract_parcels`, `ddiq_classified_parcels` (+ 4 indexes).

### serve_rag SQLite schema (`persistence.py` — 2 tables)

`sessions`, `messages` (+ 2 indexes). `sessions.user_id` column exists but is
unused.

---

## 10. Infrastructure & orchestration

| Technology | Version / detail | Role |
|-----------|------------------|------|
| Docker + Docker Compose | — | Container orchestration |
| vLLM (`vllm/vllm-openai:latest`) | image `latest`; pkg pinned 0.19.0 | LLM / embedding model servers |
| NVIDIA Container Toolkit | — | GPU passthrough (`deploy.resources.devices`) |
| PostgreSQL 16 | `pgvector/pgvector:pg16` | DB (also runs as a **host process** on port 5435 in current deployment) |
| Redis 7 | `redis:7-alpine` | cache/broker |
| MinIO | `minio/minio:latest` (+ `minio/mc:latest`) | object storage |
| Prometheus | `prom/prometheus:latest` | metrics (configured, not deployed — see §14) |
| Grafana | `grafana/grafana:latest` | dashboards (configured, not deployed) |
| MLflow | `ghcr.io/mlflow/mlflow:latest` | experiment tracking (training); Postgres backend + MinIO artifacts |
| `text-embeddings-inference` (TEI) | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.8` | reranker container actually running live (`lai-test-reranker`) |
| Bash ops scripts | `LAI/scripts/ops/{start,stop,status}-host.sh`, `LAI/ops/resume_step6.sh`, `scripts/resume_step5.sh` | host-process lifecycle, pipeline resume |

**Python monitoring lib:** `prometheus-client` 0.21+ (declared in core package).

**Networking:** all containers join an external Docker network `lai_network`.
Compose files default ports to `127.0.0.1`; `.env` overrides bind several to
`0.0.0.0`.

### Service / port map (intended)

| Service | Container / process | Intended port | GPU |
|---------|--------------------|--------------|-----|
| Analyzer LLM (Qwen3.6-27B) | `lai_analyzer_llm` (vLLM) | 8005→8000 | 0 |
| Embedding (Qwen3-Embedding-8B) | `lai_embedding` (vLLM) | 8003→8000 | 1 |
| PostgreSQL+pgvector | `lai_postgres_main` | 5434→5432 | — |
| Redis | `lai_redis` | 6379 | — |
| RAG/chat backend | `serve_rag.py` (host) | 18000 | 1 (in-proc reranker) |
| DDiQ backend | `lai-backend` (Docker) | 18001→8000 | — |
| Frontend | Vite (host) | 5173 | — |

---

## 11. External / third-party services

| Service | Used by | Purpose |
|---------|---------|---------|
| **ALKIS INSPIRE WFS** (12 German federal-state endpoints: Niedersachsen LGLN, NRW Geobasis, SH GDI, Brandenburg LGB, MV, Sachsen-Anhalt, Hessen HVBG, Thüringen, Sachsen GeoSN, RLP, Bayern LDBV, BW LGL) | DDiQ `cadastral_pipeline.py` / `ddiq_report.py` | Real cadastral parcel polygons (`cp:CadastralParcel`, WFS 2.0.0) |
| **Nominatim / OpenStreetMap** (`nominatim.openstreetmap.org`) | DDiQ | Address geocoding |
| **OSM map tiles** | DDiQ `/config/map-tiles`, frontend Leaflet | Base maps |
| **Hugging Face Hub** | vLLM containers, Transformers, training | Model downloads (offline mode `HF_HUB_OFFLINE=1` in runtime compose) |
| **Vercel** | frontend | Primary hosting (`lai-beta.vercel.app`, `lai-pied.vercel.app`, `lai-ashen.vercel.app`) |
| **Cloudflare Workers** (D1, R2) | frontend `worker/` | Declared but unused (empty stub) |

---

## 12. Authentication & security technologies

| Technology | Declared / present | Status |
|-----------|--------------------|--------|
| `python-jose[cryptography]` 3.3+ | core package (`lai.auth.jwt`) | JWT decode/encode logic exists, **wired only to the dead `api/main.py`** |
| `passlib[bcrypt]` 1.7.4+ | core package (`lai.auth`) | Password hashing logic exists, not used by live services |
| Frontend `utils/jwt.ts` | LAI-UI | **Fake** — unsigned base64, no HMAC |

**Net result:** no functional authentication anywhere in the running system.

---

## 13. Python dev tooling (core package)

| Technology | Version | Role |
|-----------|---------|------|
| ruff | >=0.8.0 | linter/formatter (rules: E, F, I, N, W, UP; line-length 120) |
| mypy | >=1.13.0 | type checking (`strict=false`) |
| pytest | >=8.3.0 | test framework (`asyncio_mode=auto`; markers: unit/integration/e2e/slow) |
| pytest-asyncio | >=0.24.0 | async test support |
| pytest-cov | >=6.0.0 | coverage |

**Training extras:** `torch>=2.5`, `transformers>=4.46`, `peft>=0.13`,
`trl>=0.12`, `datasets>=3.1`, `accelerate>=1.1`, `mlflow>=2.19`, `boto3>=1.35`,
`bitsandbytes>=0.49.2` (LoRA fine-tuning: 4-bit NF4, r=128/α=256, TRL SFTTrainer).

> **Test status:** the `tests/` tree (`unit/`, `integration/`, `e2e/`,
> `fixtures/`) contains **zero test files**. The DDiQ microservice and frontend
> have no tests either.

---

## 14. Gaps & discrepancies — documented vs. actual

This section exists to satisfy the "no gap" requirement. The following are
points where configuration/documentation and the **live running system** (as of
2026-05-14) diverge:

1. **Live runtime ≠ compose topology.** *(Initial probe state — since
   resolved; see `RE_VERIFICATION.md` §B1. The intended runtime stack is now
   actually running.)* At first probe, `docker ps` showed `lai-backend` (port
   18001, **unhealthy** — crashed at startup: cannot resolve `lai_postgres_main`),
   `lai-teacher-llm-gpu0` (vLLM, 8005), `lai-test-reranker` (TEI cpu image, 8004),
   `lai-user-doc-processor` (8300, **unhealthy**), `lai_neo4j` (7474/7687).
   **Not running at first probe:** `lai_postgres_main`, `lai_embedding`,
   `lai_redis`, `lai_analyzer_llm` as defined in `LAI/docker-compose.yml`, and
   `serve_rag.py` (port 18000 was not listening). PostgreSQL ran instead as a
   **host process on port 5435** (empty `lai_db`). **Current state (later in
   session):** the full runtime stack is up — `lai_postgres_main`,
   `lai_embedding`, `lai_analyzer_llm`, `lai_redis` all healthy on the
   `lai_network`; `lai-backend` and `serve_rag` both return `/health` OK; only
   `lai-user-doc-processor` remains unhealthy. The Docker/host topology drift
   that allowed this outage is still a latent risk — pick one deployment model.

2. **Postgres port mismatch.** Compose declares 5434; the host-process Postgres
   listens on 5435; the DDiQ container is hardcoded to DNS name
   `lai_postgres_main`. *Current state:* the `lai_postgres_main` container is
   now running on the `lai_network` and resolves correctly (verified
   `docker exec lai-backend getent hosts lai_postgres_main` → 172.18.0.5);
   the host-process Postgres on 5435 has an empty `lai_db` and is independent.
   Two Postgres instances coexist — clean up.

3. **Neo4j is running but undocumented.** `lai_neo4j` (`neo4j:5.15-community`)
   is live on 7474/7687 but appears in no tracked compose file and no code
   imports a Neo4j driver. Origin/purpose unclear.

4. **Reranker has 3 conflicting definitions:** (a) in-process `Qwen3-Reranker-8B`
   in `serve_rag.py`; (b) `cross-encoder/ms-marco-MiniLM-L-12-v2` via vLLM in
   `Docker/reranker/`; (c) the live container is a HuggingFace **TEI cpu** image.
   The README also mentions Qwen3-Reranker-8B replacing MiniLM in 2026-04.

5. **Embedding dimension drift.** Current pipeline + DB schema use **4096-dim**
   (`Qwen3-Embedding-8B`, `halfvec(4096)`). Legacy configs
   (`Docker/embedding_server/.env`, `Docker/services/.env`) and the
   `data_processing/` README use **1024-dim** `BAAI/bge-m3`. The README code
   sample also shows a `struct.unpack('1024f', …)` snippet — stale.

6. **Two RAG backends in one package.** `LAI/src/lai/api/serve_rag.py` is live;
   `LAI/src/lai/api/main.py` + the `search/`, `generation/`, `auth/`, `infra/`
   packages (**~3,200 LOC** — corrected from an earlier "~6,000" figure, see
   `RE_VERIFICATION.md` B2) are imported by nothing. The README "Quick Start"
   instructs running `lai.api.main` — which is dead. The DDiQ service uses none
   of these; it reimplements PDF/embedding/rerank helpers itself.

7. **Monitoring stack configured but not deployed.** `Docker/monitoring/`
   defines Prometheus + Grafana with a valid `prometheus.yml`, but neither
   container is running, and scrape targets don't match live container names.
   `prometheus-client` is a declared dependency but no `/metrics` endpoint is
   exposed by the live services.

8. **Celery is declared but not running.** `celery[redis]` 5.6.2 is a core
   dependency; no Celery worker process or beat scheduler is running. DDiQ async
   jobs use a plain `ThreadPoolExecutor` instead.

9. **`vllm` 0.19.0 is a declared Python dependency** of the core package, but
   models are actually served by the **separate `vllm/vllm-openai:latest`
   Docker image** — the Python `vllm` import is not used by `serve_rag.py`.

10. **Image versions unpinned.** `vllm:latest`, `prometheus:latest`,
    `grafana:latest`, `minio:latest`, `mlflow:latest`, `minio/mc:latest` —
    not reproducible. The DDiQ `requirements.txt` uses unpinned `>=` ranges with
    no lockfile (only the core `LAI/` package has `uv.lock`).

11. **Real secret on disk.** `Docker/inference_engine/.env:11` contains a live
    `HUGGINGFACE_HUB_TOKEN` (`hf_SdUN…`). Gitignored and never in git history,
    but present in plaintext on a shared host. **Should be rotated.**

12. **Frontend backend URL is a private LAN IP.** `LAI-UI/.env` points
    `VITE_BACKEND_URL` / `VITE_DDIQ_URL` at `http://192.168.178.82:18000/18001`
    — incompatible with the Vercel/Cloudflare hosting story.

13. **`data_processing/` (top-level, 43 GB dir) is dead legacy code** —
    not git-tracked, imported by nothing, superseded by `LAI/src/lai/pipeline/`.
    Targets an older `law_chunks` / 1024-dim schema.

14. **`.env.example` drift.** `LAI-UI/.env.example` advertises `VITE_JWT_SECRET`
    and `VITE_API_URL` — neither variable is referenced anywhere in `src/`.

---

## 15. Quick reference — full dependency tables

### Core Python package (`LAI/`, resolved from `uv.lock`)

Web: `fastapi` 0.135.1, `uvicorn` 0.41.0, `sse-starlette`, `python-multipart`.
DB: `asyncpg` 0.31.0, `pgvector`, `psycopg2-binary`.
Cache/queue: `redis` 6.4.0, `celery` 5.6.2.
Config: `pydantic` 2.12.5, `pydantic-settings`.
HTTP: `httpx` 0.28.1.
Docs: `docling` 2.77.0, `python-docx`.
Storage: `miniopy-async`, `minio`.
Auth: `python-jose[cryptography]`, `passlib[bcrypt]`.
Monitoring: `prometheus-client`.
Retry: `tenacity` 9.0+.
ML: `numpy` 2.2.6, `vllm` 0.19.0, (`torch` 2.10.0 + `transformers` 4.57.6 via
training extra / used by serve_rag).

### DDiQ microservice (`LAI/micro-services/requirements.txt`, unpinned)

`fastapi`, `uvicorn[standard]`, `python-dotenv`, `pydantic`, `python-multipart`,
`psycopg2-binary`, `requests`, `PyMuPDF`, `pytesseract`, `Pillow`, `numpy`,
`shapely`.

### Frontend (`LAI-UI/package.json`)

Runtime: `react` 19.0.0, `react-dom` 19.0.0, `react-router` ^7.5.3,
`radix-ui` ^1.4.3, `lucide-react` ^0.510.0, `react-markdown` ^10.1.0,
`leaflet` ^1.9.4, `react-leaflet` ^5.0.0, `zod` ^3.24.3, `hono` 4.7.7,
`@hono/zod-validator` ^0.5.0, `class-variance-authority`, `clsx`,
`tailwind-merge`.
Dev: `vite` ^7.1.3, `typescript` 5.8.3, `tailwindcss` ^3.4.17,
`tailwindcss-animate`, `postcss`, `autoprefixer`, `eslint` 9.25.1,
`typescript-eslint` 8.31.0, `eslint-plugin-react-hooks`,
`eslint-plugin-react-refresh`, `knip` ^5.51.0, `wrangler` ^4.33.0,
`@cloudflare/vite-plugin` ^1.12.0, `cross-env` ^10.1.0, `@vitejs/plugin-react`,
`globals`, `@types/*`.

---

*End of inventory. Every entry above is backed by a file in the repository or a
live system probe; §14 enumerates every known divergence between the configured
and the running state.*
