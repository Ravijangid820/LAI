# LAI — Technical Documentation

**Legal AI for German Wind-Energy Due Diligence**
On-premise · citation-grounded · bilingual (DE/EN)

*Last updated: 2026-05-22 · branch `v2-restructure`*

---

## 1. What LAI is

LAI lets a German law firm upload a **data room** (a VDR — one to hundreds of
documents) into a single workspace ("Matter") and ask questions about it in
plain German **or** English. Every answer is grounded with clickable
citations to either the uploaded documents (`[M-n]`) or a pre-indexed
326 GB German legal corpus (`[C-n]`), and runs entirely **on the firm's own
GPUs** so it is usable on client matters under BRAO § 43a
(Verschwiegenheit).

### The four USPs

| USP | Why it matters in Germany |
|---|---|
| **On-premise / firm-hosted** | BRAO § 43a forbids sending Mandanten-Daten to US cloud. Harvey/OpenAI are effectively unusable for client work; LAI is not. |
| **Pre-indexed 326 GB German legal corpus** | Statutes, commentaries, rulings already embedded + reranked. New matters benefit from day 1, zero onboarding. |
| **Citation-grounded answers** | Every claim carries an `[M-n]`/`[C-n]` handle → click → exact source passage + page. Uncited claims are marked `(unbelegt)`. |
| **Bilingual EN/DE** | Ask German documents in English; the answer mirrors the question's language while quoting German law verbatim. |

---

## 2. System architecture

```
                         ┌─────────────────────────────────────────┐
   Browser (React/Vite)  │  serve_rag  (FastAPI, port 18000)         │
   ───────────────────►  │                                           │
   /upload  /query/stream│  ┌─ ingestion ──────────────────────────┐ │
   /sessions/{id}/docs   │  │ VLM-OCR (scanned) / docling (text)    │ │
                         │  │ → chunk → embed → index               │ │
                         │  │ background ThreadPool, live progress  │ │
                         │  └───────────────────────────────────────┘ │
                         │  ┌─ retrieval ──────────────────────────┐ │
                         │  │ matter (per-session) + corpus hybrid  │ │
                         │  │ dense + BM25 + RRF + reranker         │ │
                         │  └───────────────────────────────────────┘ │
                         │  ┌─ generation ─────────────────────────┐ │
                         │  │ doc-first routing · language detect   │ │
                         │  │ citation validate · jurisdiction gate │ │
                         │  └───────────────────────────────────────┘ │
                         └───────┬───────────────┬───────────────┬────┘
                                 │               │               │
                    ┌────────────▼──┐  ┌─────────▼────────┐  ┌────▼──────────┐
                    │ vLLM 27B (VL) │  │ Embedding 8B      │  │ Reranker 8B   │
                    │ :8005 chat+OCR│  │ :8003 Qwen3-Emb   │  │ in-process GPU│
                    └───────────────┘  └──────────────────┘  └───────────────┘
                                 │
            ┌────────────────────┼─────────────────────────────┐
            │ Postgres + pgvector (:5434)  │  SQLite (sessions)  │
            │  corpus_child_chunks  (24M)  │  sessions, messages │
            │  matter_chunks (per-Matter)  │  matter_documents   │
            └──────────────────────────────┴─────────────────────┘
```

### Model stack (all on-prem, `HF_HUB_OFFLINE=1`)

| Role | Model | Notes |
|---|---|---|
| Chat + OCR | **Qwen3.6-27B** (vision-capable) via vLLM `:8005` | thinking-mode OFF for chat; also used as the OCR vision model |
| Embeddings | **Qwen3-Embedding-8B** (4096-d, Matryoshka) `:8003` | truncated to 4000-d `halfvec` for pgvector |
| Reranking | **Qwen3-Reranker-8B** cross-encoder | in-process, GPU; auto-selects GPU with most free VRAM |

Hardware: 2× RTX Pro 6000 (96 GB VRAM each).

### Key source files

