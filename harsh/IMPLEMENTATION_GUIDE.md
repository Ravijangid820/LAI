# LAI V1 — Implementation Guide

**Date:** 2026-05-15
**Status:** Master implementation reference. This document supersedes
`FINAL_REPORT.md`, `DDIQ_ROADMAP.md`, and the teammate's `LAI_V1_STRATEGY.md`
for the build phase. The earlier documents are retained in `harsh/` for
audit traceability.

**Source basis:**
- Original audit, deep research, and re-verification — `harsh/AUDIT.md`,
  `harsh/DEEP_RESEARCH.md`, `harsh/RE_VERIFICATION.md`,
  `harsh/VERIFICATION.md`, `harsh/TECH_STACK.md`,
  `harsh/ARCHITECTURE_BRIEF.md`, `harsh/ISSUES_FIXES_METHODS.md`.
- Teammate's strategy work — `harsh/LAI_V1_STRATEGY.md`.

**Rules followed in writing this guide:**
- Every code-level claim cites `file:line`.
- Every numeric claim was either re-probed in this session or marked
  *(unverified)*.
- Teammate's findings I independently re-probed and confirmed are merged in
  with credit; teammate's claims I found incorrect are corrected inline with
  the verification.
- Implementation order is the sequence the team should actually work in. The
  team's locked constraints (on-prem, no budget, feedback-loop learning, no
  code during analysis) are respected.
- Strategic decisions still open at the time of writing are surfaced
  explicitly — not silently chosen.

---

## Table of contents

1. What we are building (system & data overview)
2. Verdict on the current architecture
3. What the smoke test proved
4. The four structural moves
5. The five new building blocks
6. The matter-centric data model
7. The dual retrieval combiner
8. Implementation order (phases, no fixed dates)
9. The complete issue catalog (issue → fix → how)
10. The "replace the lawyer" program
11. Strategic positioning
12. Open strategic decisions
13. Verification & test suite
14. Success bar
15. Appendices: file references, code citations, sources

---

# 1. What we are building

LAI is a self-hosted German legal-AI platform for wind-energy due diligence. It
runs entirely on-premise on two NVIDIA RTX PRO 6000 Blackwell GPUs (96 GB each).
Three deployable units sit over a shared model + data layer:

| Unit | Role | Port | Status (2026-05-14 probe) |
|------|------|------|---------------------------|
| `serve_rag` (host process) | Conversational chat, document upload, contract analyzer | 18000 | Running, healthy |
| `lai-backend` / DDiQ (Docker) | Multi-document due-diligence report generator | 18001 | Running, healthy |
| `LAI-UI` (React/TS) | Web frontend (Vercel/Cloudflare today; **must move on-prem** per locked constraint) | 5173 (dev) | Running |
| `lai.pipeline` (CLI) | 6-step batch corpus build | — | Step 6 incomplete — see §1.2 |

Shared layer, all on the same `lai_network`:

- **`lai_analyzer_llm`** — Qwen3.6-27B via vLLM, GPU 0, port 8005, thinking
  mode + prefix caching
- **`lai_embedding`** — Qwen3-Embedding-8B via vLLM, GPU 1, port 8003, 4096-dim
- **In-process reranker** — Qwen3-Reranker-8B inside `serve_rag.py` on GPU
  (DDiQ calls a separate reranker container on `:8004` via HTTP — the topology
  drift here should converge on the in-process model)
- **`lai_postgres_main`** — `pgvector/pgvector:pg16` on port 5434, holds DDiQ
  tables today; will hold the corpus after the keystone migration (§4)
- **`lai_redis`** — `redis:7-alpine`, used for session state once moved
  out of in-RAM dicts
- **SQLite `pipeline_local.db`** (350 GB) — holds the legal corpus today;
  retires as a serving store after the keystone migration

## 1.1 Diagram — current runtime

```
                ┌──────────────┐
   browser ───▶ │   LAI-UI     │ ─┐  (today: Vercel/Cloudflare;
   :5173        │   (React)    │  │   to be served on-prem)
                └──────────────┘  │
                                  │ Frontend talks to TWO backends
         ┌────────────────────────┴──────────────────────┐
         │                                               │
   ┌─────▼─────────────────┐               ┌─────────────▼──────────────┐
   │  serve_rag :18000     │               │  lai-backend (DDiQ) :18001 │
   │  host process, GPU 1  │               │  Docker container          │
   │  + in-proc reranker   │               │  ThreadPoolExecutor worker │
   │  155 GB RAM corpus    │               │  HTTP to reranker :8004    │
   └──┬─────────┬──────────┘               └──┬───────────┬─────────────┘
      │         │                              │           │
      ▼         ▼                              ▼           ▼
  SQLite     vLLM embedding/analyzer       Postgres    ALKIS WFS
  corpus     :8003 / :8005                 pgvector    Nominatim (external)
  350 GB                                   :5434

  TODAY: DDiQ does NOT touch the SQLite corpus. The 672 GB knowledge base is
  invisible to the report engine. This is the central architectural problem.
```

## 1.2 The four data tiers

*(This taxonomy is taken from the teammate's strategy doc §4; every number
below was independently re-probed in this session and confirmed exact.)*

| Stage | Location | Size | Role |
|-------|----------|------|------|
| 1. raw | `LAI/data/lai-raw/` | **671 GB** | Source PDFs/HTML/JSON. Not queryable; pipeline input only. |
| 2. parsed | `LAI/data/lai-segments/` | **50 GB** | Normalised text segments — pipeline intermediate. |
| 3. chunked | `parent_chunks` + `child_chunks` tables in `pipeline_local.db` | ~250 GB | Parent/child chunks with text, metadata, FTS5 indexes. |
| 4. embedded | `child_embeddings` table in the **same** `pipeline_local.db` | rest of the 350 GB | Float32 4096-dim vectors. The runtime-queryable knowledge base. |
| 4a. shards | `LAI/data/lai-embeddings/child_embeddings/*.npz` | **77 GB** | NPZ backup of the same embeddings — dual-write safety, not a separate store. |

**The runtime-queryable store is the 350 GB SQLite. Everything else is either
source or intermediate.** This is the single most-asked clarification — keep
this table handy when explaining the system.

### 1.3 What's in the corpus (probed directly)

```
parent_chunks total                 13,807,675
  legal_text       6,370,822    general legal prose
  urteil           5,262,573    court rulings
  gesetz           1,438,319    statutes
  beschluss          592,895    court decisions
  vertrag             73,791    contracts
  vdr                 42,121    virtual data rooms (past DD)
  sonstige            15,436
  gerichtsbescheid     6,286
  fachbuch             4,909    specialist books
  dd_report              293

child_chunks total                  49,953,830
child_chunks WITH embeddings         9,462,540   ← 19%
child_chunks WITHOUT embeddings     40,491,290   ← 81%
```

**Step 6 (embedding) is ~81% incomplete.** Today the system can semantic-search
~9.5 M chunks; the remaining 40 M are reachable only via BM25 (exact-keyword)
search. **Completing the embedding pass on the missing 40 M chunks is a
mechanical background job.** The teammate estimates 2–3 GPU-days at batch 32;
that estimate is plausibly optimistic — realistic range is **3–5 GPU-days** on
one RTX 6000 *(unverified — measure on first batch and recalibrate)*. It runs
on GPU 1 alongside the embedding container; verify no contention with
`serve_rag`'s SQLite read path before kicking off.

This is the single biggest free win in the program. **Start it as Day-0
background work** (see §8.1).

### 1.4 Postgres state (probed directly)

```
ddiq_documents          5
ddiq_doc_chunks       250        chunks from 5 uploaded files
ddiq_reports            3
ddiq_contracts          0        ←
ddiq_classified_parcels 0        ←  the cadastral classification half
ddiq_contract_parcels   0        ←  of the data model was scaffolded
ddiq_parcel_cache       0        ←  but the pipeline never ran end-to-end
ddiq_geocode_cache      5
```

---

# 2. Verdict on the current architecture

**The pieces are solid. The wiring is not.** This is the central architectural
finding and it determines the response — **re-wire, do not rewrite**.

## 2.1 Solid (do not throw away)

- **The model stack** — Qwen3.6-27B / Qwen3-Embedding-8B (#1 multilingual
  MTEB, 4096-dim) / Qwen3-Reranker-8B, all locally hosted via vLLM.
- **The legal-reasoning capability** — the smoke-test report correctly parsed
  a complex OVG ruling, distinguished BImSchG §§4/6/10/15 statuses, and caught
  a real cross-document inconsistency (E-79 vs E-70 turbine type between the
  permit and the maintenance contract). Hard to fake.
- **The 672 GB German legal corpus** — real, parent/child-chunked,
  domain-classified, contextually enriched (see §1.3 breakdown).
- **The 6-step data pipeline** — idempotent, signal-handled, dual-write NPZ
  backups, schema versioning. Above typical research-code maturity.
- **The cadastral / wind-energy domain depth** — 12 federal-state ALKIS WFS
  endpoints (`ddiq_report.py:61-93`), INSPIRE CadastralParcel schema, 10H
  setback logic, 13-step parcel workflow (`cadastral_pipeline.py`).
- **The `analyzer/reconciler.py` design pattern** — "LLM never does the
  arithmetic"; deterministic classification + severity bands. Exactly the
  philosophy DDiQ needs.
- **The DDiQ database schema** (9 sensibly normalised tables).

## 2.2 Not solid — the wiring

- DDiQ cannot reach the legal corpus (`ddiq_report.py:475` — see §3.1).
- Three parallel codebases: `LAI/src/lai/`, `LAI/micro-services/`, and a
  third — **~3,200 LOC** of dead code in `LAI/src/lai/{api/main.py,
  api/pipeline.py, auth/, documents/, extraction/, generation/, infra/,
  search/{routes,repository,hybrid_search,reranker,query_analyzer}.py}` —
  imported by no live module *(line-counted directly in
  `RE_VERIFICATION.md` B2; the initial "~6,000 LOC" figure was overstated)*.
