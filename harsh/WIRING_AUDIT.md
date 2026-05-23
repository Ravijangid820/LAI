# LAI v1 — Wiring & Integration Audit

**Date:** 2026-05-19
**Branch:** `v2-restructure`
**Scope:** for every shipped subsystem, prove the code path is wired
end-to-end, NOT just that the file exists.

Symbols:
| Symbol | Meaning |
|---|---|
| ✅ wired | Code consumes the module at the documented call site |
| 🔄 wired-but-runtime-stale | Code is correct; running process predates the wiring → invisible in live API until restart |
| 🟡 partial | Some routes / sites wired, others not |
| ❌ not wired | Module exists but no consumer call site |
| ⛔ intentional | Documented decision not to wire (e.g. reranker model mismatch) |

---

## 1. `lai.common.*` subpackage consumer wiring

### `lai.common.llm` — ✅ fully wired

- `serve_rag.py:65` `from lai.common.llm import ChatMessage, LlmConfig, SyncLlmClient`
- `ddiq_report.py:32` `from lai.common.llm import …`
- Both `llm_call` / `llm_json` in DDiQ + `llm_generate` in serve_rag route through `SyncLlmClient`.

### `lai.common.reranker` — ⛔ intentionally not wired

- **No consumer in `serve_rag.py` or `micro-services/`.** Confirmed.
- Documented decision: `serve_rag` uses its own in-process
  `Qwen3-Reranker-8B` (different model, higher quality); the common
  module targets a smaller TEI cross-encoder. Adoption would
  downgrade quality.

### `lai.common.embedding` — ❌ not wired

- **No consumer.** `serve_rag.py:71` (per the working-tree
  inspection) has its own `_get_embedding_client()` helper.
- DDiQ still calls `requests.post(EMBEDDING_URL, …)` directly in
  `embed_texts` / `embed_single`.
- **This is the missing wiring** — `lai.common.embedding` ships, has
  tests, and is consumed by nothing live.

### `lai.common.pdf` — ✅ wired (commit `9c0a8cf`)

- `ddiq_report.py:64` `from lai.common.pdf import PdfExtractor, PdfExtractorConfig`
- `ddiq_report.py:66` `_PDF_EXTRACTOR = PdfExtractor(...)`
- `extract_pdf_text` is now a thin shim. Smoke-tested live last
  session: `pdf.extract.complete elapsed_seconds=0.005 …` log fires.

### `lai.common.chunk` — ✅ wired (commit `9c0a8cf`)

- `ddiq_report.py:63` `from lai.common.chunk import Chunk, Chunker, ChunkerConfig`
- `ddiq_report.py:78` `_CHUNKER = Chunker(...)`
- `chunk_text` shim returns the legacy `[{idx, text}]` shape; verified
  4-chunk output on 3800-char input.

### `lai.common.citation` — ✅ wired (commit `8431797` / `86b7b31`)

- `serve_rag.py:62` `from lai.common.citation import validate_citations`
- Two call sites:
  - `serve_rag.py:1487` non-streaming `/query` path
  - `serve_rag.py:1905` streaming `/query/stream` path
- Output mutates the answer text AND attaches `citation_validation`
  to `QueryResp`.

### `lai.common.auth` — ✅ wired backend; route coverage is real

- `auth_dep.py:22` (DDiQ side) + `serve_rag.py:75-89` (chat side)
- `TokenIssuer` + `build_get_current_user` are concrete imports, not
  forward references.
- **Route audit DDiQ** — 12 protected routes, all carry
  `user: CurrentUser = Depends(get_current_user)`:
  `/documents`, `/documents/upload`, `/report/generate/async`,
  `/report/{id}/status`, `/report/generate`, `/reports`,
  `/report/{id}` (GET + DELETE), `/report/{id}/geojson`,
  `/report/{id}/validate`, `/project-area`. Only `/config/map-tiles`
  is intentionally public (returns static map-tile URL config).
- **Route audit serve_rag** — every route except `/health` is
  `Depends(get_current_user)`: `/query`, `/query/stream`, `/upload`,
  `/analyze-contract` (3 endpoints), `/sessions` (4 endpoints),
  `/feedback` (2 endpoints), `/sessions/{id}/document`, etc.
- **SQL audit** — 19 `WHERE user_id = …` filter sites in
  `ddiq_report.py`, including all critical reads
  (`/documents`, `/report/{id}`, `/reports` list, `/report/{id}/status`,
  `/project-area`, the report-generate dedup-fingerprint check).

### `lai.common.jurisdiction` — ✅ wired (in serve_rag) but ❌ NOT in DDiQ