| Area | File |
|---|---|
| API, routing, ingestion, generation | `src/lai/api/serve_rag.py` |
| pgvector client (corpus + matter) | `src/lai/common/retrieval/client.py` |
| Session/message/document persistence | `src/lai/persistence.py` |
| Corpus hybrid retrieval helpers | `src/lai/search/eval.py` |
| Citation validator | `src/lai/common/citation/` |
| Jurisdiction gate | `src/lai/common/jurisdiction/` |
| Frontend chat + documents | `LAI-UI/src/react-app/` |

---

## 3. Features

### 3.1 Data-room Matter (per-session pgvector index)

A "Matter" is one session holding many uploaded documents (`[M-1]..[M-n]`,
where `n` is a stable `doc_index`). Each document is chunked into
page-tagged passages, embedded, and stored in the `matter_chunks` table in
the **same pgvector database** as the corpus.

- **Retrieval**: *exact* KNN filtered by `session_id` (`WHERE session_id = ?
  ORDER BY embedding <=> q LIMIT k`), not the shared HNSW index. Rationale:
  a single Matter holds at most a few thousand chunks, so an exact scan over
  just that session's rows is both fast and **perfect-recall**, whereas a
  shared HNSW index would post-filter by `session_id` and silently lose
  recall.
- **Scales** to a real VDR: only the top-k reranked passages enter the
  prompt, so behaviour is identical at 3 documents or 300.
- **Citations**: retrieved passages are grouped back under their document so
  the handle stays `[M-doc_index]` (consistent with the document list);
  every document in the Matter also gets a baseline chunk so any cited
  `[M-n]` resolves and opens its PDF.

`RetrievalClient.ensure_matter_table / index_matter_document /
matter_dense_search / delete_matter_chunks`.

### 3.2 Vision-LLM OCR for scanned PDFs

German VDR scans defeat classic OCR — e.g. "Enercon **E-70** E4" is read
"E-79" by Tesseract at every DPI/preprocessing/engine combination, because
the degraded "0" glyph closes at the top. LAI routes **scanned** PDFs (no
text layer, detected via `pdftotext`) to the **on-prem vision model**, which
reads the same pixels *in context* and gets it right. Text-layer PDFs and
DOCX/HTML keep the faster docling path.

- Pages rendered with `pdftoppm` at `LAI_VLM_OCR_DPI` (default 200) →
  transcribed page-by-page to Markdown → joined with `<!-- Seite N -->`
  markers so every passage carries a **page number**.
- Toggle `LAI_VLM_OCR=0` to force the legacy docling/Tesseract path.
- On any failure it degrades gracefully to docling — an upload is never
  blocked.

`convert_document / _pdf_has_text_layer / _render_pdf_to_images /
_vlm_ocr_image / _vlm_ocr_pdf`.

### 3.3 Non-blocking background ingestion + live progress

Uploads return the instant the bytes are on disk; OCR + embed + index run in
a bounded `ThreadPoolExecutor` (`LAI_INGEST_WORKERS`, default 4). Concurrent
OCR requests batch on the GPU via vLLM, so a big data room ingests as fast
as the GPU allows — not one-at-a-time.

- Per-document status lifecycle: `queued → processing → done | failed`,
  persisted on `matter_documents` (`status`, `pages_done`, `pages_total`,
  `n_chunks`, `error`).
- The client polls `GET /sessions/{id}/documents`; the UI shows a live
  page-by-page progress bar, then a green checkmark (or a red error that
  isolates the one failed document).
- **Restart recovery**: on startup, documents left `queued`/`processing` by
  an interrupted process are re-enqueued — nothing strands in a spinner.

`_ingest_document_job / _enqueue_ingestion / _recover_unfinished_ingestion`.

### 3.4 Document-first routing + corpus on demand