- ~400 LOC of *function-body* duplication measured directly across six
  helper categories (PDF extract 98, chunker 34, embedding 44, reranker 23,
  LLM client 145, lenient JSON parse 58 — totalling 402 LOC by `def` to next
  `def`). The earlier "~1,500–2,000 LOC" estimate was overstated. The
  *qualitative* claim — helpers duplicated 2–4× across `serve_rag.py`,
  `api.py`, `ddiq_report.py` — is correct; the true total when adding
  hybrid retrieval, system prompts, session memory, greeting routing, DB
  pool wrappers, and `<think>` stripping is ~600–900 LOC *(unmeasured for
  those last categories)*.
- Storage split across SQLite (corpus) and Postgres (DDiQ) — drift, not
  design. `cli.py:869` is literally titled "Step 6: Embeddings → pgvector"
  but writes SQLite.
- No validation layer between AI output and the user-facing report.
- No reconciler — multiple passes can produce contradictory numbers that all
  appear in the document.
- No retrieval router — `rag_context()` is hard-wired to one source.
- No connector abstraction — ALKIS and Nominatim are bolted into the
  2,463-line `ddiq_report.py` god-file.
- No authentication or tenant isolation — every user sees every report.
- One copy of each AI model — a restart kills chat, contract analyzer, and
  DDiQ together.
- An English keyword gate (`EXTERNAL_LAW_REFS` in `serve_rag.py:1017-1028`)
  silently skips the corpus for English-language questions. *(Caught by the
  teammate; re-confirmed by reading the code.)*

**Verdict:** competent in pieces, incoherent as a system. Five new components
+ four structural moves (§4–5).

---

# 3. What the smoke test proved

`LAI/docs/smoke_test_report.pdf` — Windpark Lamstedt, 4 input PDFs, generated
2026-04-29. Six failures verified by re-reading the PDF and tracing each to
code.

## 3.1 The six failures (with root cause)

| # | Failure | Evidence | Verified root cause |
|---|---------|----------|---------------------|
| A | **"Manual review required (findings extraction failed)"** printed in the client deliverable. Action Items chapter empty. | PDF page 14 | `generate_findings()` makes one batched `llm_json()` call (`ddiq_report.py:1543-1648`); any malformed JSON → bottom `except` → placeholder. The 27B in thinking-mode at `max_tokens=4096` very likely blew the budget or returned invalid JSON. |
| B | All six turbines geocoded to **Bremen Überseestadt** — 65 km from the actual Lamstedt site. | PDF map p.13; coords 53.094 / 8.785 (Bremen) vs Lamstedt 53.622796 / 9.147855 *(web-verified)* | `geocode_address()` (`ddiq_report.py:571`) passes the LLM-emitted *paragraph* verbatim to Nominatim with `limit=1` and no plausibility check. |
| C | Cadastral parcels wrong / mislabeled. Table shows 3 parcels; body references 9. Synthetic estimated polygons render with `source="ALKIS WFS"`. | PDF p.6 vs p.12 | Knock-on from B: ALKIS WFS queried at wrong coords → empty → fall through to `make_parcel_polygon()` (`ddiq_report.py:1481-1486`). Parcel area is `round(2.0 + (hash(pnum) % 20) / 10, 1)` — a hash of the parcel number. |
| D | Four conflicting turbine counts in one report: 7/3/10 in text, 11 in capacity math, "10 von 11" in title, 6 in table. | PDF pp.2-3, p.12 | Four independent derivations, never reconciled. `parse_wea_count` (`:838-839`) is `re.search(r"(\d+)", value)` — grabs the first integer from a value the prompt deliberately fills with multiple numbers. |
| E | Action Items near-empty. | PDF p.14 | Direct consequence of A. |
| F | WEA "Address" column = full multi-sentence paragraph repeated 6× across 6 turbines. | PDF p.13 | LLM was asked for `{address: "municipality, state"}` and returned the paragraph. Displayed untouched and also fed to the geocoder — the trigger for B. |

Plus, from the same re-read:

- **Denglisch** — English category labels, German content, mixed mid-sentence.
- **Defensive "no information" paragraphs in 25 of 37 sections.** Not a bug
  per se — by design, DDiQ only reads uploaded PDFs and cannot reach the
  672 GB corpus. The fix is architectural (§4.1, §5.2).

## 3.2 The compounding-failure math

One full report makes **~45 LLM calls** (37 section questions + 1 metadata +
1 WEA + 1 infra + 1 cadastral contract + 1 findings + 1 timeline + 1 cross-doc
+ 1 Rückbau + 1 Grundbuch). Section questions degrade gracefully (per-row
fallback). **Eight of the remaining passes are single points of failure for an
entire report chapter.**

If `p` = per-pass probability of returning usable JSON within the single retry,
then `p^8 ≈ 0.78` at a generous `p = 0.97` — **roughly 22% of reports lose a
whole chapter to LLM-JSON fragility alone**, before counting
geocoding/ALKIS/network failures.

*(p is illustrative, not measured. The structural claim — 8 SPOFs, sections
graceful — is verified in `_generate_report_core` at `ddiq_report.py:1884` and
following.)*

**The reason this matters for implementation:** schema-enforced LLM output via
vLLM guided decoding (§5.3, §9.A1) moves `p` close to 1.0 and structurally
eliminates the chapter-loss class. It's the single highest-leverage engine
change.

## 3.3 What the report did well (counterweight)

So we don't lose sight: legal reasoning on the documents that *were* in
context was strong. It correctly parsed the Änderungsgenehmigung, the deferred
Bestandskraft, the OVG ruling cancelling L6/L7/L9 and the resulting
Rückbauanspruch, the noise/shadow limits (60/45 dB(A), 30 min/day, 30 h/yr),
and the recurring-inspection rhythms. **The reasoning quality is real; the
plumbing failed it.**

---

# 4. The four structural moves

## 4.1 Move 1 — Unify storage (the keystone)

