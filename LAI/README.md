# LAI - German Legal AI Platform

Legal AI platform for wind energy due diligence. Answers legal questions using RAG (Retrieval-Augmented Generation) over a 672GB German legal corpus with locally-hosted models. Includes a full data processing pipeline for both RAG retrieval and model fine-tuning.

> **Where current state lives (2026-06-03):** this README is the
> long-form "what is LAI" answer. For *current* state — what shipped
> this week, what's pending, what unblocks what — see:
> * Rolling tracker: [`harsh/PROGRESS_V2.md`](../harsh/PROGRESS_V2.md)
> * Exec status: [`rj/boss-status-2026-06-03.md`](../rj/boss-status-2026-06-03.md)
> * **Pilot conversation prep:** [`rj/pilot-prep/`](../rj/pilot-prep/)
> * Engineering writeups: [`rj/blueprint/`](../rj/blueprint/)
> * EU AI Act coverage: [`harsh/EU_AI_ACT.md`](../harsh/EU_AI_ACT.md)
> * Onboarding overview: [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)

## Architecture

- **RAG Pipeline:** Embed query → hybrid search (pgvector HNSW dense + SQLite FTS5 BM25 v5 + RRF) → cross-encoder rerank (Qwen3-Reranker-8B, in-process) → LLM generation (remote Qwen3.6-27B at `:8005`) → citation validation
- **Data Pipeline:** Raw documents → segments → parent-child chunks → domain classification → contextual enrichment → fine-tuning data → embeddings
- **Models:**
  - **Chat LLM (production, remote):** `Qwen/Qwen3.6-27B` via vLLM at `:8005` (`--reasoning-parser qwen3`, Apache-2.0). The chat path uses this model ONLY; any change requires explicit project-owner approval.
  - **Pipeline LLM:** Qwen2.5-72B-Instruct-AWQ (Step 5 synthetic generation, tensor-parallel 2 GPUs)
  - **Embedding:** Qwen3-Embedding-8B (4096 dims, truncated to 4000 for pgvector `halfvec(4000)` HNSW index — production has cosine ANN, ~3 ms warm per query)
  - **Reranker:** Qwen3-Reranker-8B (multilingual, in-process at serve_rag startup, ~18.5 GB on cuda:1)
  - **Phase 3 (planned):** LoRA fine-tune of Qwen3.6-27B on BImSchG-scoped data. Sequenced AFTER the first pilot — see `harsh/MODEL_COMPARISON.md` + `rj/pilot-prep/`.
- **Infrastructure:** PostgreSQL + pgvector, Redis, MinIO, vLLM — all self-hosted
- **Hardware:** 2x RTX Pro 6000 GPUs (96GB VRAM each)
- **Multi-tenancy:** Per-org PostgreSQL `corpus_*` + per-session matter view; audit log per [EU AI Act Art. 12](../harsh/EU_AI_ACT.md)

## Quick Start

```bash
# 1. Start Docker services
docker network create lai_network
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d  # port 5434
cd /data/projects/lai/Docker/database/redis && docker compose up -d
cd /data/projects/lai/Docker/embedding && docker compose up -d         # :8003
cd /data/projects/lai/Docker/llm && docker compose up -d               # :8005 (Qwen3.6-27B)

# 2. Run the chat backend (serve_rag — the live API)
cd /data/projects/lai/LAI
uv sync
CUDA_VISIBLE_DEVICES=1 LAI_BIND_HOST=0.0.0.0 .venv/bin/python -m lai.api.serve_rag --port 18000
# API at http://localhost:18000, /health for the readiness probe

# Or use the managed restart wrapper (recommended for production-style start):
./scripts/ops/restart_serve_rag.sh
```

## Runtime services (what's actually shipping)

The MVP runtime is a **two-service split** that runs alongside the data pipeline above:

- **`serve_rag`** (host process, `:18000`) — the conversational legal assistant. Document upload + clause analyzer + RAG-grounded chat with conversational memory + vLLM prefix caching. Started via `bash scripts/ops/start.sh`.
- **`lai-backend`** (Docker container, `:18001`) — the DDiQ multi-document due-diligence microservice at [`micro-services/`](micro-services/). Async report generation with request-fingerprint dedup, incremental persistence, statutory-anchor section prompts, timeline / cross-doc / Grundbuch / Rückbau extraction passes, 10H rule. Brought up via `docker compose -f micro-services/docker-compose.yml up -d`.