Once a document is uploaded, questions are answered **from the uploaded
documents only** by default. The legal corpus is consulted *only* for
legal-knowledge questions — statute references (`§`, BGB, BauGB, BImSchG…),
legal doctrine ("Rückbauverpflichtung", "10H", "gesetzlich", "rechtlich"),
market practice ("marktüblich", "market standard"), or comparison ("compare",
"andere Verträge"). Pure contract-extraction ("Welche Pacht?", "which
turbine type?") stays document-only.

This prevents the failure mode where a lease-term question was answered by
quoting an **unrelated** corpus contract. Modes: `contract` (docs only),
`rag+contract` (docs + corpus), `rag` (corpus only), `chat`.

`session_uses_contract / is_legal_knowledge_question / wants_corpus`.

### 3.5 Bilingual answers (auto language detection)

The question's language is detected server-side (function-word + umlaut
heuristic) and an **explicit** directive is emitted — "answer in English" /
"auf Deutsch" — rather than relying on the model to mirror, which loses
under a heavily-German prompt. German statutes/clauses are always quoted
**verbatim in German** regardless of answer language, so the cited text
matches the source preview. The manual DE/EN UI toggle was removed.

`_detect_question_language / _effective_language / _language_directive`.

### 3.6 Citations: handles, passages, pages, panel

- The model is instructed to cite a stable handle on every claim: `[C-n]`
  (corpus) or `[M-n]` (uploaded document).
- Matter citations resolve to the **specific passage** and **page**; the
  citation panel shows the passage text and scrolls the PDF preview to
  `#page=N`.
- **Server-side validator** strips any handle the model emitted that was not
  in the prompt and rewrites the surrounding sentence `(unbelegt)` — the
  difference between a draft and defensible work product.
- **Document manifest**: the prompt always lists the Matter's documents so
  meta-questions ("which files do I have?") are answered correctly (capped
  at 40 with a count for large rooms).
- **Chunk persistence**: citation sources are saved with each message, so
  chips still resolve after a page reload / conversation switch.
- UI: footnote-style chips — 📄 *Dok. n* (amber, uploaded) / ⚖ *Recht n*
  (indigo, corpus) — with plain-language tooltips.

### 3.7 Jurisdiction sanity gate