**Migrate the 9.46 M-embedding corpus from SQLite `pipeline_local.db` into
Postgres `pgvector` as `halfvec(4096)` + HNSW.** This was the README's
documented intent (`cli.py:869` is literally titled "Step 6: Embeddings →
pgvector"); it was never finished because `pgvector`'s default `vector` type
caps HNSW at 2000 dims. The **`halfvec` type supports HNSW to 4096 dims** —
this is the missing piece that unblocks the migration.

**What this unlocks (four problems collapse into one project):**

1. DDiQ becomes able to ground answers in the legal corpus — a plain SQL join
   between corpus chunks and `ddiq_doc_chunks`.
2. The 155 GB RAM-load + cold-restart model goes away.
3. Continuous corpus growth via online upserts (today: cold restart only).
4. Future horizontal scaling becomes possible (today: tied to one box).

**Migration scope:**

- ~9.46 M × 4096 fp32 → `halfvec(4096)` halves storage to ~77 GB.
- HNSW build on a corpus this size is hours-to-days of compute, one-time.
- Step 6 must either finish first (3-5 GPU-days more) or stream-forward into
  pgvector as new embeddings are produced. **Recommended:** migrate the
  existing 9.46 M now and stream forward — Step 6 completion runs as Day-0
  background work anyway (§8.1).

## 4.2 Move 2 — Delete dead code + extract `lai.common`

**Delete** the ~3,200 LOC dead stack (`api/main.py`, `api/pipeline.py`,
`auth/`, `documents/`, `extraction/`, `generation/`, `infra/`,
`search/{routes,repository,hybrid_search,reranker,query_analyzer}.py`) after
final grep-confirmation that nothing live reaches them.

**Extract `lai.common`** with one of each duplicated helper:

- `PdfExtractor` (PyMuPDF + Tesseract with a quality gate)
- `Chunker`
- `EmbeddingClient`
- `RerankerClient`
- `LlmClient` — must include: `<think>`-trace stripping, schema-enforced
  output (guided decoding), `tenacity` retries, brace-balanced JSON salvage
- `JsonSalvage`
- `Auth` — JWT issue/validate, bcrypt; port from the dead `auth/` package
  (the validation logic itself is correct — it was just wired to nothing)

**Salvageable patterns from the dead stack:** the JWT validator in
`auth/jwt.py` and the citation-verifier design from `generation/` get *ported*
into `lai.common`. The dead modules themselves are deleted.

## 4.3 Move 3 — Add auth + tenant isolation (the GDPR blocker)

**Day-0 priority.** Today neither backend has authentication; DDiQ tables have
no `user_id` columns; every user sees every report.

- `POST /auth/login` issues JWTs (bcrypt-hashed passwords). `get_current_user`
  dependency validates on every route. Shared `AUTH_SECRET`.
- Migration: `user_id NOT NULL` added to `ddiq_documents`, `ddiq_doc_chunks`,
  `ddiq_reports`, `sessions`. Populate from JWT on insert. Every
  `SELECT/UPDATE/DELETE` adds `WHERE user_id = current_user.id`.
- Frontend: replace `AuthContext.tsx:55-82` (which mints an unsigned base64
  token and never sends it) with real backend auth; interceptor attaches
  `Authorization: Bearer` to every fetch in `ragApi.ts` and `ddiqApi.ts`.
- CORS allow-list driven from env; in production only the on-prem UI host.
- **Rotate the HF token** in `Docker/inference_engine/.env:11` and move it to
  a secret store (`docker secret` or `chmod 600` outside the repo).

This is non-negotiable. Without it, LAI technically violates GDPR/BDSG and
cannot onboard a second customer.

## 4.4 Move 4 — Move the frontend on-prem

Per the locked on-prem constraint: drop the Vercel and Cloudflare Worker
configs (`wrangler.json`, the Vercel `vercel.json`); build the React app and
serve `dist/` from Nginx or Caddy on the same box behind the auth layer.
`VITE_BACKEND_URL` points to the local backend.

This **strengthens** the pitch: *"your contracts never leave the building"* is
a genuine selling point for German legal clients. It is *not* a moat —
Legartis already ships sovereign legal AI in Switzerland (§11) — but it is a
real credential.

---

# 5. The five new building blocks

Components that do not exist today. These are not bug fixes — they are new
structural pieces every fix in §9 keeps referencing.

## 5.1 `lai.common`

(Covered in §4.2.) One shared library. Every fix lands once instead of three
times.

## 5.2 `lai.retrieval` — the retrieval router with citation handles

Replaces the hard-wired `rag_context()` (`ddiq_report.py:525`) and
`search/eval.py`'s eval-harness-doing-double-duty.

- Per question, decides which sources to query: uploaded documents, legal
  corpus, public registries.
- Returns ranked chunks **with provenance tags** — every chunk carries
  `source_kind ∈ {corpus, matter, registry, estimated, unverified}`.
- **Citation handles** *(from teammate's strategy doc §6.3 — adopted):* every
  chunk gets a stable id rendered to the user as `[C-1]…[C-n]` for corpus
  chunks and `[M-1]…[M-n]` for matter (uploaded) chunks. The LLM is required
  to cite these in every answer. The validator (§5.5) verifies the handle
  resolves to a real retrieved chunk.

The dual-collection design (corpus + matter) and the RRF + rerank pipeline are
detailed in §7.

## 5.3 `lai.connectors` — the public-registry plugin layer

Per the locked no-budget constraint, **public/free sources only**.

- `Connector` ABC with `fetch`, `cache_key`, `parse`, `source_tag` methods.
- Refactor existing ALKIS WFS (12 federal-state endpoints,
  `ddiq_report.py:61-93`) and Nominatim into the package as the first two
  implementations.
- Add **Marktstammdatenregister (MaStR)** — free public API; confirms turbine
  registration, commissioning dates, capacity. Directly addresses the
  turbine-count + EEG-status questions.
- Add **Handelsregister** — publicly accessible; project-company verification
  (fixes the missing HRB number flagged in the smoke test).
- **Grundbuch** remains a *"request this document from the client"* action
  item — no openly API-accessible source, paid options out of scope.

## 5.4 Facts ledger + deterministic reconciler

Borrows the design pattern from `lai/analyzer/reconciler.py` (the same
codebase already has the right answer; DDiQ just hasn't adopted it).

- One canonical `ProjectFacts` Pydantic object every extraction pass reads
  from and writes into.
- Deterministic in-code reconciler resolves the four turbine-count derivations
  (§3.1 D) into one canonical value; the contradiction becomes *one* finding,
  not four printed numbers.
- Replace the broken `parse_wea_count` (`:838-839`) with a multi-group parser
  that handles `errichtet + genehmigt + geplant` correctly.

## 5.5 Validation / guardrail layer

A new pipeline stage between extraction and rendering. Single owner of:

- **Location plausibility** — Nominatim hits must fall in the named
  Bundesland/Landkreis bounding box AND clear an importance threshold;
  low-confidence locations marked `unverified` and not used for ALKIS or map
  pins.
- **Schema compliance** — every LLM-derived structure validated against its
  Pydantic schema before insertion.
- **Citation handle resolution** — every `[C-n]/[M-n]` must map to a chunk in
  the retrieval result set; unresolved citations are stripped and the sentence
  is tagged `(unverified)`.
- **Source-tag honesty** — synthetic `make_parcel_polygon()` output cannot be
  rendered as `source="ALKIS WFS"`.
- **Single-language enforcement** — one language per report; mid-sentence
  switches detected and re-prompted.
- **Hedge-language strip** — removes "consult a Fachanwalt", "as an AI…",
  reflexive caveats. (See §10 — this is the cosmetic half of the
  "replace-the-lawyer" requirement.)
- **Jurisdiction sanity** — 10H setback warnings only when
  `Matter.bundesland == 'BY'` (and `'HE'`, where applicable); a 10H
  reference in a Niedersachsen report is filtered or flagged.

The legacy `inference_engine`'s `MAX_DISCLAIMERS` / `REMOVE_AI_REFERENCES`
controls were the right idea in the wrong place (dead code now). This layer is
where they belong.

---

# 6. The matter-centric data model

*(Adopted from the teammate's strategy doc §6.2. Schema designed for one
unified Postgres; aligns with the keystone migration in §4.1.)*

```
matters
├── id (uuid, PK)
├── name (text)
├── bundesland (text, 2-char ISO)
├── project_type (text)              -- 'wind' | 'solar' | etc
├── created_by (user_id, FK users)
├── created_at, updated_at

matter_documents
├── matter_id (FK matters)
├── document_id (FK documents)
├── role (text)                       -- 'pachtvertrag' | 'permit' | 'gutachten' | ...
└── (matter_id, document_id) UNIQUE

documents
├── id (uuid, PK)
├── user_id (FK users)
├── filename, mime, size_bytes
├── full_text (text)
├── chunk_count (int)
├── status (text)                     -- 'parsed' | 'embedded' | 'indexed' | 'failed'
└── created_at

conversations
├── id (uuid, PK)
├── matter_id (FK matters)
├── user_id (FK users)
├── created_at, updated_at
└── pinned_facts (jsonb)              -- the existing conversational memory

messages
├── id (uuid, PK)
├── conversation_id (FK conversations)
├── role (text)                       -- 'user' | 'assistant'
├── content (text)
├── citations (jsonb)                 -- the [C-n]/[M-n] resolutions
├── created_at

audit_events                          -- immutable; AI Act compliant
├── id (uuid, PK)
├── user_id, matter_id (FKs)
├── ts (timestamptz)
├── action (text)                     -- 'chat' | 'upload' | 'report.generate' | ...
├── prompt_hash (text)
├── retrieved_cite_ids (jsonb)
├── response_hash (text)
├── model_version (text)
└── ...
```

This consolidates the in-RAM dicts in `api.py:121` (lost on restart), the
SQLite `sessions.db` (serve_rag), and the per-matter document scoping. One
data model, one Postgres, one source of truth.

**Backward compatibility:** existing `sessions` and `ddiq_*` rows migrate into
the new schema with `user_id` populated from current ownership (or marked
`legacy`). No data is lost; the smoke-test report continues to be retrievable.

---

# 7. The dual retrieval combiner

*(Adopted and verified from the teammate's strategy doc §6.3.)*

This is the architectural piece that turns LAI from "chat with PDF" into
"legal AI grounded in the corpus".

```
            ┌──────────────────────────┐
            │ User question (any lang) │
            │ + conversation context   │
            │ + matter_id              │
            └─────────────┬────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Qwen3-Embedding-8B       │  multilingual:
            └─────────────┬────────────┘  EN query → DE docs OK
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   ┌────────────────┐          ┌─────────────────┐
   │ CorpusCollection│          │ MatterCollection│
   │ 9.5 M embedded  │          │ matter_id-scoped│
   │ (read-only)     │          │ (read-write)    │
   │ Dense top-50    │          │ Dense top-50    │
   │   + BM25 top-50 │          │   + BM25 top-50 │
   └────────┬────────┘          └────────┬────────┘
            │                            │
            └─────────────┬──────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Reciprocal Rank Fusion   │
            │ (combine 200 candidates) │
            └─────────────┬────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Qwen3-Reranker-8B        │  cross-encoder
            │ (in-process on GPU)      │  → top-K
            └─────────────┬────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Tag each chunk:          │
            │  source_kind ∈ {corpus,  │
            │    matter, registry,     │
            │    estimated, unverified}│
            │  cite_id = [C-n] / [M-n] │
            └─────────────┬────────────┘
                          ▼
                 →  to LLM prompt + validator (§5.5)
```

**Important consequence (from teammate's catch at `serve_rag.py:1017-1028`):**
the existing `EXTERNAL_LAW_REFS` keyword gate silently skips the corpus for
English queries. **Remove it.** The router fires *both* collections in
parallel on every chat turn where a matter is selected. The cost is one
additional embedding lookup plus ~50 BM25 hits — negligible.

---

# 8. Implementation order

Phases describe sequence and dependencies, not weeks. The team's own
estimation of effort goes here; no fixed-date commitments.

## 8.0 Phase compatibility — what must run before what

Hard dependencies (must complete first):

| Item | Must come after | Why |
|------|-----------------|-----|
| Auth + tenant isolation rollout (S1, S2) | — (Day 0) | GDPR blocker. Until done, cannot onboard a second customer. Internal-only until then. |
| Removing the fake frontend auth (S3) | Backend auth deployed (S1) | Otherwise users are locked out during the cutover. |
| `lai.common` extraction (Phase 1a) | Step 6 background job kickoff (so it's not blocked on Day 0) | Phase 1a is a pure refactor; no external dependencies. |
| Phase 1b Track A reliability fixes | `lai.common` available (Phase 1a) | Track A wants the schema-enforced LLM client + JSON salvage from `lai.common` so each fix lands once. |
| Phase 1b Track B keystone | `lai_postgres_main` healthy *(already true since 2026-05-14)* | Cannot migrate into an unavailable DB. |
| Step 6 background job | SQLite WAL mode + `serve_rag` opens read-only | Otherwise the embedding writer contends with the serve_rag reader. Pause embedding during demo windows. |
| Corpus migration to pgvector (Track B) | Step 6 either complete OR migration designed for streaming inserts | Decided in §12.4. Recommended: migrate 9.46 M existing, stream forward. |
| Phase 2A statutory grounding | Track B keystone complete | The retrieval router is the insertion point; cannot ground in a corpus DDiQ can't query. |
| Phase 2B connector additions (MaStR, Handelsregister) | `lai.connectors` refactor of existing ALKIS + Nominatim | New connectors must land in the package, not the god-file. |
| Phase 2C provenance enforcement | Phase 1b Track A validation layer | Provenance is a function on the validator. |
| Dropping the formal disclaimer footer (§10.2) | Phases 1–3 exit criteria met AND counsel clearance on RDG (§10.3) | The footer is the liability shield, not filler. |

Soft compatibility (can overlap; ordering preference for risk reduction):

- Phase 1a (consolidation) is safe to run **alongside** Phase 0 auth — they
  touch different files.
- Phase 3 (feedback capture) can begin during Phase 1b — the `POST /feedback`
  endpoint + `lai_feedback` writes are small and independent.
- The frontend on-prem move (M4 of §4.4) can run anytime after auth is
  deployed; it does not block anything.

## 8.1 Phase 0 — Day-0 prerequisites

Run **before** anything else. Two of these can start in parallel; the third
runs in the background for the duration of Phases 1–2.

| Task | Why |
|------|-----|
| **Auth + tenant isolation** (§4.3) | GDPR blocker. Cannot onboard a second customer. **Highest priority — Day 0.** |
| **Pick one deployment model** (all-Docker vs host-process) and retire the other | The Docker/host topology drift was the cause of the original audit-time outage. Recurrence risk. |
| **Kick off background embedding-completion job** | 40.5 M chunks still need embedding. Runs on GPU 1 alongside the embedding container. The teammate's command pattern (§12 of `LAI_V1_STRATEGY.md`) is the starting point — verify exact flags by reading `src/lai/pipeline/embed.py` first. Monitor with the SQL snippet in §13.2. Realistic timeline: 3–5 GPU-days, in the background. |

The runtime outage finding from the initial audit is **already resolved** —
the full LAI runtime stack came back up during the audit window. The Phase-0
items above are what remains.

## 8.2 Phase 1a — Consolidation

Cheap. Runs before Phase 1b so every fix lands once.

- Delete the ~3,200 LOC dead stack (§4.2) after final grep-confirmation.
- Extract `lai.common` (§5.1) — shared helpers, schema-enforced LLM client,
  retries, JSON salvage.
- Salvage JWT validation and citation-verifier patterns from the dead modules
  into `lai.common.auth` and the validator (§5.5).
- No behaviour change. Pure refactor.

## 8.3 Phase 1b — Two parallel tracks

Track A touches `ddiq_report.py` logic; Track B touches infra and a package
refactor. Different files; different streams; can run concurrently.

### Track A — DDiQ engine reliability

Everything in §9.1 + §9.5 + the hedge-language strip from §10. Exit criterion:
**the Lamstedt smoke test passes the eight-point success bar in §14**.

### Track B — The keystone (corpus → pgvector + `lai.retrieval`)

- Migrate 9.46 M existing embeddings into `pgvector` `halfvec(4096)` + HNSW
  (§4.1).
- Stream new embeddings from Step 6's continued run into `pgvector` via
  online upserts.
- Build the shared `lai.retrieval` package (§5.2) — dense + BM25 + RRF +
  rerank over the unified store; both serve_rag and DDiQ import it.
- Implement the dual retrieval combiner (§7).
- Optional bridge during the migration: a `/retrieve` endpoint on `serve_rag`
  that DDiQ calls over HTTP — retired once the migration lands.

Exit criterion: DDiQ can query the corpus with a plain SQL join; cold restarts
no longer reload 155 GB into RAM.

## 8.4 Phase 2 — Beyond the data room

Gated on Track B.

- **2A — Statutory grounding.** Every section pulls relevant statute and case
  law from the corpus via the retrieval router. A gap becomes *"§35(5) BauGB
  Rückbaubürgschaft — request from client"* with the statute cited, not a
  defensive paragraph.
- **2B — Registry connectors.** MaStR, Handelsregister, expanded ALKIS via
  the `lai.connectors` package (§5.3).
- **2C — Provenance tagging.** Every fact in the report carries its
  `source_kind` typed enum (§5.5); rendering enforces honest labels.

## 8.5 Phase 3 — Feedback loop

Can start as early as Phase 1b.

- `POST /feedback` endpoint capturing `(original, corrected, reason)` keyed
  by `(conversation_id, message_id)` — the `lai_feedback` table already
  exists in `pipeline_local.db` but is unused.
- Correction memory stored in pgvector; on each new extraction pass, retrieve
  the most similar past corrections and inject them as few-shot guidance.
- **No GPU retraining.** The model improves from inference-time context.
- Doubles as the eval harness the project lacks today.

## 8.6 The DDiQ vs chat-first decision

**Open strategic question** — surfaced explicitly in §12. The original session
locked *"DDiQ engine is the priority surface"*. The teammate's strategy doc
proposes *"chat-first; render the DD report from conversation history in
v1.1"*. **Both are defensible.** The implementation order above is largely
neutral — Phase 1b Track A fixes DDiQ engine reliability either way; the chat
path benefits from the same retrieval router and validation layer. The
decision affects **which exit criterion to demo against** (a passing smoke
test vs a polished chat) and **whether `/ddiq/report/generate` is wired in
the UI for v1 or hidden** until v1.1.

---

# 9. The complete issue catalog

Each entry: **what** → **fix** → **how**. Severities are honest. File:line
citations throughout.

## 9.1 Report output quality

| Issue | Fix | How |
|---|---|---|
| **A1.** `generate_findings()` prints "Manual review required" when batch JSON parse fails (`ddiq_report.py:1543-1648`). | Per-finding iteration + schema-enforced output | Replace the single batched `llm_json()` with a loop; one small LLM call per flagged row. Use vLLM **guided decoding** with a Pydantic JSON schema. Strip `<think>` reasoning traces before parse. Per-call retry. Partial success keeps the chapter alive. |
| **A2.** Turbines geocoded to Bremen (`ddiq_report.py:571`). | Location-normalization pass + plausibility gate | Pre-step extracts structured `{gemeinde, gemarkung, landkreis, bundesland}` from the LLM, never a paragraph. Nominatim hits must fall in the named Landkreis bbox AND clear an `importance` threshold; below-threshold locations marked `unverified` and not used for ALKIS or map pins. Add `cached_at` TTL to `ddiq_geocode_cache`. |
| **A3.** Cadastral parcels mislabeled (synthetic shown as ALKIS). | Provenance enum + honest rendering | Every fact carries `source_kind ∈ {uploaded_doc, legal_corpus, registry, estimated, unverified}`. Synthetic polygons cannot carry an `ALKIS WFS` tag. Hash-derived `area` becomes `None` when ALKIS didn't return one. Render estimated geometry visually distinct. |
| **A4.** Four conflicting turbine counts (`ddiq_report.py:838-839` + scattered other places). | Deterministic reconciler | In-code stage after all extraction passes. Multi-group parser replaces the first-int regex. Forces `len(weas)` back into the overview row. Emits one canonical count. Contradictions become one finding, not four numbers. Ports `analyzer/reconciler.py` design. |
| **A5.** 25 of 37 sections "no information". | Statutory grounding + structured "missing" state | Retrieval router pulls the relevant statute from the corpus per section, so a gap becomes *"§35(5) BauGB requires X — absent from the data room, request from client"*. "Missing" is a typed state rendered as a red-flag icon, not a paragraph. |
| **A6.** Same paragraph repeated 6× in WEA table. | Facts ledger | Canonical `ProjectFacts` object; identical values referenced, not regenerated per row. Structural fix. |
| **A7.** "Address" column = paragraph. | Structured location model | Separate the displayed string from the geocoded fields. |
| **A8.** Denglisch — mixed languages in body. | Single-language enforcement (validator) | One language per report, configurable per matter. Mid-sentence switches detected and re-prompted. |
| **A9.** Reflexive "consult a Fachanwalt" filler. | Output cleanup pass + tighter prompts | Strip disclaimer-class phrases. System prompts say "decisive Fachanwalt; do not refer to other lawyers." The formal liability footer is **not** removed here — see §10. |
| **A10.** WEA specs (hub/rotor/power) often `null`. | Dedicated specs prompt + Docling table mode | New focused prompt targets the numeric spec table; use Docling `TableFormerMode.ACCURATE` for datasheets. |

## 9.2 The corpus silo (the keystone)

| Issue | Fix | How |
|---|---|---|
| **B1.** `search_doc_chunks` (`ddiq_report.py:475`) only sees uploaded docs. | Unified retrieval over pgvector | Migration (§4.1). Both serve_rag and DDiQ import `lai.retrieval`. |
| **B2.** Storage drift (SQLite corpus / Postgres DDiQ). | One Postgres | Above. |
| **B3.** Step 6 ~81% incomplete (40.5 M chunks pending). | Resume + monitor + decide migration policy | `resume_step6.sh` is the existing runner. Recommended: migrate the 9.46 M now and stream forward. |
| **B4.** `rag_context()` hardwired to one source. | Retrieval router (§5.2, §7) | New module; replaces the one-liner. |
| **B5.** `EXTERNAL_LAW_REFS` keyword gate skips corpus for English questions in contract-loaded sessions (`serve_rag.py:503` defines the regex; `:1018-1028` applies it). *(Teammate's catch — confirmed by direct read.)* The gate is **intentional** (code comment at `:1023-1024` documents the design: prevents conflating corpus contract chunks with the uploaded contract). It fires **only when an uploaded contract is in the session** (`use_contract == True`); when no contract is uploaded, `needs_rag` decides separately. The real downside is that English questions like *"cross-check this with your database"* match no German legal-keyword tokens and the corpus is silently skipped. | Replace the gate, don't blindly remove it | Move the routing decision into `lai.retrieval` as a typed decision: "uploaded-only" / "uploaded + corpus" / "corpus only", driven by intent classification (a small model call or a smarter heuristic — multilingual keyword set + question-type detection). Keep the *intent* of the original gate (don't pollute upload-specific queries with corpus contract chunks) but support English and natural-language cross-check phrasing. |
| **B6.** No external public data is queried. | `lai.connectors` package | §5.3. MaStR, Handelsregister, expanded ALKIS — all free. |

## 9.3 Codebase fragmentation

| Issue | Fix | How |
|---|---|---|
| **C1.** ~3,200 LOC dead code imported by nothing live. | Delete | §4.2. After grep-confirmation. Salvage JWT and citation-verifier patterns into `lai.common`. |
| **C2.** ~400 LOC of function-body duplication measured across 6 helper categories (~600–900 LOC estimated total when all duplicated logic is included; see §2.2). | `lai.common` shared library | §4.2 / §5.1. |
| **C3.** `ddiq_report.py` is a 2,463-line god-file. | Decompose into modules | Split (no logic change): `db.py`, `models.py`, `extractors/`, `routes.py`, `pipeline.py`, `connectors/`. |
| **C4.** DDiQ has no reconciler; `analyzer/reconciler.py` has the pattern. | Adopt the pattern | §5.4. |

## 9.4 Security & tenant isolation

| Issue | Fix | How |
|---|---|---|
| **S1.** No auth on either backend. | JWT + `Depends` on every route | §4.3. |
| **S2.** No `user_id` on DDiQ tables → data globally visible. **GDPR**. | Migration + filter every query | §4.3. |
| **S3.** Frontend `AuthContext` is fake (unsigned base64 token at `AuthContext.tsx:55-82` and `utils/jwt.ts:26-36`). | Real backend auth | §4.3. Delete the browser-side base64 helper. |
| **S4.** `CORS allow_origins=["*"]`. | Env-driven allow-list | §4.3. |
| **S5.** Live HF token in `Docker/inference_engine/.env:11`. | Rotate + secret store | §4.3. |
| **S6.** Hardcoded default credentials in source (`core/config.py:38, 84, 273`). | Fail closed | Remove defaults. The microservice compose already does this right with `DB_PASSWORD:?Set DB_PASSWORD in .env`. |

## 9.5 Engine fault tolerance

| Issue | Fix | How |
|---|---|---|
| **E1.** ~22% reports lose a whole chapter to LLM-JSON fragility. | Schema-enforced output + typed empty fallback per critical pass | The guided-decoding change in A1 applied system-wide. Each of the 8 SPOF passes gets a typed empty fallback so failure yields an empty section with a logged warning, not a thrown exception. Drops the rate near zero. |
| **E2.** `_parse_alkis_feature` inverted control flow at `ddiq_report.py:705, 712`. Severity Medium — only manifests when multiple candidate keys are simultaneously present. | Move `break` to the success path | One-line fix per loop. |
| **E3.** `llm_json` double-failure uncaught (`:516-523`). | Catch + typed empty | Wrap the retry's `json.loads`; return `{}` / `[]`. |
| **E4.** `request_fingerprint` index is plain INDEX, not UNIQUE → TOCTOU race (`:140`). | Make UNIQUE + atomic claim | `CREATE UNIQUE INDEX … WHERE request_fingerprint IS NOT NULL`; `INSERT … ON CONFLICT DO NOTHING RETURNING id`. |
| **E5.** Sync `/report/generate` sets fingerprint after pipeline completes (`:2199-2206`) — concurrent dedup misses. | Set at row creation | Mirror the async path. |
| **E6.** Sync path has no try/except → mid-pipeline crash leaves row at default `status='done'` (`:133`). | Wrap; mark `failed` on exception. | Standard try/except. |
| **E7.** Aux-table writes (`:2138-2170`) have no `ON CONFLICT` → duplicates on retry. The comment at `:2133` admits it. | Upsert keyed on `report_id` | Each aux insert becomes upsert. |
| **E8.** `ddiq_geocode_cache` and `ddiq_parcel_cache` poison forever (no TTL). | TTL + bust-on-regenerate | Use existing `cached_at` columns; reject stale; delete cache rows the previous run wrote on regeneration. |
| **E9.** Evidence rollup silently drops out-of-range LLM indices (`:1620`). | Detect + downgrade confidence | Log; reduce the finding's confidence; do not produce evidence-less findings silently. |
| **E10.** `_evidence` on `__dict__` not serialized by `.dict()` (`:1028, 1793`). | Promote to real Pydantic field | `evidence: list[Evidence] = []` on the row model. |
| **E11.** OCR triggers on `len(text) < 50` only — no quality gate. | Alphabetic-ratio + mojibake pattern checks | Augment the trigger. |
| **E12.** No retries on any external HTTP call. | `tenacity` retries + backoff | Already a declared dep. Wrap embed/LLM/ALKIS/Nominatim. ALKIS gets retry-on-HTTP-530. Step 6 retries the batch instead of `break`. |
| **E13.** DDiQ async reports run in an in-process `ThreadPoolExecutor(max_workers=2)` (`ddiq_report.py:1736`) — only 2 concurrent reports, and **a service restart abandons running reports** (the comment at `:2432` admits it). | Move report jobs to an out-of-process queue | Redis-backed Celery worker (the dependencies `redis` 6.4.0 and `celery[redis]` 5.6.2 are already declared in `pyproject.toml` but the worker is not deployed). `reap_orphans()` on startup remains as a safety net. |
| **E14.** Realistic concurrent-user ceiling is single-digit users (the single Qwen3.6-27B + the in-process reranker + the 155 GB shared NumPy corpus matrix all serialize). | LLM-serving redundancy + smaller fast model for chat | Add a second 27B replica when budget allows; in the meantime, route chat to a faster model (Qwen2.5-7B already in the cluster) and reserve the 27B for `/analyze-contract` and DDiQ report generation. Tracked here as a known scaling ceiling, not a Phase 1 fix. |

## 9.6 Operational layer

| Issue | Fix | How |
|---|---|---|
| **O1.** Zero automated tests anywhere. | Start with pure functions + the test battery in §13. | `german_splitter`, `text_cleaner`, new reconciler, multi-group `parse_wea_count` replacement, validation gates, `JsonSalvage`. Frontend Vitest for the API clients. Integration tests once the keystone lands. |
| **O2.** Monitoring stack configured but not deployed. | Deploy + correct targets | Bring `Docker/monitoring/` up; fix scrape targets to actual container names; expose `/metrics` on both backends. |
| **O3.** No streaming on `/query` (`sse-starlette` declared but unused). | SSE via `sse-starlette` | `StreamingResponse` over async generator; frontend uses `EventSource`. |
| **O4.** `:latest` image tags + unpinned `>=` deps in the deployed microservice. | Pin everything | Digest-pin Docker images; lockfile for the microservice (`uv pip compile`). |
| **O5.** Frontend on Vercel/Cloudflare → conflicts with on-prem mandate. | Serve on-prem | §4.4. |

## 9.7 Corpus & data quality

| Issue | Fix | How |
|---|---|---|
| **D1.** Step 6 ~81% incomplete. | Resume + stream into pgvector | §8.1 background job. |
| **D2.** Top-level `data_processing/` is dead legacy code (not git-tracked, imported by nothing, old schema). | Archive or delete | After confirming nothing reads from it. |
| **D3.** 15.8% fabricated citations in synthetic training data (`audit_results.json`). | Verification loop inside generation | `generate.py` regex-extracts citations, confirms in source chunk, reject + regenerate. `audit_training_data.py` becomes a CI gate. Fine-tuning can resume on clean data. |
| **D4.** No feedback capture today. | `POST /feedback` + correction memory | §8.5. `lai_feedback` table already exists unused. |
| **D5.** SQLite FTS5 BM25 index over `child_chunks` is built once and goes stale on new rows (`eval.py:139` only builds if absent). | Moot after keystone migration — Postgres FTS maintains `tsvector` per row automatically. | In the interim (while corpus is still on SQLite), drop+rebuild the FTS5 table after each embedding-batch landing if BM25 quality matters for that window. |

## 9.8 Topology and configuration drift (the small inconsistencies)

These are not failures but real drift between docs, configs, and reality.
Each is a small cleanup that prevents future confusion.

| Issue | Source / evidence | Fix |
|-------|-------------------|-----|
| **T1.** `lai_neo4j` (neo4j:5.15-community) is **running** on `:7474` / `:7687` on the `lai_network` but appears in **no tracked compose file** and no live code imports a Neo4j driver. | Live `docker ps`; no `neo4j-driver` in `pyproject.toml` or `requirements.txt`. | Identify owner/purpose; if vestigial, remove. Otherwise document it in `LAI/docker-compose.yml`. |
| **T2.** Postgres port mismatch — runtime compose declares `:5434`; the host-process Postgres (legacy, currently empty `lai_db`) listens on `:5435`; DDiQ uses internal DNS `lai_postgres_main:5432`. Two Postgres instances coexist. | `docker ps`; `ss -tlnp \| grep 5435`. | After the keystone migration consolidates writes to `lai_postgres_main`, retire the host-process Postgres entirely. |
| **T3.** Embedding-dimension drift in legacy configs. Live system uses **4096-dim Qwen3-Embedding-8B**; legacy `Docker/embedding_server/.env` and `Docker/services/.env` still reference **1024-dim BAAI/bge-m3**; the README's "Docker-free" example uses `struct.unpack('1024f', ...)` — stale. | TECH_STACK §14 item 5. | Delete the legacy configs (they're for the dead `inference_engine`); fix the README snippet. |
| **T4.** Reranker has **three conflicting definitions** in the repo: (a) in-process `Qwen/Qwen3-Reranker-8B` in `serve_rag.py:852`; (b) vLLM-served `cross-encoder/ms-marco-MiniLM-L-12-v2` in `Docker/reranker/docker-compose.yml`; (c) the live running container is `lai-test-reranker` = `ghcr.io/huggingface/text-embeddings-inference:cpu-1.8`. | TECH_STACK §14 item 4. | Converge on (a) — the in-process Qwen3-Reranker-8B is what `serve_rag` actually uses; DDiQ's HTTP `RERANKER_URL` should target `host.docker.internal:8004` exposed by serve_rag, not a separate container. Delete `Docker/reranker/`. |
| **T5.** `celery[redis]` 5.6.2 is a declared dep in `pyproject.toml` but **no Celery worker or beat scheduler is running**. DDiQ uses `ThreadPoolExecutor` (see E13) instead. | TECH_STACK §14 item 8. | Either deploy Celery + retire the executor (the E13 fix), or remove the unused dep. |
| **T6.** `vllm` 0.19.0 is a declared Python dep of the core package, but models are served by the separate `vllm/vllm-openai:latest` Docker image — the in-process `vllm` import is not used by `serve_rag.py`. | TECH_STACK §14 item 9. | Remove the in-process `vllm` Python dep from `pyproject.toml` unless used in scripts; the Docker image is the authoritative serving path. |
| **T7.** `.env.example` drift — `LAI-UI/.env.example` advertises `VITE_JWT_SECRET` and `VITE_API_URL` that are referenced nowhere in `src/`. (Also: putting a JWT secret in a Vite client-side env var is itself a bad signal — Vite inlines `VITE_*` into the bundle.) | TECH_STACK §14 item 14. | Delete the unused vars from `LAI-UI/.env.example`. Real auth (S1) doesn't need a client-side JWT secret. |
| **T8.** `LAI/.env.example` and `LAI/micro-services/.env.example` describe **two different model sets** (generation-old BGE-M3/Qwen2.5-7B/ms-marco vs current Qwen3.6-27B/Qwen3-Embedding-8B). | TECH_STACK §14 + audit. | Single source of truth — keep `micro-services/.env.example`, delete the older `.env.example` (it's the dead-stack config). |

---

# 10. The "replace the lawyer" program

The boss's requirement that LAI not tell users to contact a lawyer splits
into two layers with opposite handling.

## 10.1 Layer A — In-analysis hedge language (Phase 1; low risk)

Reflexive "consult a Fachanwalt", "as an AI…", filler caveats inside the
answer. Sources: the live model's defaults; the now-dead `constants.py:326`
`REFUSAL_LOW_CONFIDENCE` ("…konsultieren Sie die Originalquellen oder einen
Rechtsanwalt"); the shelved synthetic training data
(`pipeline/generate.py:84`).

**Fix:** the new validation/cleanup layer (§5.5) strips them. System prompts
tightened — *"decisive Fachanwalt; do not refer to other lawyers"*. Lands in
Phase 1.

## 10.2 Layer B — The formal liability footer and lawyer-replacement positioning

The footer *"does not substitute legal review"* (`ReportDownloadPanel.tsx:833,
2103`) is the company's liability shield, not filler. Dropping it is **not a
code change** — it is gated by:

1. Reliability proven (smoke test passes — §14).
2. Citation integrity solved (the 15.8 % fabrication problem — §9.7 D3).
3. Grounding/provenance on every fact (§5.5, §9.1 A3).
4. Coverage — no more "no information" ×25 (§4.1 keystone).
5. Demonstrable improvement (the feedback loop — §8.5).
6. **Counsel clearance on the German Rechtsdienstleistungsgesetz (RDG)** —
   whether LAI may be positioned and marketed as a legal-services provider.

This is the program's definition of "done", not a step.

## 10.3 The counsel question — sharpened

The RDG question goes to legal counsel **before** any external marketing.
Web-verified background to give counsel:

- **RDG §2(1)** defines a "legal service" as *any activity in a concrete
  third-party matter as soon as it requires legal examination of the
  individual case*. RDG §3 makes it permissible only as allowed by the RDG or
  other law.
- **The BGH cleared `smartlaw`** (a document/contract generator from Wolters
  Kluwer) as **not** a Rechtsdienstleistung — comparable to a "form manual",
  no concrete-individual-case activity, no human-or-thinking involvement
  beyond schematic subsumption.
- **Legal chatbots that provide preliminary legal opinions and are not
  operated by an attorney are incompatible with the current RDG**
  (multiple commentaries; the legal-tech press treats this as the live edge).

What this means for LAI's positioning:

| Positioning | RDG risk |
|-------------|----------|
| *"A pre-indexed legal-research tool that helps lawyers find statutes and case law faster — every answer cites its source; final review is the lawyer's"* | **Low.** Sits in the "form manual" / research-assistant zone. |
| *"A render-from-conversation DD report that the lawyer produces and then signs off on"* | **Low–Medium.** Same logic; the lawyer remains in the loop. |
| *"A lawyer-replacement that gives legal opinions on individual matters to non-lawyer end users"* | **High.** Closer to the chatbot pattern that's incompatible with current RDG. |

**Implication:** the marketing/positioning choice is the lever, not the
technology. Counsel's review should focus on the messaging that goes to
customers and partners, not the codebase. The §10.2 disclaimer footer
becomes droppable only when both (a) the system is reliable enough (the
Phase 1–3 gates) and (b) counsel signs off on the specific external
positioning.

Separately, the EU AI Act's bulk obligations land **2 August 2026**
(Article 50 transparency, Annex III high-risk requirements, GPAI obligations
already applied since August 2025). Whether a commercial legal-DD assistant
counts as Annex III "high-risk" is **debatable** — the Annex III justice
category concerns AI used *by judicial authorities*, not commercial tools
used by law firms. LAI more likely faces Article 50 transparency obligations
+ GPAI-downstream considerations. **Counsel question, not a given.**

The architecture choices in §4–7 (audit logging, provenance tags, deterministic
reconciler, citation handles) line up with the AI Act's transparency
obligations regardless of how counsel rules on the high-risk classification.

---

# 11. Strategic positioning

## 11.1 Competitive landscape (web-verified)

| Competitor | Reality |
|------------|---------|
| **Luminance** (UK) | $75 M Series C early 2025, multi-agent contract review, runs inside Microsoft Word, 1,000+ enterprises in 70+ countries. |
| **Harvey** (US) | Strong Germany presence. **Deutsche Telekom adopted Harvey in early 2024** — entire Law & Integrity team uses it. Data stored in Germany under contract. "Harvey Agents" real. |
| **Bryter** (Germany) | Frankfurt HQ, no-code legal/compliance automation, ~201 employees, ~$66 M raised, "Cool Vendor 2025". |
| **Leverton** (Berlin) | **Acquired by MRI Software, July 2019.** Pivoted to real-estate / lease abstraction. **Not an independent competitor.** |
| **Legartis** (Switzerland) | Swiss, since 2017. **Hosts all data AND all LLMs locally in Switzerland/Europe; GDPR-compliant; ISO 27001-certified.** Handles German/Swiss/Austrian + English. **Directly ships LAI's three "USP" claims** (on-prem, citation-grounded, bilingual). |
| **Spellbook** (Canada) | Native Microsoft Word add-in; 4,000+ legal teams across 80+ countries; GPT-4o-based. |

## 11.2 The corrected positioning

The teammate's strategy doc proposes four USPs: on-prem, pre-indexed German
corpus, citation-grounded, bilingual. **Three of the four are not differentiators
— Legartis already ships on-prem, citation-grounded, and bilingual operation.**
The two genuine differentiators are:

1. **The pre-indexed 350 GB German corpus** of statutes, commentaries, court
   rulings, past VDRs — no on-boarding cost for new matters.
2. **Vertical depth in German wind-energy DD** — the cadastral integration
   across 12 federal-state ALKIS endpoints, the 10H setback logic, the
   statutory-anchor prompts referencing BImSchG/BauGB/BNatSchG/EEG by section,
   the 12 wind-energy domain classifications, the 200 K synthetic Q&A across
   7 task types in those domains.

**Correction to the teammate's claim that *"Harvey, OpenAI, Anthropic are
effectively unusable for sensitive matters"*:** Harvey is demonstrably usable
in Germany under proper data-residency contracts (the Deutsche Telekom case
proves this). The accurate framing is *"these competitors require explicit
data-residency contracting; LAI is on-prem by default"* — a credential, not
an exclusion.

**The honest pitch:**

> LAI is the on-premise legal AI for German wind-energy due diligence. We have
> a pre-indexed 350 GB German legal corpus and the cadastral plumbing for all
> 12 federal states. Every answer is citation-grounded — click to see the
> source paragraph. It runs in your office; no Mandanten-Daten leave the
> building.

Note what this sentence does *not* claim: not "Harvey alternative", not
"general legal AI". Narrow and deep beats broad and shallow for a company
this size.

### 11.3 Compliance framing for the pitch — accurate, not overstated

Web-verified background for talking to a German legal audience:

- **BRAO § 43a** establishes the lawyer's confidentiality duty.
- **§ 203 (3) StGB** (2017 amendment) clarifies that *using cloud services is
  not "disclosure" of a secret* — i.e. cloud is **not prohibited per se**.
- **BRAO § 43e** governs the practical conditions: careful selection of the
  service provider, text-form contract obligating confidentiality and
  notifying of criminal-law consequences.
- For services from **abroad**, § 43e (4) requires a *comparable level of
  protection*. **EU member states are assumed comparable.** Non-EU
  jurisdictions (incl. US) require a documented assessment — and the legal
  literature flags real uncertainty about US equivalence.

**Accurate framing of LAI's on-prem credential:**

> *"LAI runs entirely in your firm's infrastructure. There's no § 43e BRAO
> outsourcing assessment to do for US cloud equivalence, no contractual
> confidentiality terms to negotiate with a foreign provider, no transit of
> Mandanten-Daten across borders."*

This is **stronger** than the teammate's draft *"Harvey/OpenAI/Anthropic are
effectively unusable for sensitive matters"* — and it's accurate. Harvey *can*
be used in Germany (Deutsche Telekom proves it), but the cost is a § 43e
compliance exercise the customer doesn't have to do with LAI.

---

# 12. Open strategic decisions

## 12.1 DDiQ-first vs chat-first

| Position | Source | Argument |
|----------|--------|----------|
| **DDiQ-first** | Boss-via-user in `AskUserQuestion` exchange (this session) | The smoke-test failure is what the boss reviewed; fixing it is the demonstrable competence gate. The DD report is the billable artifact. |
| **Chat-first** | Teammate's `LAI_V1_STRATEGY.md` §1–6 | Easier to demo (one LLM call, not 45); the chat with citations already feels like product; the DD report can later be *rendered from* the chat history. |

**Both are defensible.** The implementation order in §8 is mostly neutral —
the retrieval router, validation layer, auth, keystone, and consolidation are
needed for either. The decision affects:

- Which exit criterion drives Phase 1b Track A — a passing smoke test (§14)
  or a polished chat demo?
- Whether `/ddiq/report/generate` is wired in the UI for v1 or hidden until
  v1.1?

**Recommendation:** surface this to the boss **explicitly** as a decision —
*"we are recommending a shift in priority"* — not slip it through. If he
chooses chat-first, the teammate's render-from-conversation v1.1 path (§7.4
of his doc) becomes the official roadmap for the DD report.

## 12.2 Locked constraints (carry forward)

- **On-prem only.** No cloud. Frontend moves on-prem (§4.4).
- **No further budget** — public/free data sources only.
- **Learning = feedback loop.** No GPU retraining.
- **No code changes during analysis phase.** This guide is the deliverable.

## 12.3 Counsel-gated items

- **Q5 — German RDG.** May LAI be marketed as a legal-services provider /
  lawyer-replacement? Gates §10.2.
- **EU AI Act exposure.** Whether LAI is an Annex III high-risk system
  (§10.3) — Article 50 transparency obligations apply regardless.

## 12.4 Embedding-completion policy

Step 6 has ~40.5 M chunks pending. The keystone migration (§4.1) needs a
decision:

- **Finish first** — clean migration, but blocks Phase 2 by 3–5 GPU-days.
- **Migrate-9.46 M-and-stream-forward** *(recommended)* — Phase 2 starts
  sooner; new embeddings stream into pgvector via online upserts as Step 6
  continues in the background.

## 12.5 Closed decisions (for traceability)

These were open in earlier docs and have been resolved during the audit
sequence. Kept here so future readers see the full path.

| # | Question | Resolution |
|---|----------|------------|
| Q1 | How should DDiQ reach the corpus? | **RESOLVED.** Migrate corpus into Postgres `pgvector` as `halfvec(4096)` + HNSW, behind a shared `lai.retrieval` package (§4.1, §5.2). |
| Q2 | Budget for paid data sources (Grundbuch services, credit bureaus)? | **RESOLVED — no further budget.** Locked. Public/free only (§5.3). Grundbuch stays a "request from client" action item. |
| Q3 | What is the corpus's canonical home? | **RESOLVED.** `LAI/processed/pipeline_local.db` (350 GB SQLite). `app.db` (304 GB in `db_export/`) is a stale April snapshot — not authoritative. |
| Q4 | Deployment target — on-prem or cloud? | **RESOLVED — on-prem only.** Locked. Frontend moves on-prem (§4.4). |
| Q5 | German RDG / EU AI Act — may LAI be marketed as a lawyer-replacement; how does removing the disclaimer change exposure? | **Open — counsel.** Gates §10.2. |
| Q6 | Is Step 6 (corpus embedding) complete? | **RESOLVED — incomplete.** 9.46 M of 50 M embedded (~19%); ~40.5 M chunks have `embedding IS NULL`. The `WHERE embedding IS NULL` SELECT in `cli.py:917-933` confirms this is paused/in-progress, not a filter-by-design. Background job is Day-0 work (§8.1). |

---

# 13. Verification & test suite

*(Adopted from the teammate's strategy doc §5 with additions. The teammate's
testing battery is the right shape; we add citation-verifier and reconciler
tests that the audit identified as missing.)*

## 13.1 Coverage sanity (5 min)

```bash
cd /data/projects/lai/LAI
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('file:processed/pipeline_local.db?mode=ro', uri=True)
c = conn.cursor()
print("parent_chunks   :", c.execute("SELECT COUNT(*) FROM parent_chunks").fetchone()[0])
print("child_chunks    :", c.execute("SELECT COUNT(*) FROM child_chunks").fetchone()[0])
print("child_embeddings:", c.execute("SELECT COUNT(*) FROM child_embeddings").fetchone()[0])
for r in c.execute("SELECT doc_type, COUNT(*) FROM parent_chunks GROUP BY doc_type ORDER BY 2 DESC LIMIT 12"):
    print(f"  {r[1]:>12,}  {r[0]}")
EOF
```

Pass: counts match §1.3.

## 13.2 Embedding-job progress (hourly during the background run)

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/data/projects/lai/LAI/processed/pipeline_local.db?mode=ro', uri=True)
total = c.execute('SELECT COUNT(*) FROM child_chunks').fetchone()[0]
done  = c.execute('SELECT COUNT(*) FROM child_embeddings').fetchone()[0]
print(f'{done:,} / {total:,}  ({100*done/total:.1f}%)')"
```

Pass: `done` grows monotonically.

## 13.3 Retrieval sanity — golden German questions (30 min)

Save as `tests/fixtures/golden_de.json`:

```json
[
  {"q": "Wie hoch ist die Rückbauverpflichtung bei Windenergieanlagen nach § 35 Abs. 5 BauGB?",
   "expected_doc_type": "gesetz",
   "expected_keywords": ["Rückbau", "BauGB", "Sicherheit"]},
  {"q": "Welche Abstandsregelung gilt in Niedersachsen für Windenergieanlagen?",
   "expected_doc_type": "gesetz",
   "expected_keywords": ["NBauO", "Abstandsfläche"]},
  {"q": "Wann ist eine UVP-Pflicht für einen Windpark gegeben?",
   "expected_doc_type": ["gesetz", "urteil"],
   "expected_keywords": ["UVPG", "Vorprüfung"]},
  {"q": "Was sind die Anforderungen an die Schriftform nach § 550 BGB bei Pachtverträgen?",
   "expected_doc_type": "gesetz",
   "expected_keywords": ["Schriftform", "BGB", "Pacht"]},
  {"q": "Welche Schattenwurfgrenzwerte sieht die TA Lärm vor?",
   "expected_doc_type": ["gesetz", "urteil"],
   "expected_keywords": ["TA Lärm", "Schattenwurf", "30 Minuten"]}
]
```

Run each via `/query`; inspect top-5. Pass: ≥3/5 chunks have expected
`doc_type` and ≥1 expected keyword. Below 3/5 on more than one question →
retrieval-quality investigation needed.

## 13.4 Cross-lingual sanity (15 min)

Translate each question to English, run again, compare top-5 overlap with the
German run. Pass: ≥60 % overlap. Below 40 % → prompt-engineer the English path.

## 13.5 End-to-end golden conversations (1 hour, German lawyer review)

Five representative conversations a wind lawyer might have. The teammate's
list in `LAI_V1_STRATEGY.md` §5.5 is the starting set:

1. *"I'm reviewing the Lamstedt project. The OVG ruling partially voided the
   permit for turbines L6, L7, L9. What's the legal consequence?"*
   — Expected: § 35 Abs. 5 BauGB Rückbau triggers surfaced from corpus.
2. *"Compare the Schriftform requirement under § 550 BGB to the actual
   Pachtvertrag in [uploaded doc]."*
   — Expected: both `[M-n]` (upload) and `[C-n]` (§ 550 commentary) citations.
3. *"Translate the key clauses of this German Wartungsvertrag to English and
   explain the Verfügbarkeitsgarantie."*
   — Expected: English answer; German quoted verbatim.
4. *"What deadlines exist across all the documents in this Matter?"*
   — Expected: enumerated Fristen with statutory anchors.
5. *"What's missing from the supplied documents that a buyer would require?"*
   — Expected: structured gap list with the missing items cited to their
   governing statutes (the §4.1 + §5.5 promise made real).

German lawyer reviewer scores each on 1-5: (a) factual correctness, (b)
citation quality, (c) language quality, (d) practice usefulness. Target
average ≥ 4.0 across the 20 cells.

## 13.6 Latency benchmark

| Metric | Target | Hard cap |
|--------|--------|----------|
| Time-to-first-token | < 2 s | < 5 s |
| Total response time | < 15 s | < 30 s |
| Citation render in UI | < 100 ms | < 500 ms |

If above hard cap, suspect (in order): reranker batch size; Postgres query
plan on the joined `corpus_chunks` / `ddiq_doc_chunks`; embedding cache
cold-start; missing HNSW index.

## 13.7 Citation validator unit tests

1. Answer with all citations resolving → pass through unchanged.
2. Answer with one fabricated `[C-99]` → validator strips it, marks the
   sentence `(unverified)`.
3. Answer about 10H setback when `Matter.bundesland != 'BY'` → validator
   appends a jurisdiction warning footer.
4. Answer with a coordinate that geocodes outside the Matter's Bundesland →
   block the answer, return validation error.

## 13.8 Reconciler unit tests (new — fills the §3.1-D gap)

1. Overview row says *"7 errichtet, 3 genehmigt, 0 geplant"* and
   `extract_wea_statuses` returns 10 turbines → reconciler returns
   `total=10, breakdown={…}`. No contradiction.
2. Overview row says *"7 errichtet, 3 aufgehoben"* and
   `extract_wea_statuses` returns 6 → reconciler emits one finding flagging
   the discrepancy. Canonical count is `extract_wea_statuses` length (the
   authoritative ground truth).
3. Total Capacity says *"21,8 MW"* but `len(weas) * mean(rated_power) == 12 *
   2.0`. Reconciler flags as a finding, not a printed contradiction.

## 13.9 Confidentiality / outbound-network audit

*(Critical — the on-prem story collapses otherwise.)*

Run on the backend host while a chat turn is in flight:

```bash
sudo netstat -anp | grep -E "ESTABLISHED|SYN_SENT" | grep -v 127.0.0.1
```

Expected: only DNS to internal hosts. Outbound to `*.openai.com`,
`*.anthropic.com`, or any non-internal host → critical bug, fix before any
pilot.

Known external touches that **must remain off the chat hot path**:

- ALKIS WFS endpoints (12 federal-state hosts, see
  `ddiq_report.py:61-93`) — used by the cadastral pipeline only.
- Nominatim geocoding (`nominatim.openstreetmap.org`) — DDiQ only.
- HuggingFace model downloads (currently disabled via `HF_HUB_OFFLINE=1`
  and `TRANSFORMERS_OFFLINE=1` in `LAI/docker-compose.yml`).

For a customer demo, verify HF offline flags are set; the geocoding / ALKIS
calls are not part of the chat flow and should not fire.

---

# 14. Success bar

Re-run the Lamstedt smoke test. Grade on these eight, all measurable:

1. **Non-empty Findings chapter.** No "extraction failed" placeholder.
2. **Turbines on the correct map** (Landkreis Cuxhaven), or explicitly flagged
   `unverified`. No more Bremen.
3. **One consistent turbine count** across text, capacity math, title, table.
4. **Every "missing" item rendered with the governing statute cited** — e.g.
   *"§35(5) BauGB Rückbaubürgschaft — request from client"*. No more defensive
   paragraphs.
5. **One language end-to-end** (no Denglisch).
6. **No reflexive "consult a Fachanwalt" filler** in the body.
7. **Every fact carries a visible source tag** (`uploaded_doc / legal_corpus /
   registry / estimated / unverified`).
8. **PDF tables render cleanly** — re-confirmed in the PDF we have (already
   true; do not regress).

If we deliver all eight, the smoke test stops being embarrassing and LAI is
positioned to be evaluated on its real strengths. If we cannot, the rest
doesn't matter.

---

# 15. Appendices

## Appendix A — Key file references

| File | Role |
|------|------|
| `LAI/src/lai/api/serve_rag.py` | Live chat backend. The `EXTERNAL_LAW_REFS` gate at `:1017-1028` is the Phase-1 Day-1 edit. |
| `LAI/src/lai/search/eval.py` | Current retrieval kernel (`Corpus`, `load_embeddings`, `retrieve_dense`, `retrieve_bm25`, `rrf_fuse`). Becomes the basis of `lai.retrieval`. |
| `LAI/src/lai/analyzer/reconciler.py` | The deterministic-reconciliation pattern DDiQ should adopt (§5.4). |
| `LAI/src/lai/analyzer/llm_client.py` | Has `<think>`-trace stripping that DDiQ's LLM client should also have. |
| `LAI/src/lai/pipeline/embed.py` + `pipeline/cli.py:917-933` | Step 6 embedder. Verify resume flag before the Day-0 background job. |
| `LAI/micro-services/ddiq_report.py` | The 2,463-line DDiQ god-file. Target for the §9 catalog. |
| `LAI/micro-services/cadastral_pipeline.py` | The 13-step parcel workflow + 12 ALKIS endpoints. |
| `LAI-UI/src/react-app/contexts/AuthContext.tsx` + `utils/jwt.ts` | The fake auth to replace (§4.3). |
| `LAI-UI/src/react-app/components/ReportDownloadPanel.tsx` | The disclaimer footer at `:833, 2103` (§10.2). |
| `LAI/docker-compose.yml` | Runtime services. The authoritative deployment topology to converge on. |
| `LAI/processed/pipeline_local.db` | The 350 GB SQLite corpus. To be migrated into pgvector in Phase 1b Track B. |

## Appendix B — Re-verified facts (probed in this session)

- `pipeline_local.db` row counts: parent 13,807,675 / child 49,953,830 /
  embedded 9,462,540 (probed).
- DDiQ Postgres: `ddiq_doc_chunks` 250, `ddiq_documents` 5, `ddiq_reports` 3,
  others 0 or 5 (probed).
- Storage: `data/lai-raw/` 671 G, `data/lai-segments/` 50 G,
  `data/lai-embeddings/` 77 G (via `du -sh`).
- 155 GB RAM load: 9,462,540 × 4096 × 4 = 155.04 GB (math).
- `ddiq_report.py` is 2,463 lines / 129,035 bytes (`wc -l -c`).
- 37 SECTION_QUESTIONS (overview 11 + land 8 + permits 8 + economics 10) —
  counted via direct `"label"` extraction from source.
- 12 router endpoints in `ddiq_report.py` (grep `@router.{get,post,…}`).
- ~3,200 LOC dead code — line-by-line across the dead packages.
- 12 ALKIS WFS endpoints — read `ALKIS_WFS_ENDPOINTS` at `ddiq_report.py:61`.
- Lamstedt coordinates 53.622796 N / 9.147855 E vs Bremen Überseestadt
  ~53.094 / 8.785 → ~65 km offset *(web-verified)*.
- Live runtime state (2026-05-14): `lai_postgres_main` healthy on 5434;
  `lai_embedding` healthy on 8003; `lai_analyzer_llm` healthy on 8005;
  `lai_redis` healthy; `lai-backend` healthy on 18001; `serve_rag` healthy
  on 18000; only `lai-user-doc-processor` unhealthy.

## Appendix C — Estimates not independently measured

- **~400 LOC of function-body duplication across 6 helper categories** —
  measured directly (PDF 98 + chunker 34 + embedding 44 + reranker 23 + LLM
  client 145 + JSON parse 58 = 402). Adding hybrid retrieval, system prompts,
  session memory, greeting routing, DB pool wrapper, and `<think>` stripping
  brings the rough total to ~600–900 LOC *(those categories not individually
  measured)*. The earlier "~1,500–2,000" estimate was overstated.
- **`p` in the compounding-failure math** — illustrative, not measured.
- **15.8% fabricated citations** — taken from the project's own
  `audit_results.json`; not re-run.
- **3–5 GPU-days to finish Step 6** — order-of-magnitude estimate; measure on
  first batch and recalibrate. The teammate's "2–3 GPU-days" is plausibly
  optimistic.
- **Per-pass success rate `p` for the chapter-loss math** — depends on the
  schema-enforced output change in §5.1; measure on a sample run after Phase
  1b Track A.

## Appendix D — External sources (web-verified)

- Luminance — https://www.luminance.com/
- Harvey / Deutsche Telekom — https://www.harvey.ai/customers/deutsche-telekom
- Bryter — https://bryter.com/
- Leverton / MRI Software acquisition (July 2019) — https://www.mrisoftware.com/news/mri-software-acquires-ai-real-estate-pioneer-leverton-turn-unstructured-data-business-insights/
- Legartis — https://www.legartis.ai/
- Spellbook — https://www.spellbook.legal/
- EU AI Act timeline — https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai · https://artificialintelligenceact.eu/implementation-timeline/
- Lamstedt coordinates — https://www.findlatitudeandlongitude.com/l/Seth,+Lamstedt,+Samtgemeinde+B%C3%B6rde+Lamstedt,+Landkreis+Cuxhaven,+Lower+Saxony,+21769,+Germany/8230282/
- BRAO § 43a (Grundpflichten / Verschwiegenheit) — https://www.gesetze-im-internet.de/brao/__43a.html
- BRAO § 43e (Inanspruchnahme von Dienstleistungen) — https://www.gesetze-im-internet.de/brao/__43e.html
- Cloud + StGB § 203 (3) outsourcing reform commentary — https://www.haufe.de/recht/kanzleimanagement/neuregelung-der-schweigepflicht-bei-dienstleistern-des-anwalts_222_418378.html
- BGH ruling on `smartlaw` document generator (Wolters Kluwer) under RDG — https://www.wolterskluwer.com/de-de/news/bgh-erklaert-dokumentengenerator-smartlaw-fuer-zulaessig
- Legal-tech vs RDG commentary (when LegalTech without a lawyer is permissible) — https://digitalisierungsrecht.eu/digitale-rechtsanwaelte-wann-ist-legaltech-ohne-anwalt-zulaessig/

## Appendix E — Corrections to inputs (record for traceability)

Items in the source documents that did not survive re-verification:

- **Teammate's "~127 GB RAM"** (`LAI_V1_STRATEGY.md`) → corrected to **155 GB**
  (9.46 M × 4096 × 4 bytes). The 127 GB was a stale README docstring figure
  for 8 M chunks.
- **Teammate's "~50 sequential LLM calls" per report** → corrected to **~45**
  (37 sections + 8 critical passes). Their per-section subtotal (11+8+8+10=37)
  matches; the "TOTAL 50-60" double-counted.
- **Teammate's "Bavarian 10H rule applied to a Niedersachsen project"** →
  softened: the smoke test *references* 10H in the Setback section and
  explicitly says it cannot be evaluated; not the same as "applied".
- **Teammate's *"Harvey, OpenAI, Anthropic are effectively unusable for
  sensitive matters"*** → softened with Deutsche Telekom counter-example
  (§11.2).
- **Teammate's "four USPs" framing** → corrected: three of four are not
  differentiators (Legartis ships them) — the genuine differentiators are the
  pre-indexed German corpus and the wind-energy vertical depth (§11.2).
- **Original audit "~6,000 LOC dead code"** → corrected to **~3,200 LOC** (line-
  counted directly; see Appendix B).
- **Original audit "14 DDiQ endpoints"** → corrected to **12** (grep count).
- **Original audit "live system broken right now"** → corrected: was true at
  audit time; resolved during the session. Current state in Appendix B.
- **`_parse_alkis_feature` severity Critical → Medium** — the bug is real but
  only manifests when multiple candidate keys are simultaneously present.
- **Teammate's *"BRAO § 43a prohibits sending Mandanten-Daten to US cloud
  providers"*** → corrected to: cloud use is **not prohibited per se** since
  § 203 (3) StGB (2017); BRAO § 43e governs practical conditions; non-EU
  providers require a documented equivalence assessment under § 43e (4). The
  more accurate framing is in §11.3.
- **Teammate's framing of EXTERNAL_LAW_REFS as a bug** → refined: it is an
  **intentional** scoping choice (the code comment at `serve_rag.py:1023-1024`
  documents the design — preventing the model from conflating corpus contract
  chunks with the uploaded contract). The fix is *replace the heuristic with
  the retrieval router's typed decision*, not "remove the gate" naively.
- **Estimate "~1,500–2,000 LOC duplicated"** → measured **~400 LOC** for the
  six headline helper categories; rough total **~600–900 LOC** for all
  duplicated logic (still less than the original estimate).

---

*This document is the single source of truth for the implementation phase.
Update it in place as decisions are made. The seven earlier working documents
in `harsh/` remain for audit traceability.*
