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
| **hf_cases** | 13 GB | **251,038** | ✅ done (2026-04-23) | pending | pending | Custom processor: `scripts/temp/process_court_decisions.py` |
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

**Why shelved**: a quality audit (`scripts/audit_training_data.py`)
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

**Key script**: [`scripts/rag_eval.py`](../scripts/rag_eval.py) — runs
any of the 6 modes above on N val queries; per-query and aggregated
metrics saved to `scripts/rag_eval_results/`.

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
| **15.8% of training citations are fabricated** | FT model hallucinates §§/clauses | Captured in `scripts/audit_training_data.py`; regenerate with verification loop before retraining |
| GPU contention with shared users | Training/eval may OOM | `./scripts/resume_step5.sh --status` to diagnose; resume cleanly via SQLite checkpoint |
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
| Export training data to JSONL | `python -m training.fine_tuning.scripts.export_training_data` |
| Run LoRA fine-tune | `python -m training.fine_tuning.scripts.run_lora --epochs 2` (see script for all flags) |
| Training outputs | `training/fine_tuning/output/qwen25-7b-legal-lora/` (adapter + best checkpoint) |
| **Process court decisions** | `python scripts/temp/process_court_decisions.py --source all` (handles hf_cases + openlegaldata, writes Step-1-compatible segments) |
| **Training-data quality audit** | `python scripts/audit_training_data.py` (citations verified against parent chunks; found 15.8% fabrication rate) |
| **Retrieval eval harness** | `python scripts/rag_eval.py --mode hybrid_rerank --n 500` (6 modes; writes results to `scripts/rag_eval_results/`) |
| **Retrieval failure analysis** | `python scripts/rag_audit_analysis.py <results.json>` (breaks down recall by task, specificity, doc_type) |
| **End-to-end RAG test** | `python scripts/rag_generate_test.py --n 5` (retrieve + generate with base + FT, side-by-side) |
| **Raw corpus layout** | `LAI/data/lai-raw/` (671 GB source docs) + `LAI/data/lai-segments/` (1.7 GB Step-1 output) — moved from `minio-backup/` 2026-04-23 |
| RAG pipeline | [src/lai/api/pipeline.py](../src/lai/api/pipeline.py) |
| Hybrid search SQL | [src/lai/search/hybrid_search.py](../src/lai/search/hybrid_search.py) |
| Prompt templates | [src/lai/generation/prompt_builder.py](../src/lai/generation/prompt_builder.py) |
| Docker services | [/data/projects/lai/Docker/](../../../Docker/) |
| Infrastructure docs | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
| Architecture overview | [architecture/overview.md](architecture/overview.md) |
| Improvement roadmap | [analysis/LAIV5_IMPROVEMENTS.md](analysis/LAIV5_IMPROVEMENTS.md) |
| Project history (V1-V4) | [analysis/LAI_PROJECT_ANALYSIS.md](analysis/LAI_PROJECT_ANALYSIS.md) |
