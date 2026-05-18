# LAI Workflow — End-to-End

A narrative walkthrough of how data flows through the LAI platform: from raw
legal documents to a searchable corpus, and from a user's question (or uploaded
contract, or due-diligence data room) to a grounded answer or report.

This is the **flow** doc. For the component **inventory** (libraries, models,
versions) see the Tech Stack table in [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
— and `harsh/TECH_STACK.md`, a fuller exhaustive inventory currently being
written by another developer (not yet in `docs/`). For the **feature list**
see [`MVP_DELIVERY.md`](MVP_DELIVERY.md); for **per-domain detail** see the
`README.md` in each [`src/lai/`](../src/lai/) package.

---

## The shape of the system

LAI is **three deployable units** over a shared model + data layer:

| Unit | What it is | Port |
|---|---|---|
| `serve_rag` | Conversational chat backend — RAG Q&A, document upload, contract analysis | 18000 |
| `lai-backend` (DDiQ) | Multi-document due-diligence report service | 18001 |
| `LAI-UI` | React frontend (own repo) | 5173 |
| `lai.pipeline` | Batch CLI that builds the corpus the others search | — |

Shared layer: vLLM model servers (analyzer LLM on :8005, embedding on :8003),
an in-process reranker, PostgreSQL + pgvector, Redis, MinIO, SQLite.

There are **two distinct "document processing" flows** people often conflate:
1. **Corpus build** (offline, batch) — turning the 672 GB legal corpus into
   searchable embeddings. Done once, by `lai.pipeline`.
2. **Runtime upload** (online, per-request) — a user uploads their own PDF and
   it becomes searchable within their session / report. Done by `serve_rag`
   and the DDiQ backend.

---

## Part A — Building the corpus (`lai.pipeline`)

The offline pipeline turns raw legal documents into the embedded corpus that
RAG searches. Six idempotent steps, run via
`python -m lai.pipeline.cli step{1..6}`:

| Step | Module | What happens |
|---|---|---|
| 1 — convert | `convert.py` | Raw PDFs/DOCX/JSON (from MinIO) → normalized text **segments**. Docling for documents, custom parsers for dataset dumps, Tesseract OCR for scanned pages, parallelized with a `ProcessPoolExecutor`. |
| 2 — chunk | `chunk.py` | Segments → **parent chunks** (large, ~3 K chars) and **child chunks** (small, ~1.5 K chars, with overlap). Uses a German legal-aware sentence splitter so chunk boundaries don't fall mid-citation. |
| 3 — classify | `classify.py` | Each parent chunk gets a **legal-domain label** (12 wind-energy domains) from Qwen2.5-72B-AWQ via vLLM. |
| 4 — enrich | `enrich.py` | Each child chunk gets a **context prefix** (Anthropic's contextual-retrieval idea) so an isolated chunk still carries enough context to be retrievable. |
| 5 — generate | `generate.py` | Synthesizes ~200 K Q&A training samples from the corpus (ChatML, 7 task types) — the fine-tuning dataset. Not on the RAG path. |
| 6 — embed | `embed.py` | Every child chunk → a **4096-dim Qwen3-Embedding-8B vector**. Stored as `halfvec(4096)` in pgvector, or as BLOBs in SQLite. Supports `--embed-urls` for parallel multi-GPU embedding. |

**Storage modes:** PostgreSQL + pgvector (default) or SQLite
(`processed/pipeline_local.db`, the Docker-free mode). Either way the end state
is the same: `parent_chunks` + `child_chunks` + child embeddings.

Long-running steps resume via [`scripts/ops/resume_step5.sh`](../scripts/ops/resume_step5.sh)
and [`resume_step6.sh`](../scripts/ops/resume_step6.sh).

---

## Part B — The chat service (`serve_rag`, :18000)

`lai.api.serve_rag` is a FastAPI host process. **At startup** it loads the
~8 M child embeddings into RAM (~127 GB) as a NumPy float32 matrix, and loads
the Qwen3-Reranker-8B cross-encoder onto the GPU. Then it serves three flows.

### B1 — Uploading a document (`POST /upload`)

```
PDF/DOCX  →  Docling (text + tables)  →  segment  →  embed  →  store in-memory,
             bound to the caller's session_id
```

Per-session uploaded documents live in process memory, so the next `/query` in
that session can retrieve over *both* the global corpus and the user's own doc.

### B2 — Asking a question (`POST /query` and `POST /query/stream`)

```
question
  → route               (RAG / plain chat / contract / rag+contract)
  → embed query         (lai.common.embedding.EmbeddingClient → Qwen3-Embedding-8B)
  → retrieve            dense exact-cosine + BM25 (lai.search.eval)
  → fuse                Reciprocal Rank Fusion (RRF)
  → rerank              lai.common.reranker.RerankerClient → Qwen3-Reranker-8B in-proc
  → generate            lai.common.llm.SyncLlmClient → Qwen3.6-27B (thinking mode);
                        strip_think + salvage_json applied server-side
  → citation validate   lai.common.citation.validate_citations strips fabricated
                        [C-n]/[M-n] handles, rewrites the sentence to "(unbelegt)"
  → jurisdiction check  lai.common.jurisdiction.check_jurisdiction returns a
                        JurisdictionWarning if Bundesland disagrees with citations
  → return              { answer, chunks, citation_validation, jurisdiction_warning,
                          timings, tokens, session_id }
```

`POST /query/stream` is the same flow with **SSE token streaming**; the trailing
SSE event carries the validation + warning payload.

The `lai.common` building blocks (`llm`, `embedding`, `reranker`, `citation`,
`jurisdiction`) are held to `mypy --strict` + ≥85 % branch coverage —
[`CONTRIBUTING.md`](../CONTRIBUTING.md). The `serve_rag` legacy paths consume
them; new modules go directly under `lai.common`.

**Conversational memory:** each session keeps a rolling 32-message window plus
LLM-extracted "pinned" stable facts (who the user is, the matter they're on).
Both are replayed into the prompt; vLLM prefix caching keeps the repeated
context cheap.

**Auth + tenant isolation:** every `/query*` and `/sessions/*` call is JWT-gated
via `lai.api.auth_router` (backed by `lai.common.auth`). Sessions and uploaded
documents are scoped per tenant.

**Lawyer feedback:** `POST /feedback` persists thumbs-up/down per message — the
UI's optimistic chip rehydrates from the persisted verdict on reload.

### B3 — Analyzing a contract (`POST /analyze-contract`)

Routes into [`lai.analyzer`](../src/lai/analyzer/) — the V2 analyzer. It runs
the contract against per-type **playbooks** with Qwen3.6-27B in thinking mode,
produces structured findings against a Pydantic `schema`, and a `reconciler`
dedupes overlapping findings. Progress is pollable; the full result is fetched
separately.

---

## Part C — The DDiQ report service (`lai-backend`, :18001)

DDiQ ("due-diligence intelligence") turns a **whole data room** of wind-park
documents into a structured due-diligence report. It's a separate codebase
([`micro-services/`](../micro-services/)), Dockerized.

### C1 — Uploading documents (`POST /ddiq/documents/upload`)

```
PDF  →  lai.common.pdf.PdfExtractor (PyMuPDF text + Tesseract OCR fallback)
     →  lai.common.chunk.Chunker (German-legal-aware)
     →  4096-dim Qwen3-Embedding-8B vectors (via lai.common.embedding)
     →  pgvector (ddiq_documents / ddiq_doc_chunks tables)
```

DDiQ adopted the `lai.common.pdf` + `lai.common.chunk` primitives in commit
`9c0a8cf` so bug fixes land once across both backends.

### C2 — Generating a report (`POST /ddiq/report/generate/async`)

Returns `{ report_id, status: "queued", cached? }` immediately. A
`ThreadPoolExecutor` worker then runs the pipeline; a **request fingerprint**
(sorted doc IDs + preset + project name) deduplicates — an identical request
returns the cached report instantly. Progress is checkpointed incrementally as
JSONB so a crash can resume (`reap_orphans()` runs on startup). Poll
`GET /ddiq/report/{id}/status`, fetch `GET /ddiq/report/{id}` when done.

A **guardrail layer** (`_guardrail.py`) validates LLM output before it lands
in the report; a **deterministic cross-source reconciler** (`_reconcile.py`)
merges findings from multiple documents. Geocoding has a plausibility gate +
cache TTL.

Inside the worker, two things run:

**Eight LLM extraction passes** over the uploaded documents (Qwen3.6-27B):
section analysis, timeline, cross-document consistency, Rückbau-bond check,
Grundbuch matching, WEA (turbine) status, infrastructure, and findings.

**The cadastral pipeline** ([`cadastral_pipeline.py`](../micro-services/cadastral_pipeline.py))
— a 13-step parcel workflow: project area → collect parcels from the German
**ALKIS INSPIRE WFS** services (12 federal-state endpoints) → extract parcels
named in contracts → match contract parcels to real cadastral polygons →
classify → produce spatial output. Geocoding via Nominatim. Applies the **10H
rule** (Bavarian turbine-setback distance).

The result is a report with sections, turbine statuses, parcels, findings,
timeline, cross-doc findings, Grundbuch checks, the Rückbau bond, and a
**GeoJSON** layer (`GET /ddiq/report/{id}/geojson`) loadable into QGIS/ArcGIS.

---

## Part D — How the pieces connect at runtime

```
                 ┌─────────────┐         ┌──────────────┐
   browser ────▶ │  LAI-UI     │ ──────▶ │  serve_rag   │ :18000  (host process, GPU 1)
   :5173         │  (React)    │ ──┐     │  + in-proc   │
                 └─────────────┘   │     │    reranker  │
                                   │     └──────┬───────┘
                                   │            │
                                   │            ├──▶ embedding vLLM   :8003 (GPU 1)
                                   │            └──▶ analyzer LLM     :8005 (GPU 0)
                                   │                  (Qwen3.6-27B)
                                   │     ┌──────────────┐
                                   └───▶ │  lai-backend │ :18001  (Docker)
                                         │   (DDiQ)     │ ──▶ embedding + analyzer LLM
                                         └──────┬───────┘ ──▶ ALKIS WFS / Nominatim
                                                │
                              PostgreSQL + pgvector   ◀── corpus + DDiQ tables
                              SQLite (sessions, pipeline_local.db)
```

Both backends are stateless-ish front-ends over the **same two model servers**
(embedding + analyzer LLM) and the **same PostgreSQL**. `serve_rag` additionally
holds the big in-RAM embedding matrix and the in-process reranker; DDiQ reaches
the reranker over HTTP. Both consume `lai.common` primitives so bug fixes land
once across both codebases.

**Track B — corpus → pgvector migration:** `scripts/ops/migrate_corpus.py`
streams the SQLite corpus into pgvector with a `topup` daemon that keeps the
two stores in sync as Step 6 emits new embeddings. `serve_rag` still uses the
in-RAM SQLite matrix today; the switch to pgvector retrieval is a separate
commit after the HNSW index finishes building (per
[`DEMO_STATUS.md`](DEMO_STATUS.md)).

**Observability:** both backends expose `/metrics` for Prometheus, scraped by
the stack at [`infra/monitoring/`](../infra/monitoring/) (`docker compose -f
infra/monitoring/docker-compose.yml up`). A 9-panel Grafana dashboard renders
request latency, token usage, citation-validation counts, and retrieval
quality.

Bring it all up with [`scripts/ops/start.sh`](../scripts/ops/start.sh) (Docker)
or [`start-host.sh`](../scripts/ops/start-host.sh) (Docker-free, host
processes). See [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md) for the service/port
map. (The live deployment currently diverges from the compose topology in
several ways — `harsh/TECH_STACK.md` §14 catalogs them.)

---

## Where to go next

| You want to… | Read |
|---|---|
| Current v1 demo state + remaining work | [`DEMO_STATUS.md`](DEMO_STATUS.md) |
| Master strategy + 10-day roadmap + USPs | [`LAI_V1_STRATEGY.md`](LAI_V1_STRATEGY.md) |
| See the tech stack | Tech Stack table in [`PROJECT_STATUS.md`](PROJECT_STATUS.md) (+ `harsh/TECH_STACK.md` for the exhaustive code-grounded inventory) |
| Architecture diagram + parameters | [`architecture/overview.md`](architecture/overview.md) |
| Feature list / what ships | [`MVP_DELIVERY.md`](MVP_DELIVERY.md) |
| Architecture decisions on `lai.common.llm` | [`adr/`](adr/) (ADRs 0000–0004) |
| Contributor contract + quality gate | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Per-screen UI design | [`UI_GUIDE.md`](UI_GUIDE.md) |
| Onboard as a developer | [`PROJECT_STATUS.md`](PROJECT_STATUS.md), [`DEVELOPMENT.md`](DEVELOPMENT.md) |
| Run / operate the stack | [`../scripts/ops/README.md`](../scripts/ops/README.md) |