- `serve_rag.py:64` `from lai.common.jurisdiction import check_jurisdiction, detect_bundesland`
- `serve_rag.py:1029` `JurisdictionWarningOut` model
- `serve_rag.py:1512` `_run_jurisdiction_check(...)` invoked in `/query`
- `serve_rag.py:1564` + `:1572` attached to response
- `serve_rag.py:1605-1606` Prometheus counters incremented when
  warnings fire (`jurisdiction_warnings_responses_total`,
  `jurisdiction_warnings_total`)
- `serve_rag.py:1921` invoked in `/query/stream` too
- ❌ **DDiQ does not call the validator** — its report sections + WEA
  rows can still cite cross-Bundesland rules without flagging.

---

## 2. Live runtime vs. committed state

This is the failure mode that made the live API look broken in the
boss's screenshot.

| Subsystem | Code committed | Live serve_rag has it? | Reason |
|---|---|---|---|
| `citation_validation` field in `QueryResp` | ✅ `86b7b31` | ❌ **No** (response keys are still `['answer','chunks','mode','session_id','timings','tokens']` — verified just now) | serve_rag PID 3413088 started **2026-05-15 11:28**, pre-`86b7b31` (committed 2026-05-17) |
| `cite_id` + `source_kind` on `Chunk` | ✅ `86b7b31`/`8431797` | ❌ **No** | Same staleness |
| `POST /query/stream` route | ✅ `85008f1` / `a67088c` | ❌ **HTTP 404** (just probed) | Same |
| `POST /feedback` route | ✅ `85008f1` | ❌ **HTTP 404** (just probed) | Same |
| `target_language` field accepted | ✅ `85008f1` | ❌ silently dropped | Same |
| `jurisdiction_warnings` field emitted | ✅ `85008f1` | ❌ **No** | Same |
| Auth `Depends(get_current_user)` on routes | ✅ `c15f2f1` / `85008f1` | ❌ **No** (`/query` returns 200 without a token — just probed) | Same |
| `JWT_ACCESS_SECRET` env required | ✅ in Sumit's auth | n/a — process loaded its old code | Same |

**Three commits land live on next restart**:
- `86b7b31` (citation_validation)
- `c15f2f1` (auth backend)
- `85008f1` (jurisdiction / feedback / SSE / metrics / target_language)

A fourth commit (the upcoming `_do_rag` swap) is gated on HNSW
finishing.

---

## 3. Per-failure-mode wiring trace

For each row in the boss's "Done but not connected" screenshot:

### 3.1 `lai.common.reranker` not consumed

- ✅ confirmed at code level. **Intentional**. Not a wiring bug.

### 3.2 `lai.common.pdf` not consumed

- ✅ **wired this session** (commit `9c0a8cf`). DDiQ uses it. Live
  in the rebuilt `lai-backend` container.
- ❌ NOT wired in `serve_rag`'s upload path (uses Docling there).
  Acceptable: Docling is a different higher-fidelity tool for
  contract-mode chat; `lai.common.pdf` is for DDiQ's bulk-ingest.

### 3.3 `lai.common.chunk` not consumed

- ✅ **wired this session** (commit `9c0a8cf`). DDiQ uses it.
- ❌ NOT wired in `serve_rag` (uses its own Docling-tokenised chunks
  produced inline). Same reasoning as PDF.

### 3.4 Track B migration target tables — `_do_rag` still SQLite

- 🔄 **In progress.** Data is in pgvector; HNSW index at 95% loading;
  `_do_rag` swap is the next commit after HNSW completes.

### 3.5 `POST /query/stream` SSE not called from UI

- ✅ frontend `streamQuery()` exists and is the ONLY call from
  `DashboardChat.handleSendMessage` (LAI-UI commit `4474388`).
- 🔄 backend route exists in code (`serve_rag.py:1745`) but live
  process returns HTTP 404 — runtime-stale.

### 3.6 `jurisdiction_warnings` UI render

- ✅ **wired this session** (LAI-UI commit `94053ad`). Amber chip
  next to the unbelegt badge, with multi-warning tooltip.
- 🔄 won't appear in live UI until serve_rag restart emits the field.

### 3.7 `<LanguageToggle>` / `target_language`

- ✅ frontend toggle persists choice; `streamQuery(question, sessionId,
  handlers, targetLanguage)` sends `target_language` on every call
  (LAI-UI `4474388`).
- ✅ backend `QueryReq.target_language: Optional[str]` accepted at
  line 935; `_language_directive()` helper at 167; appended to system
  prompt by both `build_rag_messages` and `build_chat_messages`.
- 🔄 invisible in live API because runtime is stale.

### 3.8 `<ConfidentialityBadge>`

- ✅ wired pre-existing.

---

## 4. NEW wiring gaps discovered during this audit

Things that ARE shipped but quietly not wired anywhere:

| Gap | Where | Severity |
|---|---|---|
| **`lai.common.embedding` has no consumer** | DDiQ `embed_texts` / `embed_single` still hit `EMBEDDING_URL` via `requests`; `serve_rag` has its own helper | High — was a Phase 1a deliverable; missing the retries + Prometheus metrics |
| **`lai.common.reranker` has no consumer** | Both backends use other rerankers | Documented as intentional |
| **`lai.common.jurisdiction` not called from DDiQ** | DDiQ reports can cite wrong-Bundesland rules without warning | Medium — DDiQ has its own bbox gate, but no cross-rule jurisdiction check |
| **`lai.common.pdf` not used by serve_rag** | serve_rag uses Docling | Acceptable trade-off (different tool for different need) |
| **`lai.common.chunk` not used by serve_rag** | serve_rag uses Docling | Same |
| **No `lai.retrieval` / `lai.common.retrieval` package exists** | Track B keystone; both backends need it | Critical — gated on HNSW completion |
| **No `lai.connectors` package** | DDiQ still has hand-rolled ALKIS + Nominatim | Medium — blocks Phase 2B (MaStR, Handelsregister) |
| **Frontend on-prem move not started** | `LAI-UI/vercel.json`, `LAI-UI/wrangler.json` still committed | Medium — conflicts with on-prem mandate |
| **Monitoring stack not deployed** | `Docker/monitoring/` exists; no `lai_prometheus` container running. `lai.common.*` modules emit metrics that nobody scrapes. | Medium — invisible operational signal |
| **Celery worker not deployed** | DDiQ uses `ThreadPoolExecutor(max_workers=2)`; a service restart abandons running reports. `redis` + `celery[redis]` declared in deps. | Medium (E13 from `IMPLEMENTATION_GUIDE`) |
| **`ddiq_report.py` still a 3,168-LOC god-file** | C3 from the guide | Low-medium — refactor not started; growing under feature pressure |

---

## 5. Count of remaining work (against `IMPLEMENTATION_GUIDE §9`)

| Phase | Done | In-flight / staged-for-restart | Pending |
|---|---:|---:|---:|
| §9.1 Output quality (10 items) | 5 ✅, 1 partial | — | A3, A5, A6, A7, A10 |
| §9.2 Corpus silo (6 items) | 1 ✅ | B1, B2 (HNSW), B3 (Step 6) | B4, B6 |
| §9.3 Codebase (4 items) | 3 ✅ | — | C3 |
| §9.4 Security (6 items) | 2 ✅, S2 partial | — | S4 (serve_rag CORS), S5, S6 |
| §9.5 Fault tolerance (14 items) | 7 ✅, 3 partial | — | E9, E10, E13; E14 deferred |
| §9.6 Operational (5 items) | 0 | O3 staged | O1 (partial), O2, O4, O5 |
| §9.7 Data quality (5 items) | 1 ✅ (D4 endpoint) | D1 running | D2, D3, D4 memory, D5 |
| §9.8 Topology drift (8 items) | 0 | — | All 8 |
| **TOTAL** | **19 ✅** | **5 in-flight / staged** | **34 pending** |

`19 of (19+5+34) = 19/58 ≈ 33%` done; `(19+5)/58 ≈ 41%` after the
HNSW + restart pair lands.

---

## 6. The single action that unlocks the most

**Restart serve_rag after the `_do_rag` swap commits.**

That one event flips all of these from 🔄 to ✅:

- citation_validation field in response
- jurisdiction_warnings field in response (+ amber chip in UI)
- POST /query/stream live (HTTP 200 not 404)
- POST /feedback live
- target_language actually steers answer language
- Auth `Depends(get_current_user)` actually enforces tokens
- The `WHERE user_id = …` filters actually scope queries to the JWT
- `_do_rag` reads from pgvector (sub-100ms) instead of in-RAM matrix
- Startup time drops from ~14 min (load 144 GB matrix) to seconds

Currently blocked on HNSW build completing (~95% loading, ETA hours).
The watcher (PID 600746) will notify the second `indisvalid='t'`.

---

## 7. The next 5 commits (recommended order, post-HNSW)

1. **`_do_rag` swap → pgvector** — closes B1/B2; unblocks `lai.retrieval` design.
2. **serve_rag restart command + healthcheck** — one downtime, 6 features live.
3. **`lai.common.embedding` adoption in DDiQ + serve_rag** — closes the missing wiring identified in §4.
4. **S4 CORS allow-list in `serve_rag.py:1234`** — one-line fix.
5. **`lai.retrieval` package skeleton** — Track B keystone; unblocks A5 statutory grounding.

After that the cleanup queue (§9.8 topology, O1 tests, O2 monitoring,
C3 god-file split) can proceed in parallel.