The frontend lives in its own repo, [LAI-UI](https://github.com/Ravijangid820/LAI-UI), cloned by convention to `/data/projects/lai/LAI-UI/` (override via `LAI_UI_DIR`).

See [`docs/MVP_DELIVERY.md`](docs/MVP_DELIVERY.md) for the full feature list, [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the API endpoint catalog, [`TODO.md`](TODO.md) for what's next, and [`scripts/ops/README.md`](scripts/ops/README.md) for copy-paste operational commands (resume long pipeline runs, smoke-test the chat / DDiQ, check pipeline progress).

For a Docker-free runtime (when the Docker daemon is unavailable, or you want everything as host processes), use `bash scripts/ops/start-host.sh` — it runs the vLLM servers, `serve_rag`, the DDiQ backend, Vite, and a user-local PostgreSQL cluster, all without root. See [`scripts/ops/README.md`](scripts/ops/README.md).

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

## Docker-free Operation

The pipeline can run without PostgreSQL/MinIO/Redis — only the LLM container is required. State lives in SQLite at `processed/pipeline_local.db`.

```bash
# One-shot resume (auto-starts vLLM container + Step 5)
./scripts/ops/resume_step5.sh
./scripts/ops/resume_step5.sh --status   # check progress
./scripts/ops/resume_step5.sh --stop     # stop Step 5 (keeps LLM up)

# Or run any step in --local mode (reads MinIO bind-mount, writes SQLite)
uv run python -m lai.pipeline.cli step2 --local
uv run python -m lai.pipeline.cli step5 --local
```

Portable database snapshots (built with `python scripts/db/export_to_sqlite.py all`):
- `processed/db_export/pipeline.db` (1 GB) — chunks, training samples, classifications
- `processed/db_export/app.db` (284 GB) — chunks with embeddings as binary BLOBs

Read with no PostgreSQL required:
```python
import sqlite3, struct
conn = sqlite3.connect('processed/db_export/app.db')
blob = conn.execute("SELECT embedding FROM chunks LIMIT 1").fetchone()[0]
embedding = list(struct.unpack('1024f', blob))  # 1024-dim vector
```

## LoRA Fine-tuning

Qwen2.5-7B-Instruct is fine-tuned via TRL SFTTrainer + PEFT LoRA on the
synthetic samples generated by Step 5 of the pipeline.

```bash
# 1. Export training_samples from SQLite to ChatML JSONL (95/5 stratified split)
python -m training.fine_tuning.scripts.export_training_data
# -> training/fine_tuning/data/{train,val}.jsonl

# 2. Run LoRA fine-tune (2 epochs, effective batch 16, ~14h on a single RTX Pro 6000)
CUDA_VISIBLE_DEVICES=1 \
HF_HOME=./.runtime-cache/hf HF_HUB_CACHE=./.runtime-cache/hf/hub \
PYTORCH_ALLOC_CONF=expandable_segments:True \
python -m training.fine_tuning.scripts.run_lora \
    --epochs 2 --per-device-batch 2 --grad-accum 8 --eval-batch 8 \
    --no-grad-ckpt --max-seq-len 4096 \
    --eval-steps 1000 --save-steps 1000 --log-steps 25 \
    --output-dir training/fine_tuning/output/qwen25-7b-legal-lora
```

The trainer loads Qwen2.5-7B-Instruct in 4-bit (bnb NF4) and trains LoRA
adapters (r=128, α=256) on all 7 Qwen projection matrices. `load_best_model_at_end`
picks the checkpoint with the lowest eval_loss at the end of the run.

Flags to know:
- `--no-grad-ckpt` — disables gradient checkpointing; ~30% faster, needs more VRAM
- `--eval-batch 8` — eval uses no gradients, larger batches are safe and 4× faster
- `--resume` — resume from the latest checkpoint in `--output-dir`
- `--limit N` — process only the first N train rows (smoke test)

> **Status (2026-06-03):** the section above documents the *historical*
> Qwen2.5-7B LoRA work (v1, v2). It is shelved — both adapters confidently
> fabricate `§ 999`-style fictional statutes per the 2026-05-30 retention
> probe (`refusal_003` returns bit-identical fabrications in v1 and v2).
> See `harsh/MODEL_COMPARISON.md` for the full failure analysis + the
> corrected recipe.
>
> **Phase 3 (current plan):** LoRA fine-tune of **Qwen3.6-27B** (NOT the
> 7B). Architecture is hybrid Gated-DeltaNet + full-attention (`model_type
> = qwen3_5`); the retention-probe callback in `training/fine_tuning/eval/`
> hard-stops on token-loop or fabrication regressions. Sequencing waits
> on the first pilot firm — every supporting artifact is already in place
> (recipe, probes, baseline workflow, eval API + UI). The moment pilot
> lands, training is unblocked. See `rj/pilot-prep/` for the pilot side.

## Corpus Processing (Phase 2)

Beyond the 6-step generic pipeline, there are **format-specific processors**
for corpora whose structure matters (court decisions have semantic sections
— Tenor / Tatbestand / Gründe — that a generic char-chunker loses). These
one-off processors are archived in `scripts/archive/temp/`:

```bash
# Unified processor for German court decisions:
#   - hf_cases     (251K cases, one per file)
#   - openlegaldata (41K cases, 10 per page file)
# Handles all 7 schema variants, 28 raw-type values, recovers ~57% of
# missing court_level via court.name parsing. Deduplicates by ECLI/slug.
python scripts/archive/temp/process_court_decisions.py --source all
# → data/lai-segments/legal_data/{hf_cases,openlegaldata}/*.segments.jsonl
# → Step-1-compatible; feeds into the existing Step 2 / Step 6.
```

## RAG Evaluation

The retrieval eval harness (`lai.search.eval`) measures retrieval quality
end-to-end on stratified val queries whose gold `parent_id` is known. Filter
the search pool with `--exclude-source-corpus multilegalpile` or
`--only-doc-types vdr,gesetz,...` to test against specific corpus subsets.

```bash
python -m lai.search.eval --mode hybrid_rerank --n 100
```

Modes (compared on the 8.3M-embedding corpus after dedup, n=100):

| Mode | R@1 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|
| dense + Qwen3 query prefix | 31% | 55% | 63% | 0.413 |
| hybrid (dense + bm25 + RRF) + prefix | 35% | 56% | 66% | 0.434 |
| hybrid + prefix + Qwen3-Reranker-8B | 37% | 66% | 72% | 0.492 |

Reproducible numbers — these match what the in-RAM eval harness
returned on the recovered DB at that time.

> **2026-06-03 update — scaled, production-fidelity harness shipped.**
> The in-RAM harness above OOMs on the current 35.7M-child corpus
> (572 GB of fp32 embeddings). The new
> [`scripts/eval/retrieval_recall.py`](scripts/eval/retrieval_recall.py)
> queries the SAME indexes serve_rag uses in production (pgvector HNSW
> + SQLite FTS5 + RRF + reranker), so reported Recall@K matches what
> users see. Live-measured baseline on **n=200 real BImSchG val
> queries**:
>
> | Mode | R@10 | R@30 | R@100 | retrieve_ms |
> |---|---:|---:|---:|---:|
> | dense (Qwen3-Embedding-8B HNSW) | 0.315 | 0.380 | 0.435 | 119 |
> | bm25 (FTS5 v5, DE-stopword filter) | 0.300 | 0.355 | 0.430 | 2,461 |
> | **hybrid (dense + bm25 v5 + RRF)** | **0.435** | **0.490** | **0.560** | 3,015 |
>
> Six retrieval-tuning experiments across four layers (HNSW ef_search,
> candidate pool size, 7 BM25 expression variants, 3 reranker-query
> augmentations) were run during 2026-06-02 / 06-03. One positive
> shipped (**BM25 v5 stopword filter, 14% faster, same recall** —
> live since 2026-06-02 22:41); five documented negatives. Production
> Recall@30 = 0.49 is the honest model ceiling at this index. Full
> table + decision rules at
> [`rj/blueprint/2026-06-02-retrieval-tuning-results.md`](../rj/blueprint/2026-06-02-retrieval-tuning-results.md).

**Cleanup that mattered:** `scripts/archive/dedup_phase1_rechunks.py` removes
the 134K parents (216K children, 216K embeddings) that Step 2 produced
when it inadvertently re-chunked Phase 1 sources during the multilegalpile
run. Pre-dedup eval was R@1=0.24 / R@5=0.45; post-dedup is R@1=0.32 /
R@5=0.55 — a 10pt R@5 lift just from removing duplicates.

Followup: `scripts/eval/rag_audit_analysis.py` slices the failures by task,
query specificity, and doc_type to show where the remaining R@5 misses
are concentrated.

## End-to-end RAG Service + Web UI

`lai.api.serve_rag` (`src/lai/api/serve_rag.py`) exposes the full
retrieval+generation pipeline behind a FastAPI endpoint that matches the
contract consumed by the **LAI-UI** frontend.

The frontend lives in its own repo as of v1.0.0:

* **Frontend repo:** https://github.com/Ravijangid820/LAI-UI
* **Local clone convention:** `/data/projects/lai/LAI-UI/` (sibling to
  this `LAI/` directory; gitignored from this repo)

```bash
# Backend (loads the Qwen3-Reranker-8B onto GPU + wires pgvector retrieval; ~30-60s cold start)
# LLM is the remote Qwen3.6-27B analyzer (:8005); restart cleanly via scripts/ops/restart_serve_rag.sh
CUDA_VISIBLE_DEVICES=1 .venv/bin/python -m lai.api.serve_rag --port 18000

# Frontend (separate repo)
git clone git@github.com:Ravijangid820/LAI-UI.git ../LAI-UI
cd ../LAI-UI && npm install && npm run dev   # Vite default port 5173
```

Endpoints:
- `GET /health` — readiness probe
- `POST /query {question, session_id?, top_k?}` — returns `{answer, chunks, timings, tokens, session_id}`
- `POST /upload` — stub (returns OK without ingestion)

Per-query latency (production smoke 2026-06-03, n_sessions=152):
~14 s wall (retrieve 2.3 s + rerank 2.5 s + generate 8.8 s + auth/serialise
overhead). The ~30 s figure in the original README was on the 8.3M-
embedding corpus before the Track-B pgvector migration; current
corpus is 35.7M children and faster per-query because we no longer
load embeddings into RAM at startup.

For end-to-end manual comparison of base vs fine-tuned model with RAG
context:

```bash
python scripts/eval/rag_generate_test.py --n 5 --top-k 3 \
  --base Qwen/Qwen2.5-7B-Instruct \
  --ft   /data/projects/lai/models/qwen25-7b-legal-merged
```

## Multi-model RAG comparison

`scripts/archive/multi_model_compare.py` runs the same retrieval contexts
through several LLMs side-by-side and writes a markdown report to
`scripts/eval/rag_eval_results/multi_model_compare.md`.

```bash
# Small models that fit alongside the embedding container (lai_embedding ~44 GB on GPU)
CUDA_VISIBLE_DEVICES=1 .venv/bin/python scripts/archive/multi_model_compare.py \
    --n 5 --top-k 3 \
    --models qwen25-ft qwen25-base gemma4 llama3

# 27B models (need lai_embedding STOPPED first — ~54 GB needed in bf16)
docker stop lai_embedding
CUDA_VISIBLE_DEVICES=1 .venv/bin/python scripts/archive/multi_model_compare.py \
    --n 5 --top-k 3 --models qwen35 qwen36
docker start lai_embedding   # restore service after eval
```

Registered model keys (full inventory in
`/data/home/rj/.claude/projects/-data-projects-lai/memory/model-inventory.md`):

| Key | Path | Size | Notes |
|---|---|---|---|
| `qwen36` *(production default)* | `Qwen/Qwen3.6-27B` (remote, vLLM `:8005`) | 54 GB | Chat path LLM since 2026-04. Apache-2.0. `--reasoning-parser qwen3`. |
| `qwen25-ft` | `/data/projects/lai/models/qwen25-7b-legal-merged` | 15 GB | Historical: Qwen2.5-7B LoRA fine-tune (v2). Shelved — confidently fabricates fictional § 999. See `harsh/MODEL_COMPARISON.md`. |
| `qwen25-base` | `Qwen/Qwen2.5-7B-Instruct` | 15 GB | Base for the historical 7B FT comparison |
| `qwen35` | `Qwen/Qwen3.5-27B` | 52 GB | Larger, newer Qwen |
| `qwen36` | `Qwen/Qwen3.6-27B` | 52 GB | Larger, newer Qwen |
| `gemma4` | `google/gemma-4-E4B-it` | 15 GB | 4B effective, fast |
| `llama3` | `meta-llama/Meta-Llama-3-8B-Instruct` | 16 GB | General-purpose comparison |
| `leo7b` | `/data/projects/lai/models/leo-hessianai-7b` | ~14 GB | German foundation, no legal FT |
| `saul7b` | `/data/projects/lai/models/Saul-7B-Instruct-v1` | ~14 GB | Equall.ai legal model (EN/FR) |

## Training-data Quality Audit

Before spending more GPU time on fine-tuning, run the citation-grounding
audit:

```bash
python scripts/archive/audit_training_data.py
```

It parses every `§`/`Art.`/`Klausel` reference in answers and checks
whether the same identifier exists in the parent chunk. Output: per-task
table of citation-verify rates + word-overlap distribution + a JSON
dump of worst offenders. **Run this before every retraining.**

See [docs/PROJECT_STATUS.md#fine-tuning-in-progress-as-of-2026-04-22](docs/PROJECT_STATUS.md)
for the current run's config and lessons learned.

## Documentation

- [Project Status](docs/PROJECT_STATUS.md) — Start here if you're new to the project
- [Workflow](docs/WORKFLOW.md) — End-to-end data flow: corpus build, upload, query, DDiQ reports
- [Architecture Overview](docs/architecture/overview.md)
- [Infrastructure Guide](docs/INFRASTRUCTURE.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Contributor contract + quality gate](CONTRIBUTING.md)
- [v1 Strategy + 10-day roadmap](docs/LAI_V1_STRATEGY.md)
- [Demo Status](docs/DEMO_STATUS.md) · [UI Guide](docs/UI_GUIDE.md)
- [Architecture Decision Records](docs/adr/)

## Project Structure

`src/lai/` is an installable package — `uv sync` (or `pip install -e .`)
makes `from lai... import ...` work everywhere, with no `sys.path` hacks.
Each subpackage is one **domain**; ownership is declared in
[`.github/CODEOWNERS`](../.github/CODEOWNERS). See
[`src/lai/README.md`](src/lai/README.md) for the package map.

```
src/lai/                  Installable domain-driven package (`lai`)
  common/                 Shared production-grade primitives — the foundation.
                          Held to strict mypy + ruff + ≥85% coverage + bandit.
    llm/                    LlmClient (async + sync), strip_think, salvage_json
    embedding/              EmbeddingClient + sync façade
    reranker/               RerankerClient (TEI /rerank)
    retrieval/              RetrievalClient — pgvector/HNSW corpus retrieval (Track B;
                            supersedes the in-RAM matrix in search/eval.py)
    pdf/                    PdfExtractor with OCR fallback
    chunk/                  German-legal-aware Chunker
    citation/               Extract + validate [C-n]/[M-n] handles, strip fabricated ones
    jurisdiction/           Bundesland detection + JurisdictionWarning
    connectors/             External registries — NominatimClient (geocode) + AlkisClient
                            (cadastral WFS → Flurstück polygons); secure XML parsing
    auth/                   JWT auth + tenant isolation
  pipeline/               Offline 6-step corpus build (`python -m lai.pipeline.cli`)
  search/                 eval.py — recall/RAG eval harness (the legacy in-RAM retriever)
  analyzer/               Qwen3.6-27B contract analyzer — playbooks, prompts, schema
  api/                    serve_rag.py (:18000 chat backend) + auth_router + admin_router
                          + share_router + upload_tus (resumable) + metrics + email
  core/                   Config, logging, exceptions, constants

  (Deleted on 2026-05-15: the old auth/, documents/, extraction/, generation/, infra/
   packages + api/main.py + api/pipeline.py — unwired FastAPI scaffolding. Their
   capabilities now live in lai.common; the promised retrieval package shipped as
   `lai.common.retrieval`.)

micro-services/           DDiQ due-diligence report service (:18001, Docker)
infra/monitoring/         Prometheus + Grafana stack — backend exposes /metrics
scripts/
  ops/                    Entry points — start/stop/status{,-host}.sh, restart_serve_rag.sh,
                          resume_step5/6.sh, migrate_corpus.py (Track B), load_demo_matter.py
  eval/                   Eval & benchmark harnesses + golden_retrieval_sanity.py
  db/migrations/          SQL migrations (001 auth+tenant, corpus→pgvector;
                          002 org tenancy, 003 super-admin, 004 invitations, 005 shares)
  archive/                Completed one-off migrations, audits, pilots
training/                 Model fine-tuning (separate lifecycle)
tests/                    Unit / integration / e2e (strict-gated under lai.common)
docs/                     Documentation (incl. adr/ + the v1 strategy/demo/technical docs)
demo-seed/                Curated demo matters (e.g. lamstedt/) — input to load_demo_matter.py
```

## Runtime features (v2.1.0+ — see [`docs/DEMO_STATUS.md`](docs/DEMO_STATUS.md) + [`docs/TECHNICAL_DOCUMENTATION.md`](docs/TECHNICAL_DOCUMENTATION.md))

The chat backend (`lai.api.serve_rag`) wires the `lai.common` primitives into a
production-grade Q&A flow:

- **Streaming** answers via `POST /query/stream` (SSE) + non-streaming `POST /query`
- **Mode router** — `needs_rag()` short-circuits UI/meta/navigation questions to chat instead of RAG; catches "was kann ich hier tun?", "can you access my documents?", "gehst du semantisch vor?" etc. Surfaced by the 2026-06-01 ks/as production audit + the 2026-06-03 wider session audit; closed at the regex layer with 51 unit tests + gold-RAG safety check against 50 BImSchG questions
- **Citation rigor** — `[C-n]` / `[M-n]` handles in retrieved chunks; `lai.common.citation.validate_citations` strips fabricated handles post-LLM and rewrites the sentence to end `(unbelegt)`. Validator audit at [`rj/blueprint/2026-06-03-citation-validation-audit.md`](../rj/blueprint/2026-06-03-citation-validation-audit.md).
- **Jurisdictional sanity** — `lai.common.jurisdiction.check_jurisdiction` returns a `JurisdictionWarning` when the matter's Bundesland disagrees with citations
- **Auth, org tenancy & sharing** — JWT (`auth_router`), org/super-admin endpoints (`admin_router`, migrations 002–004), and per-session view-only sharing (`share_router`, migration 005)
- **Audit log (EU AI Act Art. 12)** — append-only `audit_log` table (migration 006, no-UPDATE trigger), every login / query / upload / report / export event recorded. Admin read endpoint at `GET /admin/audit`; CSV/JSON export + 6-month retention CLI at `scripts/ops/audit_export.py`. Coverage map: [`harsh/EU_AI_ACT.md`](../harsh/EU_AI_ACT.md)
- **DOCX export** — `GET /ddiq/report/{id}/export.docx` for client-deliverable findings (German labels + firm-letterhead placeholder)
- **Resumable uploads** — tus 1.0 server (`upload_tus`) for VDR-scale documents
- **Feedback** — `POST /feedback` lawyer thumbs-up/down, persisted, optimistic UI
- **Observability** — `/metrics` Prometheus endpoint; 9-panel Grafana dashboard at [`infra/monitoring/`](infra/monitoring/); hourly smoke cron at `scripts/ops/smoke_test.py` catches outages within 1 h
- **Bilingual EN ⇄ DE** — `target_language` on `/query`; German language detector at [`serve_rag.py:_detect_question_language`](src/lai/api/serve_rag.py)

> **Retrieval backend:** the chat path now retrieves via `lai.common.retrieval` (pgvector + HNSW over `corpus_child_chunks`, loaded by the Track-B migration), not the legacy in-RAM numpy matrix — so cold-start no longer waits on a ~144 GB RAM load.

## Quality gate

[`Makefile`](Makefile) + [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) run the same checks locally and in CI: `ruff` (lint+format), `mypy --strict` on `lai.common`, `pytest` with ≥85 % branch coverage on `lai.common`, `bandit` security scan. See [`CONTRIBUTING.md`](CONTRIBUTING.md) — *"if `make check` doesn't pass locally, your change is not done."*