Independent of citation validation: if an answer cites a Bundesland-specific
rule (e.g. Bavaria's 10H, Art. 82 BayBO) while the matter is in a different
state (e.g. Niedersachsen), an amber warning is attached. Catches the
"10H applied to a Lower-Saxony project" defect class.

### 3.8 Data lifecycle / deletion

Deleting a chat removes **everything**: session row, messages,
`matter_documents`, feedback (SQLite cascade), the uploaded files on disk
(glob `{sid}*`), **and** the per-Matter `matter_chunks` in pgvector. Verified
in production (chunks → 0 after a UI delete; zero orphans).

### 3.9 Corpus hybrid retrieval (foundation)

The 326 GB corpus is served from `corpus_child_chunks` (24M rows,
`halfvec(4000)` HNSW `halfvec_cosine_ops`). Retrieval = dense pgvector ANN +
BM25 (SQLite FTS5, German tokenizer) fused by Reciprocal Rank Fusion, then
Qwen3-Reranker cross-encoder reranking, parent-window dedup. Replaced a
~144 GB in-RAM numpy matrix.

---

## 3b. Additional subsystems (beyond the chat-first v1 core)

The chat data-room (§3.1–3.9) is the **v1** deliverable. The following
subsystems also exist in the codebase; some are demoed, some are v1.1 scope.

### 3b.1 DDiQ — automated Due-Diligence report

A structured, statute-anchored DD report generated from a Matter's
documents. Separate from chat: it runs as an **async Celery task**
(`micro-services/ddiq_report.py` + `worker.py`), because a full report is
many LLM calls and must survive worker restarts.

- **4 sections** — `overview`, `land`, `permits`, `economics`.
- **~39 statute-anchored questions** — each carries a `label`, a `question`
  phrased the way a DD lawyer would, and an **anchor** (the German legal
  hook, e.g. *"BauGB §35 Abs. 1 Nr. 5 / Regionalplan"*, *"BImSchG §§4, 6, 10,
  15"*) so the model stays grounded instead of drifting into generic Q&A.
- **Ampel risk triage** — every answer row gets `green` / `yellow` / `red`
  (red = material gap / non-compliance, yellow = risk worth flagging) plus a
  short note and evidence-chunk references.
- **Deterministic cross-source reconciler** (`_reconcile.py`) — when sources
  disagree on a value (total MW, turbine count, Bundesland), a deterministic
  reconciler picks the authoritative value rather than letting the LLM
  guess, so the report doesn't contradict itself row-to-row.
- **Output guardrail** — strips defensive-AI boilerplate; an Ausgabeblatt of
  labelled rows + a project location/map.
- **Celery reliability** — `acks_late`, `task_reject_on_worker_lost`,
  prefetch 1, configurable soft/hard time limits (`DDIQ_SOFT/HARD_TIME_LIMIT_S`).

*Status: works (demoed); chat-first v1 prioritises the conversational flow.
The v1.1 direction is "render-from-conversation" so the report inherits the
chat's live citations.*

### 3b.2 Contract analyzer (`/analyze-contract`)

Structured clause-by-clause analysis of a single German wind-energy contract
(`src/lai/analyzer/`), using Qwen3.6-27B in **thinking mode** with
JSON-guided decoding.

- **Playbooks** (`playbooks.py`) — per-contract-type analysis rules
  (Pacht/lease, maintenance, loan, grid…), so the analyzer checks the
  clauses that matter for that document class and flags missing-required
  clauses.
- **Cadastral NER** (`cadastral_ner.py`) — extracts Gemarkung / Flur /
  Flurstück parcel identifiers.
- **Reconciler** (`reconciler.py`) — dedupes/merges extracted findings.
- **Schema** (`schema.py`) — typed I/O; severity-graded issues with
  rationale + suggested redline.
- Endpoints: `POST /analyze-contract`, `GET /analyze-contract/progress`,
  `GET /analyze-contract/full`. V1/V2 selectable; V2 needs a separate
  analyzer LLM endpoint (`ANALYZER_LLM_API_URL`), else falls back to V1.

### 3b.3 Authentication (`src/lai/common/auth/`)

Real JWT auth, not a stub: `tokens.py` (access/refresh issuance + verify),
`hashing.py` (password hashing), `repository.py` + `db.py` (Postgres user
store), `dependencies.py` (`get_current_user`, per-request `AuthDeps`).
Single-sourced JWT secret (`LAI_AUTH_JWT_ACCESS_SECRET`) shared by chat and
DDiQ so they can't drift. Every session/document/message endpoint is
**tenant-scoped** by `user_id` — no cross-tenant reads or writes. Optional
email (`LAI_EMAIL_*`) for password reset; without it, reset tokens are
issued but not mailed.

### 3b.4 Feedback

`POST /feedback` records a thumbs-up/down (+ optional reason/comment) scoped
to a specific assistant message; `GET /sessions/{id}/feedback` lists
verdicts. Upsert keyed on `(user, session, message)` so toggling collapses
to one most-recent verdict; replayed on rehydrate so a refresh shows the
lawyer their prior rating.

### 3b.5 Session memory

Two layers keep multi-turn chat coherent: a **rolling window** of recent
turns, plus **pinned session metadata** (LLM-extracted stable facts — user
name, project, key dates) refreshed every few turns so a fact stated early
survives after it rolls out of the window.

### 3b.6 Monitoring

Prometheus metrics at `GET /metrics` (domain counters: query mode, language,
latency, chunks returned, citation-validation outcomes, jurisdiction
warnings; plus retrieval-client metrics) for Grafana dashboards.

### 3b.7 Multi-format ingestion

Beyond scanned-PDF VLM-OCR (§3.2): text-layer PDFs and DOCX/HTML/CSV/XLSX go
through docling (Tesseract deu+eng OCR fallback for image regions); `.txt` /
`.md` decoded directly. Tables extracted to structured rows for the analyzer.

---

## 3c. Corpus data pipeline (`src/lai/pipeline/`)

The offline pipeline that built the 326 GB corpus from 671 GB of raw
documents. Six numbered steps, driven by a CLI
(`python -m lai.pipeline.cli step1 …`); state in PostgreSQL (or SQLite +
on-disk MinIO via `local_storage.py` when Docker isn't available).

| Step | Module | What it does |
|---|---|---|
| 1 | `convert.py` | Raw files (MinIO) → normalized text **segments** preserving structure (sections, pages), one JSONL line per doc. No chunking yet. |
| 2 | `chunk.py` | Segments → **parent/child chunks** with legal-aware German splitting. Parent ≈ 1024–2048 tokens (fine-tuning context); child ≈ 512 tokens (RAG retrieval). |
| 3 | `classify.py` | Classifies parent chunks into **legal domains** (immissionsschutzrecht, energierecht, baurecht, …) with Qwen2.5-72B; history kept with model/prompt versioning. |
| 4 | `enrich.py` | **Contextual Retrieval** (Anthropic-style): a short document-level context prefix per child chunk, prepended before embedding — cuts retrieval failure materially. |
| 5 | `generate.py` | ~200K synthetic **fine-tuning Q&A** samples (rag_qa / summarize / explain / compare) as ChatML — for model training, not runtime. |
| 6 | `embed.py` | Embeds child chunks (Qwen3-Embedding-8B) → pgvector, builds the BM25 `tsvector`; HNSW + GIN indexes created after bulk load. |

`local_storage.py` lets the pipeline run without Docker (reads MinIO's
bind-mounted data directly, swaps Postgres for SQLite state). The runtime
serving DB (`corpus_child_chunks`) is the migrated output of this pipeline
(see `scripts/ops/migrate_corpus.py`, §8.2).

## 3d. Geospatial / cadastral connectors (`src/lai/common/connectors/`)

The location + map machinery behind DDiQ. Both clients follow the shared
`lai.common` discipline (sync client + pydantic-settings + tenacity retries +
Prometheus metrics + typed exceptions).

- **`nominatim.py`** — OpenStreetMap geocoder: free-text address →
  latitude/longitude. Used to place a project on the map. Sync-only (low
  volume, OSM throttles to 1 req/s); queries most-specific-first
  (Gemeinde → Landkreis → Bundesland) so a silent document doesn't
  geocode to a state centroid (the Bremen-instead-of-Cuxhaven bug fix).
- **`alkis.py`** — German cadastral **INSPIRE WFS** client across the 12
  state services; lat/lng + Bundesland → real **Flurstück** polygons. Tries
  GeoJSON, falls back to GML 3.2 (half the states only speak GML).
- **`micro-services/bundesland_bbox.py`** — bounding boxes per Bundesland (to
  scope/validate cadastral queries).
- **`micro-services/cadastral_pipeline.py`** — the 13-step "Output Map"
  process: define project area → collect cadastral parcels → filter relevant
  → link to contracts → classify ownership/status → render map. Produces the
  parcel/ownership map in a DDiQ report.

---

## 4. API surface (selected)

| Endpoint | Purpose |
|---|---|
| `POST /upload` | Non-blocking: saves file, queues ingestion, returns at once |
| `GET /sessions/{id}/documents` | Per-document status + progress (UI polls this) |
| `GET /sessions/{id}/documents/{n}` | One document's bytes (PDF preview) |
| `POST /query/stream` | SSE chat — token stream + final `complete` with chunks, citations, jurisdiction warnings |
| `POST /query` | Non-streaming companion |
| `POST /analyze-contract` (+ `/progress`, `/full`) | Clause-by-clause contract analysis (V1/V2) |
| `GET /sessions/{id}` | Rehydrate a conversation (messages + persisted chunks) |
| `DELETE /sessions/{id}` | Full delete (DB + files + pgvector) |
| `POST /feedback` | Thumbs up/down per message |
| `GET /health` | Readiness (LLM, pgvector, session count) |
| `GET /metrics` | Prometheus |

---

## 5. Configuration (environment)

| Var | Default | Effect |
|---|---|---|
| `LLM_API_URL` | `http://localhost:8005` | vLLM chat + vision endpoint |
| `LLM_MODEL` | `qwen3.6-27b` | served model id |
| `LAI_EMBEDDING_BASE_URL` | `http://localhost:8003/v1` | embedding endpoint |
| `DB_HOST/PORT/NAME/USER/PASSWORD` | …`:5434`/`lai_db` | pgvector (corpus + matter) |
| `LAI_VLM_OCR` | `1` | vision-OCR for scanned PDFs (`0` = docling only) |
| `LAI_VLM_OCR_DPI` | `200` | render DPI for OCR |
| `LAI_INGEST_WORKERS` | `4` | background ingestion concurrency |
| `LAI_RETRIEVAL_*` | — | pool size, `hnsw_ef_search`, top-k, statement timeout |

Restart: `./scripts/ops/restart_serve_rag.sh` (SSH-proof; `/health` gate).

---

## 6. Request lifecycle (a chat turn)

1. **Route** — `session_uses_contract` (has docs + not smalltalk) decides
   document context; `is_legal_knowledge_question` decides whether to add
   the corpus.
2. **Retrieve** — matter: embed question → `matter_dense_search` → rerank →
   group by document. corpus (if triggered): dense + BM25 + RRF + rerank.
3. **Assemble** — manifest + system prompt (doc-only or statutory-grounding)
   + language directive + sources block (`[M-n]`/`[C-n]`).
4. **Generate** — vLLM streaming, thinking OFF.
5. **Validate** — strip fabricated handles → `(unbelegt)`; jurisdiction gate.
6. **Persist** — user + assistant messages with chunks; emit metrics.
7. **Stream** — tokens, then a `complete` event with answer + chunks +
   validation + warnings.

---

## 7. Known limits / roadmap

- Very large data rooms (1000s of scanned docs) are OCR-time-bound (~5–10 s
  per page on GPU); background processing keeps the UI responsive but total
  ingest takes time. Future: dedicated worker fleet (Celery) for horizontal
  scale.
- DDiQ structured report exists (`micro-services/ddiq_report.py`) but is out
  of the chat-first v1 scope (v1.1 "render-from-conversation").
- DOCX firm-letterhead export, deadline `.ics` extraction, audit-log viewer:
  deferred to v1.1.

---

## 8. Full module reference

### 8.1 Core infrastructure (`src/lai/core/`)

Shared foundation used across packages:

- **`config.py`** — nested Pydantic settings loaded from env / `.env`
  (validation, `SecretStr`, field descriptions, grouped defaults).
- **`logging.py`** — structured logging setup.
- **`models.py`** — shared core data models.
- **`constants.py`**, **`utils.py`**, **`exceptions.py`** — constants,
  helpers, and the core exception hierarchy.

Also in `lai.common`: `chunk/` (chunking helpers), `pdf/` (PDF helpers),
`reranker/` (Qwen3-Reranker wrapper), `llm/` (`SyncLlmClient`), `embedding/`
(`SyncEmbeddingClient`), `citation/` (validator), `jurisdiction/`
(Bundesland gate), `retrieval/` (pgvector client), `auth/` (JWT). Each is a
self-contained client/config/metrics/exceptions bundle.

### 8.2 Operations & deployment (`scripts/ops/`)

| Script | Purpose |
|---|---|
| `start.sh` / `start-host.sh` | Bring up the stack (containers / host processes) |
| `stop.sh` / `stop-host.sh` | Tear down |
| `status.sh` / `status-host.sh` | Health/readiness of each service (Postgres, vLLM, embedding, serve_rag) |
| `restart_serve_rag.sh` | SSH-proof serve_rag restart (`setsid`+`nohup`, `/health` readiness gate) |
| `migrate_corpus.py` | Loads the pipeline output into `corpus_child_chunks` (halfvec(4000), HNSW) — the runtime corpus |
| `resume_migration.sh` / `resume_step5.sh` / `resume_step6.sh` | Resume long-running migration / pipeline steps |
| `load_demo_matter.py` | Seed a demo Matter (e.g. `?session_id=lamstedt-demo`) |

### 8.3 Second API + DDiQ services (`micro-services/`)

| Module | Role |
|---|---|
| `api.py` | A second FastAPI ("Legal AI RAG backend, called by the React frontend"). Historically the `lai-backend`; **`serve_rag` is the active chat API** — `api.py` is a separate/legacy surface (see the architecture notes). |
| `ddiq_report.py` | DDiQ report generation (§3b.1) — sections, questions, Ampel, location. |
| `worker.py` | Celery worker that runs DDiQ async (crash-safe). |
| `_reconcile.py` | Deterministic cross-source value reconciler (numeric/categorical). |
| `_guardrail.py` | Post-generation output validator for DDiQ — strips defensive-AI boilerplate and fixes observed failure patterns before persisting. |
| `cadastral_pipeline.py` / `bundesland_bbox.py` | Parcel/map machinery (§3d). |
| `auth_dep.py` | `get_current_user` dependency for the micro-services API. |

`src/lai/api/email.py` — email config for password-reset (optional; without
`LAI_EMAIL_*`, reset tokens are issued but not mailed).

### 8.4 Frontend (`LAI-UI/src/react-app/`)

Vite + React + TypeScript + Tailwind. Auth via `AuthProvider` +
`ProtectedRoute`; routing in `App.tsx` (a `?session_id=` deep-link forwards
straight into the seeded chat).

**Pages**

| Page | Role |
|---|---|
| `Landing.tsx` | Marketing landing (root URL) |
| `Login` / `Signup` / `ForgotPassword` / `ResetPassword` | Auth flows |
| `Dashboard.tsx` | Dashboard home |
| `DashboardChat.tsx` | **The v1 product** — chat, upload, citations, document list |
| `DashboardDocuments.tsx` | Documents view |
| `DashboardProjects.tsx` | Projects view (per-project conversations) |
| `DashboardRisk.tsx` | Risk view |
| `DashboardSettings.tsx` | Account/preferences |

**Key chat components** (`components/chat/`)

| Component | Role |
|---|---|
| `ChatInput.tsx` | Composer (text + file attach + speech) |
| `ChatMessage.tsx` | One bubble (markdown, feedback buttons, badges) |
| `CitedMarkdown.tsx` | Renders answer markdown, turns `[C-n]`/`[M-n]` into chips |
| `CitationChip.tsx` | Footnote-style citation pill (📄 Dok. / ⚖ Recht) |
| `CitationPanel.tsx` | Right drawer: passage + PDF preview scrolled to page |
| `UnverifiedBadge.tsx` | `(unbelegt)` marker |
| `DocumentList.tsx` | Per-document live ingestion status (queued→processing→done) |
| `UploadProgress.tsx` | Live per-file upload batch above the composer |
| `DropZone.tsx` | Drag-and-drop upload (threads one session per data room) |
| `ConversationList.tsx` | Sidebar chat list |
| `NotificationsMenu.tsx` | Header bell dropdown |
| `MarkdownRenderer.tsx` | Plain markdown (non-citation contexts) |
| `TypingIndicator.tsx` | Streaming indicator |

API client: `lib/ragApi.ts` (chat stream, upload, sessions, documents,
feedback) and `lib/ddiqApi.ts` (DDiQ).

> Note: this is a structural reference. Page-level behaviour (Projects,
> Risk, Settings) is standard dashboard scaffolding; the substantive,
> tested product surface is `DashboardChat` + the chat components above.
