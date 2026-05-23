# LAI v1 — Strategy, Architecture, and 10-Day Roadmap

**Document type:** Consolidated strategy + implementation plan
**Audience:** Project lead, engineering team, demo stakeholders
**Date:** 2026-05-15 — _last status update: 2026-05-16_
**Status:** **Implementation in progress.** Backend foundation phase
(`lai.common`) shipped + first consumer (DDiQ) migrated and deployed.
Demo-roadmap (Section 10, frontend/UX) has not started. See
[Implementation status](#implementation-status-2026-05-16) below.

This document consolidates the findings of a multi-session audit of LAI
(currently at `/data/projects/lai/LAI/`) into one reference. It explains
what exists today, why it didn't satisfy a German wind-energy lawyer,
what the target product looks like, how the four storage tiers relate,
how to evaluate the knowledge base, and the day-by-day plan to ship a
demo within 10 working days.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
   - [Implementation status (2026-05-16)](#implementation-status-2026-05-16)
2. [Stakeholder feedback — why v0 was dismissed](#2-stakeholder-feedback)
3. [Architectural audit — what exists today](#3-architectural-audit)
4. [The four data tiers — 671 GB vs 50 GB vs 77 GB vs 350 GB](#4-the-four-data-tiers)
5. [Evaluation & testing — verifying the knowledge base works](#5-evaluation--testing)
6. [Target architecture — chat-first legal AI](#6-target-architecture)
7. [The DDiQ-style report — what it is and where it fits](#7-the-ddiq-style-report)
8. [The four USPs for v1](#8-the-four-usps-for-v1)
9. [v1 feature list — must / should / nice](#9-v1-feature-list)
10. [10-day roadmap](#10-10-day-roadmap)
11. [Considerations and risks](#11-considerations-and-risks)
12. [First-hour action items](#12-first-hour-action-items)

Appendices:
- [A. Demo script (5-minute partner pitch)](#appendix-a-demo-script)
- [B. USP statement for sales](#appendix-b-usp-statement)
- [C. Key file references](#appendix-c-key-file-references)

---

## 1. Executive summary

LAI is a self-hosted legal-AI platform for German wind-energy due diligence.
It already includes a 671 GB raw corpus of German legal material (statutes,
court rulings, contracts, books, past VDRs), a processed/embedded form of
that corpus in a 350 GB SQLite database, GPU-hosted LLM and embedding
services (Qwen3.6-27B + Qwen3-Embedding-8B + Qwen3-Reranker-8B), and a
React frontend.

The platform is technically capable but not yet a sellable product. When
shown to a German wind-energy lawyer, the platform was dismissed at a
glance for three reasons:

1. The lawyer-facing deliverable (the DDiQ report) does not actually
   query the 350 GB corpus — it only summarises the user's uploaded
   PDFs. The knowledge base is on disk but not in the loop.
2. The output contains credibility-breaking errors (a map of Bremen
   instead of Cuxhaven, Bavarian setback rules applied in Lower Saxony,
   incomplete cadastral data, a literal "findings extraction failed"
   string in the client-facing report).
3. No clickable citations. Lawyers cannot trust answers they cannot
   verify against source paragraphs.

The strategic pivot recommended here is: **make the conversational chat
(grounded in both the uploaded PDFs AND the 350 GB corpus) the primary
product surface for v1**. The DDiQ-style report becomes a downstream
artifact rendered from the conversation in v1.1.

The 10-day roadmap is feasible if scope is honestly bounded. The hard
compromises (deferring the full DDiQ report, the DOCX letterhead export,
and the deadline calendar to v1.1) are called out in section 11.

---

## Implementation status (2026-05-16)

This document was authored 2026-05-15 as a pre-implementation plan.
This section is appended one day later to record what has actually
shipped on the `v2-restructure` branch, and what is still ahead. The
detailed line-item tracker lives in [`harsh/PROGRESS.md`](./PROGRESS.md).

### TL;DR

- ✅ **Backend foundation (Phase 1a) — done.** The `lai.common` package
  (`llm`, `reranker`, `exceptions`) is shipped with 280 unit tests + 12
  live integration tests, 100% line + 100% branch coverage, and a
  strict CI gate. 12 commits.
- ✅ **First consumer migrated (Phase 1b Track A — item 1) — done and
  deployed.** DDiQ's `llm_call` / `llm_json` now run through
  `SyncLlmClient`. Container swapped via `docker compose up -d --build`;
  live probes pass. 3 commits.
- ⏳ **The rest of Phase 1a + Phase 1b + Phase 2 + Phase 3** — pending.
  See [What is remaining](#what-is-remaining) below.
- ⏳ **The 10-day demo roadmap in Section 10 (frontend / UX) has not
  started.** Section 10 is chat-first UX work (citation chips, bilingual
  mode, matter data model, auth wiring); the shipped work so far is the
  **backend reliability foundation** those UX items will sit on top of.

### What is done

#### Phase 1a foundation — `lai.common` (12 commits, 2026-05-08 → 2026-05-15)

```
de88c01 feat(common): add RerankerClient (async + sync) for TEI /rerank
c051a73 feat(common): integration tests + switch guided-JSON to response_format
        (ADR 0004 supersedes 0002 primary)
b21cf90 feat(common): add LlmClient (async + sync) — the orchestrator
f80eaf5 feat(common): add lai.common.llm.config — pydantic-settings configuration
80d9bab feat(common): add lai.common.llm.metrics — Prometheus observability bundle
d812743 fix(common): strip_think always normalises whitespace via str.strip
eaed94d feat(common): add lai.common.llm.salvage_json — repair-and-parse
3506fee feat(common): add lai.common.llm.strip_think — Qwen3 reasoning-trace strip
1f041fe feat(common): typed exception hierarchy for lai.common
9322bb2 docs(adr): record the four foundational decisions for lai.common.llm
5e85276 feat(common): scaffold lai.common package with smoke tests
447188d ci: add quality gate (ruff strict, mypy strict on lai.common,
        pytest+coverage, bandit)
```

Modules shipped:

- `lai.common.llm` — `LlmClient` (async) + `SyncLlmClient` (sync) over
  the OpenAI-compatible vLLM endpoint. JSON via `response_format:
  json_schema` (ADR 0004 supersedes 0002 after live testing proved
  vLLM silently ignores `extra_body.guided_json` on this build).
  Server-side `<think>` stripping (ADR 0003). Tenacity retry with
  exponential backoff. `LlmConfig` (pydantic-settings, frozen,
  `LAI_LLM_*` env prefix). Six Prometheus metrics with bounded
  cardinality. `salvage_json` helper for repairing malformed LLM output
  (code fences, brace balancing, trailing commas — all
  string-context-aware).
- `lai.common.reranker` — `RerankerClient` (async) + `SyncRerankerClient`
  over HuggingFace TEI's `/rerank`. Auto-batches across
  `max_client_batch_size`, merges with global-index re-mapping, applies
  `top_n` after merge.
- `lai.common.exceptions` — 13 typed exception classes (root:
  `LaiCommonError`). Two parallel hierarchies (`LlmError`,
  `RerankerError`) so callers can catch either family. kwarg-only
  context fields for structured logging.

Quality gate (CI + local `make check`):

- `ruff` strict rule set (E/W/F/I/N/UP/B/C4/SIM/RET/PIE/PT/RUF).
- `mypy --strict` scoped to `lai.common.*` (legacy keeps the
  permissive default until migrated).
- `pytest` + coverage gate ≥ 85% on `lai.common` (currently 100%
  line + 100% branch).
- `bandit` security scan.
- Pre-commit hooks (`.pre-commit-config.yaml`) + GitHub Actions
  workflow (`.github/workflows/ci.yml`) with an aggregated `ci-gate`
  job for branch protection.

Architecture decision records in `LAI/docs/adr/`:

| # | Title | Status |
|---|-------|--------|
| 0000 | Record architecture decisions | Accepted |
| 0001 | `lai.common.llm` — async-primary client surface | Accepted |
| 0002 | `lai.common.llm` — guided-JSON via `extra_body.guided_json` | **Superseded by 0004** |
| 0003 | `lai.common.llm` — strip `<think>` traces server-side | Accepted |
| 0004 | `lai.common.llm` — use OpenAI `response_format` (supersedes 0002 primary) | Accepted |

Real bugs already caught by the test discipline:

1. `strip_think` whitespace inconsistency (Hypothesis property test).
2. Qwen3 thinking-mode token-budget truncation surfaced via integration
   test; `_parse_chat_response` now exposes `finish_reason` so callers
   can distinguish truncation from spurious empties.
3. vLLM silently ignores `extra_body.guided_json` on this build (curl
   probes confirmed; ADR 0004 records the switch).
4. Lone Unicode surrogates break `json.dumps`+`json.loads` round-trip
   (Hypothesis; resolved by excluding the `Cs` category from test
   strategies).

#### First consumer migration — DDiQ (3 commits, 2026-05-16)

`micro-services/ddiq_report.py`'s `llm_call` and `llm_json` (11 in-file
call sites + the `CadastralPipeline` `llm_json_fn` callback at line
1962) now run through `SyncLlmClient` + `salvage_json` instead of a
hand-rolled `requests.post`. Both functions keep their exact
signatures so no call site changed.

```
501a315 refactor(ddiq): back llm_call / llm_json with lai.common.SyncLlmClient
aba3279 chore(ddiq): widen Docker build context to bundle lai package
0946b65 chore(common): relax lai package Python floor to >=3.11
```

Net gains over the legacy code:

- Retry with exponential backoff (was: single-shot `requests.post`).
- Server-side `<think>` stripping (was: reasoning traces contaminated
  downstream JSON parses).
- `salvage_json` repair pass before the second-attempt retry (was:
  nothing between the two `json.loads`).
- `llm_json` returns `{}` on hard failure (was: uncaught
  `JSONDecodeError` on the second attempt would crash the entire
  pipeline mid-report).
- Structured logs (`llm.call.complete duration_seconds=… status=…`)
  and Prometheus metrics on every call.
- Connection pooling via a single module-level `httpx.Client` per
  uvicorn worker.

Deployment (2026-05-16):

- Docker build context widened from `micro-services/` to the LAI/ repo
  root so the in-repo `lai` package can be installed alongside the
  microservice. `--no-deps` install + explicit transitive deps in
  `requirements.txt` keep the image lean (no vllm / docling / celery
  pulled in).
- `lai-backend` container swapped via `docker compose up -d --build`.
- `/health` returns `200 OK`.
- Live `llm_call` probe against the running `lai_analyzer_llm`:
  returned the expected answer in 19.86 s with `status=success`.
- Live `llm_json` probe: salvage path produced `{'sum': 4,
  'product': 12}` end-to-end.

### What is remaining

**Phase 1a remainder — shared utilities still to extract**

1. `lai.common.embedding.EmbeddingClient` — runtime query-embedding
   client. Currently duplicated 3× across `serve_rag.py`, `api.py`,
   `ddiq_report.py`. Required by `lai.common.retrieval` (Phase 1b
   Track B). No coordination needed with the teammate's batch
   embedding job — different code paths.
2. `lai.common.pdf.PdfExtractor` — PyMuPDF + Tesseract OCR fallback
   with quality gate. Duplicated 3×.
3. `lai.common.chunk.Chunker` — German-legal-aware sentence splitter.
   Duplicated 2× in live code + a third copy in the pipeline package.
4. Delete the ~3,200 LOC dead stack (`api/main.py`, `api/pipeline.py`,
   the abandoned `auth/`, `documents/`, `extraction/`, `generation/`,
   `infra/`, `search/{routes,repository,hybrid_search,reranker,query_analyzer}.py`).
   Salvage the JWT validation logic and `citation_verifier` design
   into `lai.common` first; then pure delete.

**Phase 1b Track A — DDiQ engine reliability (continuation)**

The high-leverage fixes that directly retire the ~22%-chapter-loss
class in production reports. Item 1 (LLM-call migration) shipped today;
the rest are independent of each other:

1. ~~Migrate `ddiq_report.py` LLM calls to `SyncLlmClient`.~~ **Done
   2026-05-16.**
2. **Per-finding iteration in `generate_findings`** — replace the
   single batched `llm_json()` call (the "Manual review required"
   failure mode from the smoke test) with per-row iteration.
3. **Geocoding plausibility gate** — bounding-box validation against
   the named Bundesland / Landkreis; cache TTL on
   `ddiq_geocode_cache`. Kills the "turbines in Bremen" failure.
4. **Deterministic reconciler** — port the
   `lai.analyzer.reconciler.py` pattern. Fixes the four-conflicting-
   turbine-counts class.
5. **Validation / guardrail layer** — output cleanup pass: hedge-
   language strip, single-language enforcement, provenance-tag
   honesty, structured "missing" state instead of defensive paragraphs.
6. **Adjacent reliability fixes** — `_parse_alkis_feature` inverted
   control flow; `request_fingerprint` index UNIQUE; sync-path crash
   handling; aux-table `ON CONFLICT`; cache TTLs.

**Phase 1b Track B — the retrieval keystone** (parallel to Track A;
needs `lai.common.embedding` and a healthy `lai_postgres_main` first)

1. Corpus migration from `pipeline_local.db` (SQLite, 350 GB) to
   pgvector `halfvec(4096)` + HNSW in `lai_postgres_main`. One-time,
   hours-to-days.
2. `lai.common.retrieval` — dense + BM25 + RRF + rerank over the
   unified store. Consumed by both `serve_rag` and DDiQ.
3. Retrieval router — per-question source selection (uploaded docs +
   legal corpus + public registries) with provenance tags.

**Phase 2 — statutory grounding + public registries** (gated on Track B)

1. **2A statutory grounding** — every section grounds in the legal
   corpus via the retrieval router. Turns "no information" into
   "§ X requires Y; absent from data room, request from client".
2. **2B public registry connectors** — MaStR, Handelsregister, ALKIS
   (already partly). `lai.common.connectors` package with a `Connector`
   ABC. All free / public per the locked budget.
3. **2C provenance enforcement** — every fact carries a typed source
   tag; renderer enforces honest labels.

**Phase 3 — feedback-loop learning** (can overlap Phase 1b)

1. Capture endpoint — `POST /feedback` storing original / corrected /
   reason keyed by `(conversation_id, message_id)`. The
   `lai_feedback` table already exists unused.
2. Correction memory — pgvector-stored corrections retrieved as
   few-shot guidance for similar future prompts. No GPU retraining.

**Operational — small batch**

1. Frontend on-prem move — drop Vercel / Cloudflare configs; serve
   `dist/` from Nginx / Caddy on the box.
2. Wire the existing Prometheus + Grafana stack in `Docker/monitoring/`
   (already present, not running). The Phase 1a code emits the
   metrics; they're invisible until scraped.
3. Pin all Docker images and dep ranges — `vllm:latest`,
   `prometheus:latest`, etc. → digest-pinned; microservice
   `requirements.txt` → lockfile.

**Owned by the teammate (we do not touch)**

- Step 6 corpus-embedding background job — 40.5 M of 50 M child chunks
  pending. Runs on GPU 1 in the background via `resume_step6.sh`.
  Different code path from the runtime `EmbeddingClient` listed above.

**Demo-roadmap (Section 10) — not started**

Section 10's day-by-day plan is frontend / UX work. None has shipped:

- Day 1 — remove `EXTERNAL_LAW_REFS` gate, citation handles, prompt
  enforcement.
- Day 2 — chip rendering, right-side panel, PDF preview, streaming.
- Day 3 — bilingual mode.
- Day 4 — confidentiality badge, validator gate.
- Day 5 — matter data model + routes.
- Day 6 — matter workspace UI.
- Day 7 — auth wiring.
- Day 8 — quick actions, audit log writes.
- Day 9 — rehearsal.
- Day 10 — final polish + demo.

The decision on when to flip from "backend reliability" → "demo
roadmap" mode is a project-lead call. Phase 1b Track A items 2-6 (the
remaining DDiQ reliability fixes) directly close the credibility
failures the lawyer cited; they are competing for the same calendar
days as the Section-10 UX work.

### Open decisions

| # | Question | Status |
|---|----------|--------|
| Q1 | How does DDiQ reach the corpus? | **Resolved** — pgvector `halfvec(4096)` + HNSW via `lai.common.retrieval` (Track B). |
| Q2 | Budget for paid data sources? | **Resolved** — no further budget. Public only. |
| Q3 | Corpus canonical home? | **Resolved** — `pipeline_local.db` (350 GB); `app.db` is stale. |
| Q4 | Deployment target? | **Resolved** — on-prem only. |
| Q5 | German RDG / EU AI Act positioning? | **Open — counsel review needed.** Gates dropping the "does not substitute legal review" footer. |
| Q6 | Step 6 status? | **Resolved** — incomplete; teammate owns. |

### Pointers

- [`harsh/PROGRESS.md`](./PROGRESS.md) — line-item implementation tracker
- [`harsh/IMPLEMENTATION_GUIDE.md`](./IMPLEMENTATION_GUIDE.md) — master plan
- [`harsh/DDIQ_ROADMAP.md`](./DDIQ_ROADMAP.md) — phased roadmap
- `LAI/docs/adr/` — the five architecture decisions on-disk
- `LAI/CONTRIBUTING.md` — workflow and quality gate
- `git log --oneline v2-restructure` — full commit history

---

## 2. Stakeholder feedback

### 2.1 The lawyer's reaction (verbatim, paraphrased)

> "Make a competitive v1 for the market — then I will look into it. LAI is nothing."

### 2.2 What he saw in 30 seconds

| Signal | Effect |
|---|---|
| Map of Bremen instead of Cuxhaven | Credibility broken on first scan |
| Footer "Auto-generated, does not substitute legal review" | Reads as "we ourselves don't trust this" |
| `"findings extraction failed"` in the Action Items table | Reads as "internal demo, not a product" |
| No clickable citations | He cannot defend a sentence he didn't write |
| No workflow integration (Beck-online, juris, Outlook, DMS) | He'd have to abandon his stack to use this |
| No firm-branded deliverable | He can't put it in front of a Mandant |
| No visible moat ("why this and not Harvey?") | Default assumption: no reason |

### 2.3 What "competitive v1" means in lawyer-speak

He did **not** mean more features. He meant: pick **one** task he does
weekly. Do it end-to-end at partner-quality. Then he'll engage.

The strongest single wedge is **conversational research on a Mandat**:
upload the relevant PDFs, talk to them with the knowledge base in the
loop, get cited answers in seconds. That is the basis for the v1
described in section 6.

---

## 3. Architectural audit

### 3.1 Three FastAPI applications, mostly disconnected

| App | Location | Storage it talks to | Status |
|---|---|---|---|
| `serve_rag` (chat + contract analyzer) | host process `:18000` | SQLite `pipeline_local.db` (350 GB) + in-process 127 GB RAM cache + `sessions.db` | Active |
| `lai-backend` (DDiQ report) | Docker container `:18001` | Postgres pgvector (`lai_db`) | Active |
| `src/lai/api/main.py` (auth + search + documents + extraction) | nowhere | Postgres via `lai.infra.database` pool | **Dead code** — never started |

Critical observation: `micro-services/*.py` imports **nothing** from
`src/lai/`. The DDiQ microservice and the chat backend share only the
LLM container and the embedding container. No domain types, no
retrieval code, no schemas.

### 3.2 Storage sprawl

- **SQLite `pipeline_local.db` (350 GB)** — corpus + child embeddings
  (serve_rag only)
- **SQLite `sessions.db`** — chat sessions (serve_rag only)
- **Postgres `lai_db`** — DDiQ uploaded documents only (microservice only)
- **Process RAM (127 GB)** — serve_rag's embedding cache
- **Filesystem** — `processed/uploads`, `processed/ddiq_reports`,
  `data/lai-raw`, `data/lai-segments`, `data/lai-embeddings`

No shared abstraction across these. No migration path between them.

### 3.3 The DDiQ report does not use the corpus

Traced in [`micro-services/ddiq_report.py`](../micro-services/ddiq_report.py)
at line 475 (`search_doc_chunks`):

```python
def search_doc_chunks(doc_ids, query_embedding, top_k=15):
    ...
    """ SELECT ... FROM ddiq_doc_chunks
        WHERE doc_id = ANY(%s)            -- ONLY user-uploaded docs
        ORDER BY embedding<=>%s::vector LIMIT %s """
```

The `doc_ids` come from the UI and only ever reference uploaded files.
**There is no codepath in the DDiQ microservice that touches the 350 GB
SQLite corpus.** The lawyer-facing report is generated by asking Qwen3.6-27B
~50 questions about the user's uploaded PDFs. The corpus is irrelevant
to that flow.

### 3.4 The chat side uses the corpus only when a regex matches

Traced in [`src/lai/api/serve_rag.py`](../src/lai/api/serve_rag.py) line 1017–1028:

```python
use_contract = session_uses_contract(sid, req.question)
if use_contract:
    # Only fire RAG when the question explicitly mentions external law
    use_rag = bool(EXTERNAL_LAW_REFS.search(req.question))
```

`EXTERNAL_LAW_REFS` is a German legal-keyword regex (`§`, `BImSchG`,
`BauGB`, `EEG`, `BGB`, `Urteil`, `BGH`, …). If the user types
*"cross-check this with your database"* in English, none of those tokens
match, `use_rag = False`, and the corpus is silently skipped.

### 3.5 Postgres tables are mostly empty

```
ddiq_doc_chunks         | 250    -- chunks from 5 uploaded files
ddiq_documents          | 5
ddiq_reports            | 3
ddiq_contracts          | 0      -- never populated
ddiq_classified_parcels | 0      -- classification pipeline never ran
ddiq_contract_parcels   | 0
ddiq_parcel_cache       | 0
ddiq_geocode_cache      | 5
```

The cadastral classification half of the data model was scaffolded but
the pipeline that fills it was never run end-to-end on real data.

---

## 4. The four data tiers

This is the single most-asked question. Short answer first:

> **They are not four databases. They are four stages of the same data.
> The 350 GB SQLite is the one runtime-readable knowledge base.
> Everything else fed into it.**

### 4.1 What each tier actually is

```
STAGE 1 (raw)              STAGE 2 (parsed)        STAGE 3 (chunked)
data/lai-raw/  671 GB  ─▶  data/lai-segments/  ─▶  parent_chunks +
PDFs / HTML /              50 GB  normalised        child_chunks tables
JSON on disk               text segments            in pipeline_local.db
                                                    (~250 GB at this
                                                    stage)
                                                            │
                                                            ▼
STAGE 4 (embedded)                          STAGE 4a (intermediate shards)
child_embeddings table inside       ◀───▶   data/lai-embeddings/
the SAME pipeline_local.db                  77 GB  shard files
(BLOBs push the DB from 250 → 350 GB)       (duplicate of what's already
                                             in the SQLite — the DB is
                                             the authoritative copy)
```

### 4.2 Which one to use

**The 350 GB SQLite (`processed/pipeline_local.db`) is the only one to
query.** Everything else is either source material (671 GB raw) or
intermediate pipeline output (50 GB segments, 77 GB embedding shards).

The 671 GB raw corpus is not queryable as such — it's PDFs and text
files on disk. Querying it would require running the pipeline. The 350
GB SQLite IS the queryable result of running that pipeline.

### 4.3 The real gap — 81% of chunks are not embedded

A direct query against the 350 GB SQLite found:

```
Parent chunks          ~13.8 million
  legal_text           6,370,822      general legal prose
  urteil               5,262,573      court rulings
  gesetz               1,438,319      statutes
  beschluss              592,895      court decisions
  vertrag                 73,791      contracts
  vdr                     42,121      virtual data rooms
  gerichtsbescheid         6,286
  fachbuch                 4,909      specialist books
  dd_report                  293

By source corpus
  multilegalpile        11,096,628    HF MultiLegalPile
  hf_cases               1,662,102    HF case law
  gerdalir                 561,577    GerDaLIR retrieval set
  openlegaldata            347,755    openlegaldata.io scrape
  (untagged Phase 1)       139,613    Custom — VDRs, DD reports, library

Child chunks (smaller windows)        : 49,953,830
Child chunks WITH embeddings          :  9,462,540   ← 19%
Child chunks WITHOUT embeddings       : 40,491,290   ← 81%
```

**Today serve_rag can semantic-search ~9.5 million chunks. The remaining
40 million are reachable only via BM25 (exact keyword) search.** This is
the practical cap on retrieval quality.

Completing the embedding pass on the missing 40 M chunks is **a known,
mechanical job at ~2-3 GPU-days** (Qwen3-Embedding-8B on the existing
GPU 1, batch 32). It can run in the background during the 10-day sprint
without blocking any other development. After it completes the corpus
grows 5× without any UI or code change.

This is the "use all the data" lever. Pull it on Day 1.

---

## 5. Evaluation & testing

The knowledge base is the moat. Verifying it works is non-negotiable
before the demo. The tests below progress from cheap-and-fast to
end-to-end.

### 5.1 Coverage sanity (5 minutes)

Verify the corpus content is what we believe it to be:

```bash
cd /data/projects/lai/LAI
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('processed/pipeline_local.db', timeout=10)
c = conn.cursor()
print("parent_chunks total       :", c.execute("SELECT COUNT(*) FROM parent_chunks").fetchone()[0])
print("child_chunks total        :", c.execute("SELECT COUNT(*) FROM child_chunks").fetchone()[0])
print("child_embeddings total    :", c.execute("SELECT COUNT(*) FROM child_embeddings").fetchone()[0])
print()
print("by doc_type:")
for r in c.execute("SELECT doc_type, COUNT(*) FROM parent_chunks GROUP BY doc_type ORDER BY 2 DESC LIMIT 12"):
    print(f"  {r[1]:>12,}  {r[0]}")
EOF
```

**Pass criteria:** counts match section 4.3. If they do not, the
database has changed since this document was written and the rest of
the plan needs re-validation.

### 5.2 Embedding-job progress (run hourly during the 10 days)

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/data/projects/lai/LAI/processed/pipeline_local.db', timeout=10)
total = conn.execute('SELECT COUNT(*) FROM child_chunks').fetchone()[0]
done  = conn.execute('SELECT COUNT(*) FROM child_embeddings').fetchone()[0]
print(f'{done:,} / {total:,}  ({100*done/total:.1f}%)')"
```

**Pass criteria:** the `done` count grows monotonically. Target: ≥ 80%
by Day 9.

### 5.3 Retrieval sanity — golden German questions (30 minutes)

A canonical set of wind-energy questions whose retrieval can be eyeballed
by a German speaker (or auto-translated for non-German developers). Save
the file as `tests/fixtures/golden_de.json`:

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

Run via `serve_rag`'s `/query` endpoint, inspect top-5 chunks. Each
question should return at least 3/5 chunks where the doc_type and at
least one expected keyword appear. Score below 3/5 on more than one
question → retrieval quality issue, investigate before proceeding.

### 5.4 Cross-lingual sanity — same questions in English (15 minutes)

The Qwen3-Embedding-8B model is multilingual. Translate each question
to English, run again, compare top-5 overlap with the German run. Target
overlap: ≥ 60%. Below 40% means the cross-lingual feature will not work
out of the box and needs prompt engineering.

### 5.5 End-to-end golden conversations (1 hour)

Take 5 representative full conversations a wind lawyer might have.
Examples:

1. *"I'm reviewing the Lamstedt project. The OVG ruling partially voided
   the permit for turbines L6, L7, L9. What's the legal consequence?"*
   — Expected: corpus should surface § 35 Abs. 5 BauGB Rückbau triggers.
2. *"Compare the Schriftform requirement under § 550 BGB to the actual
   Pachtvertrag in [uploaded doc]."*
   — Expected: both M-citations (the upload) and C-citations
   (§ 550 BGB commentary) in the answer.
3. *"Translate the key clauses of this German Wartungsvertrag to English
   and explain the Verfügbarkeitsgarantie."*
   — Expected: English answer, German quoted verbatim.
4. *"What deadlines exist across all the documents in this Matter?"*
   — Expected: enumerated list of Fristen with statutory anchors.
5. *"What's missing from the supplied documents that a buyer would
   require?"*
   — Expected: structured gap list (no Rückbaubürgschaft, no Netzanschluss-
   vertrag, no Versicherungsschein, etc.).

Run each, save the response. A German lawyer reviewer should score each
answer on a 1-5 scale for: (1) factual correctness, (2) citation
quality, (3) language quality, (4) usefulness in real practice. Target
average ≥ 4.0 across all 20 cells.

### 5.6 Latency benchmark

Per chat turn target:

| Metric | Target | Hard cap |
|---|---|---|
| Time-to-first-token | < 2 s | < 5 s |
| Total response time | < 15 s | < 30 s |
| Citation render in UI | < 100 ms | < 500 ms |

If above hard cap on Day 9, diagnose. Most likely culprits: reranker
batch size, Postgres query plan on `ddiq_doc_chunks`, or embedding
cache cold-start.

### 5.7 Citation validator unit tests

Cover four cases:
1. Answer with all citations resolving → pass through.
2. Answer with one fabricated `[C-99]` → validator strips it, marks
   the sentence `(unverified)`.
3. Answer about 10H setback when Matter.bundesland != 'BY' → validator
   appends a footer warning.
4. Answer with coordinate that geocodes outside the Matter's
   Bundesland → block the answer, return validation error.

### 5.8 Confidentiality / outbound-network audit

The on-prem story collapses if any code path leaks data. Run on the
backend host:

```bash
sudo netstat -anp | grep -E "ESTABLISHED|SYN_SENT" | grep -v 127.0.0.1
```

while a chat turn is in flight. Expected: only DNS, only to internal
hosts. Outbound to `*.openai.com`, `*.anthropic.com`, etc. → critical
bug, must fix before any pilot.

---

## 6. Target architecture

### 6.1 The conceptual shift

```
FROM (what LAI is today)                  TO (what it must become)

Two disconnected apps:                    ONE matter-centric workspace:

 • Chat (corpus only,                       Matter ▸ Documents ▸
   uploads partially used)                  Conversation ▸ Outputs

 • DDiQ report (uploads only,             Chat is the PRIMARY surface.
   corpus ignored, 1-2 h gen)             Reports are just
                                          "rendered conversation."
Two databases, three FastAPIs,
three embedding clients.                  Lawyer sees: "A junior
                                          associate who has read all
Lawyer sees: "GPT with a German           German wind-energy law."
glossary."
```

### 6.2 System layers (target)

```
┌────────────────────────────────────────────────────────────────────┐
│ PRESENTATION                                                       │
│   React app (LAI-UI). Three surfaces only:                         │
│     [Matters list] → [Matter workspace: docs + chat + outputs]     │
│   Sidebar always shows: "On-Premise · BRAO § 43a · DSGVO" badge    │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│ API  ──  ONE FastAPI app  (lai.api.main, port :18000)              │
│  Routers: /auth /matters /documents /chat /retrieve /exports       │
│  Middleware: JWT auth, audit logger, request tracing               │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│ CORE / DOMAIN  ──  lai.core, lai.domain                            │
│  Matter ──┬── Documents (per-matter, user uploads)                 │
│           ├── Conversations ── Messages (with citations)           │
│           ├── Exports        (DOCX / PDF / ICS rendered later)     │
│           └── AuditEvents    (immutable, AI Act compliant)         │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│ RETRIEVAL  ──  lai.retrieval.Retriever                             │
│                                                                    │
│   ┌─────────────────────────┐    ┌────────────────────────────┐    │
│   │ CorpusCollection        │    │ MatterCollection           │    │
│   │  read-only              │    │  per-matter, read-write    │    │
│   │  • 350 GB pgvector or   │    │  • Matter-scoped docs      │    │
│   │    SQLite               │    │  • Re-uses same Postgres   │    │
│   │  • 8M+ embedded chunks  │    │  • Tagged with mandate_id  │    │
│   │    (50M after BG job)   │    │  • Authoritative for       │    │
│   │  • BM25 FTS index       │    │    "this case" facts       │    │
│   │  • Authoritative for    │    │                            │    │
│   │    "the law says"       │    │                            │    │
│   └─────────────────────────┘    └────────────────────────────┘    │
│                                                                    │
│   Combined via Reciprocal Rank Fusion + Qwen3-Reranker-8B          │
│   Output: top-K chunks tagged source_kind ∈ {corpus, matter}       │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│ INFRASTRUCTURE  ──  lai.infra                                      │
│   • LLMClient    →  vLLM Qwen3.6-27B   :8005   (one client)        │
│   • Embedder     →  vLLM Qwen3-Embed-8B :8003                      │
│   • Reranker     →  in-process Qwen3-Reranker-8B on GPU            │
│   • Database     →  Postgres + pgvector (corpus + matters)         │
│   • Cache        →  Redis (session state, query cache)             │
│   • Storage      →  S3-compatible (MinIO) for uploaded PDFs        │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│ HORIZONTAL CONCERNS                                                │
│   • Audit log (every prompt, retrieval set, output, model version) │
│   • Language layer (EN/DE input + output toggle)                   │
│   • Validator gate (citation must be real; jurisdiction must match)│
│   • Confidentiality guard (no outbound calls except where allowed) │
└────────────────────────────────────────────────────────────────────┘
```

### 6.3 The dual retrieval combiner

The piece that turns LAI from "chat with PDF" into "legal AI":

```
            ┌──────────────────────────┐
            │ User question (any lang) │
            │ + conversation context   │
            └─────────────┬────────────┘
                          │
                          ▼
            ┌──────────────────────────┐
            │ Qwen3-Embedding-8B       │  multilingual:
            └─────────────┬────────────┘  EN query → DE docs OK
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   ┌────────────────┐          ┌─────────────────┐
   │ CORPUS         │          │ MATTER DOCS     │
   │ 350 GB         │          │ THIS mandate    │
   │ (read-only)    │          │ (uploads)       │
   │                │          │                 │
   │ Dense top-50   │          │ Dense top-50    │
   │   + BM25       │          │   + BM25        │
   │     top-50     │          │     top-50      │
   └────────┬───────┘          └────────┬────────┘
            │                           │
            └─────────────┬─────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Reciprocal Rank Fusion   │
            │ (combine 200 candidates) │
            └─────────────┬────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Qwen3-Reranker-8B        │  → top-8
            │ (multilingual cross-enc) │
            └─────────────┬────────────┘
                          ▼
            ┌──────────────────────────┐
            │ Tag each: source_kind =  │
            │  'corpus' | 'matter'     │
            │ Add citation handle:     │
            │  [C-1]…[C-n] for corpus  │
            │  [M-1]…[M-n] for matter  │
            └─────────────┬────────────┘
                          ▼
                  →  to the LLM prompt
```

### 6.4 What happens during one chat turn

```
LAWYER  →  UI  →  API  →  RETRIEVER  →  LLM  →  STORAGE

1.  User types question
2.  POST /chat (auth, matter_id, history)
3.  Detect language; load conversation history
4.  Build retrieval query
5.  Embed query → search BOTH collections in parallel
6.  RRF fuse → rerank top-30 → keep top-8 with citation handles
7.  Build language-aware prompt (system + history + chunks + question)
8.  Stream tokens from Qwen3.6-27B
9.  Post-LLM validator:
       • every [C-n]/[M-n] must resolve to a retrieved chunk
       • Bundesland sanity (10H only if Bayern)
       • coordinate sanity (geocode inside Matter's Bundesland)
10. Stream to UI + persist + write audit_event
11. UI renders citations as chips → click opens source preview
```

End-to-end target latency: **5-15 seconds.** Conversation is one LLM
call, not fifty.

---

## 7. The DDiQ-style report

### 7.1 What it is today

The DDiQ report is the 15-page PDF generated by the `lai-backend`
microservice (`micro-services/ddiq_report.py`). For each uploaded
project, it answers ~37 structured German wind-energy DD questions
across four sections (Overview, Land Security, Permits, Economics),
plus passes for WEA extraction, infrastructure, cadastral parcels,
findings, and timeline. Output is HTML/PDF with a project location
map.

Sample output: [`docs/smoke_test_report.pdf`](smoke_test_report.pdf).

### 7.2 Observed issues (from a German wind lawyer's review)

| Issue | Severity |
|---|---|
| Location map showed Bremen instead of Cuxhaven | Critical — credibility break |
| Bavarian 10H rule applied to a Niedersachsen project | Substantive legal error |
| `§ 311b Abs. 1 BGB` cited incorrectly for Grundschuld | Citation error |
| Cadastral parcel table incomplete (3 of 9 mentioned) | Data quality |
| `Action Items: findings extraction failed` in output | Unprofessional output |
| Empty rendering between section headers and content | Cosmetic but jarring |
| Mixed English/German rendering across rows | Style inconsistency |
| Missing categories: MaStR, AwSV, § 16b BImSchG repowering | Coverage gaps |
| ~50 sequential LLM calls → 1-2 hour generation time | Performance |
| 250 chunks from uploaded docs only — corpus not used | The core gap |

### 7.3 Why it currently takes 1-2 hours

```
Step           Activity                       LLM Calls    Wall time
─────────────────────────────────────────────────────────────────────
metadata       Project name / preparedFor          1        ~30 s
sections       overview  (11 questions)           11       ~10 min
               land       ( 8 questions)           8       ~ 8 min
               permits    ( 8 questions)           8       ~ 8 min
               economics  (10 questions)          10       ~10 min
geocoding      Nominatim HTTP                      0        ~5 s
WEA extract    Turbine table                     1-3       ~2 min
infra          Substation, cables                  1       ~1 min
cadastral      13-step pipeline + ALKIS WFS     5-15    ~10-20 min
timeline       Date / deadline pass                1       ~1 min
findings       Risk synthesis                    1-3       ~2 min
parcels        Land reference extraction           1       ~1 min
                                                ─────      ────────
                                       TOTAL  50-60       60-120 min

Each call is sequential. No batching. No parallelisation across sections.
```

### 7.4 Recommended path for v1.1 (NOT v1)

Replace the question-by-question batch pipeline with a
**"render-from-chat-history"** flow:

1. Lawyer has a full conversation in the Matter (the v1 product).
2. At the end, presses **"Generate DD memo from this conversation"**.
3. A single LLM call (or 3-5 calls per section) takes the conversation
   transcript + the matter documents and renders the structured DDiQ
   format.
4. Citations from the chat carry through to the report.
5. Total wall time: 1-3 minutes instead of 60-120.

Benefits:
- Report inherits the corpus-grounded citations from chat.
- Lawyer drives what goes into the report by what they asked.
- Faster, cheaper, more accurate, more trusted.

Costs:
- Requires the v1 chat to be solid first. Hence v1.1.

### 7.5 Out of scope for v1 demo

For the 10-day demo, **hide the "Generate report" button in the UI**.
Do not run the existing DDiQ pipeline in front of the lawyer. The chat
conversation is the demo. The report is the next deliverable.

---

## 8. The four USPs for v1

These are the things told to a partner in the first 30 seconds:

| USP | Why it works in Germany |
|---|---|
| **On-premise / firm-hosted** (or dedicated EU GPU) | BRAO § 43a Verschwiegenheit prohibits sending Mandanten-Daten to US cloud providers. Harvey, OpenAI, Anthropic are effectively unusable for sensitive matters. LAI runs in the firm. Hard legal requirement, not a feature. |
| **Pre-indexed German legal corpus (350 GB)** | Statutes, commentaries, DD reports, VDRs already embedded and reranked. New matters benefit from day 1. No customer onboarding cost. |
| **Citation-grounded answers** | Every sentence carries [M-n]/[C-n] tags. Click → exact page in source. Uncited claims are marked "unverified." This is the difference between a "draft" and a "billable work product." |
| **Bilingual operation** | Non-German lawyers (international funds, foreign in-house counsel) can query German documents in English, receive English answers with the German originals quoted verbatim. Opens the market beyond Germany-only Kanzleien. |

The pitch sentence:
> "On-prem German legal AI with a pre-indexed 350 GB corpus,
> citation-grounded answers, and English-language operation."

That sentence does not describe Harvey, Luminance, Kira, Bryter, or
any product currently sold in the German wind-energy market. That is
the v1 competitive position.

---

## 9. v1 feature list

Tagged MUST (cannot demo without) / SHOULD (matters but cuttable if
behind) / NICE (cut if behind).

| Category | Feature | Priority | Effort |
|---|---|---|---|
| **Core** | Chat against uploaded PDFs + corpus, always both | MUST | 1 day |
| | Citation tags [C-n]/[M-n] in every assistant reply | MUST | 1 day |
| | Click [C-n] → side panel with corpus excerpt | MUST | 1 day |
| | Click [M-n] → PDF page (highlight optional in v1) | MUST | 1 day |
| | Streaming token output | MUST | ½ day |
| | Conversation memory across turns | done | — |
| **Knowledge base** | Embedding-completion job (background) | MUST | runs in BG |
| **Trust / diff.** | "On-Premise · BRAO § 43a · DSGVO" badge | MUST | ½ day |
| | Bilingual mode (EN/DE input + output toggle) | MUST | 1 day |
| | "Unverified" badge for uncited claims | MUST | ½ day |
| | Validator: flag jurisdictional mismatch | SHOULD | ½ day |
| **Workspace** | Matter (Mandat) workspace: create / list / switch | MUST | 1 day |
| | Per-Matter document drop zone | MUST | ½ day |
| | Per-Matter conversation thread | MUST | ½ day |
| | Sidebar: Mandanten list with Bundesland pills | SHOULD | ½ day |
| **Auth** | Real auth (email + password, JWT) | MUST | 1 day |
| | Per-Matter access control | SHOULD | ½ day |
| **Polish** | Quick actions on selected text | SHOULD | 1 day |
| | Demo seed: pre-built "Lamstedt" Matter | MUST | ½ day |
| | Statute-lookup inline ("§ 35 BauGB" mini-card) | NICE | 1 day |
| | Loading skeletons, error states, empty states | MUST | ½ day |

**Total: 6.5 MUST days + 2.5 SHOULD days = ~9 working days of net work,**
with the background embedding job running in parallel from Day 1. Day 10
is buffer + demo prep.

### 9.1 Explicitly out of v1 (defer to v1.1)

- DDiQ-style 15-page report generation
- DOCX firm-letterhead export
- Deadline extractor → .ics calendar
- Risk matrix Ampel render
- Word / Outlook plugin
- DSGVO data-handling admin page
- Audit log viewer (rows are written in v1; viewer is v1.1)

---

## 10. 10-day roadmap

| Day | Deliverables |
|---|---|
| **1** | • **Background:** kick off embedding completion job on the 40 M missing chunks (Qwen3-Embedding-8B on GPU 1, batch 32). Log to `logs/embedding_completion.log`. Verify pgvector or SQLite write path is not contended with serve_rag's read path. <br>• Remove `EXTERNAL_LAW_REFS` gate in `serve_rag.py:1026`. Default to `use_rag = True` whenever uploads exist. <br>• Add citation handles: every retrieved chunk carries `source_kind` ('corpus' \| 'matter') and stable `cite_id` ([C-n] / [M-n]). <br>• Modify `RAG_SYSTEM` prompt to enforce citation. <br>• End-of-day: backend `/chat` returns citations. Verify via curl. |
| **2** | • UI: render assistant messages with [C-n] / [M-n] as clickable chips. Right-side panel: corpus excerpt for [C-n], PDF preview for [M-n]. <br>• PDF preview via `react-pdf`. Initial v1 just opens the right page (highlight optional). <br>• "(unverified)" badge → amber pill in UI. <br>• Streaming: wrap `llm_generate` to yield deltas, confirm `/chat` is SSE-streamed. <br>• End-of-day: chat resembles ChatGPT with side-by-side citations. |
| **3** | • Bilingual mode. Add `target_language` to `/chat`. Inject into `RAG_SYSTEM` as a template var. Test both directions: <br>&nbsp;&nbsp;&nbsp;– DE PDF + EN question → EN answer with German citations quoted. <br>&nbsp;&nbsp;&nbsp;– DE PDF + DE question → DE answer. <br>• UI: language toggle in chat header. <br>• Verify cross-lingual quality on 5 test queries. |
| **4** | • Confidentiality badge at top of UI. Static. Five tokens: "On-Premise", "BRAO § 43a", "DSGVO", "EU AI Act", "No data leaves". <br>• Validator gate (server-side, post-LLM): parse all citations, strip unresolved, add "(unverified)"; Bundesland sanity warning. <br>• Mid-week checkpoint: embedding job ≥ 8 M new embeddings written. |
| **5** | • Matter data model. Tables: `matters(id, name, bundesland, project_type, created_by, created_at)`, `matter_documents(matter_id, document_id, role)`. <br>• Migrate existing sessions to a default Matter. <br>• `/matters` routes: GET list, POST create, GET detail. <br>• Wire `MatterCollection` retrieval: filter by `matter_id`. |
| **6** | • UI: Matter workspace. Sidebar: Mandanten list with Bundesland pill + doc count. Main pane: Documents tab + Chat tab. <br>• Drag-and-drop document drop zone. <br>• Document status: parsed / embedded / indexed, with progress per doc. |
| **7** | • Auth wiring. Mount the existing `src/lai/auth/` router on `serve_rag` (refactor to unified `lai.api.main` is v1.1). <br>• Login/signup screen. JWT in `Authorization` header on every fetch. <br>• `AuthContext` actually validates (currently fakes it). <br>• Per-Matter access: `matters.created_by` + creator-only rule for v1. |
| **8** | • Quick actions on selected text: tooltip → "Explain in EN" / "Find related judgments" / "Show statute". <br>• Audit log writes: every chat request → `audit_events(user_id, matter_id, prompt_hash, model_version, retrieved_cite_ids, response_hash, ts)`. Viewer is v1.1. <br>• Demo seed: pre-built "Windpark Lamstedt — Acquisition DD" Matter with 6-8 curated PDFs. |
| **9** | • End-to-end rehearsal. Run the 5-minute demo script (Appendix A) 3×. Fix top 5 paper-cuts. <br>• Verify embedding job complete or ≥ 80% done. <br>• Performance pass. <br>• Loading skeletons, error states, empty states. |
| **10** | • Final polish. Backup demo machine. Pre-warmed Vite, pre-loaded matter, pre-cached embedding load (serve_rag startup is ~5 min; start before the lawyer arrives). <br>• Failure-mode rehearsal. <br>• Demo. |

---

## 11. Considerations and risks

### 11.1 Language barrier for non-German developers

The codebase is mostly English. German is concentrated in:
- `src/lai/api/serve_rag.py:88-103` — `RAG_SYSTEM`, `CHAT_SYSTEM` constants
- `src/lai/analyzer/prompts.py` — analyzer prompts
- `micro-services/ddiq_report.py:900-988` — `SECTION_QUESTIONS` dict

Workarounds for non-German developers:
1. Pipe every German prompt through an LLM with "translate to English,
   preserve meaning" — add the English next to it as a comment.
2. Build a `tests/fixtures/golden_qa.json` with **expected behavior**
   rather than expected German text. Test retrieval and citation
   structure, not language.
3. Once the bilingual mode is built (Day 3), do all dev testing in
   English even though the docs are German.
4. Add `scripts/dev/translate.py` that pipes any text through Qwen3.6-27B
   for ad-hoc translation during development.
5. The DB schemas (`ddiq_documents`, `ddiq_reports`, `parent_chunks`,
   `child_chunks`, etc.) are all English — navigate the data model
   without German.

### 11.2 What gets compromised (honest list)

1. **DDiQ-style 15-page report** — out of v1. The chat IS the demo.
   Add the rendered report in v1.1 as a "render-from-conversation" flow.
2. **DOCX firm-letterhead export** — out of v1. 3-4 days alone.
3. **Embedding completion finish line** — if Day 9 only sees 80%
   embedded instead of 100%, demo with what you have. 80% is still 4-5×
   today's queryable corpus.

If none of these can be deferred, the 10-day window must grow.

### 11.3 Technical risks

| Risk | Mitigation |
|---|---|
| Embedding job contends with serve_rag's read of `pipeline_local.db` (SQLite write lock) | Run with WAL mode + verify `serve_rag` opens read-only. Worst case: pause embedding during demo. |
| GPU OOM when running embedding + analyzer + reranker concurrently | Reduce embedding batch size; pin embedding to GPU 1, analyzer to GPU 0. |
| 27B analyzer too slow for live chat (>15s response) | Have a fallback to Qwen2.5-7B-Instruct on a switch. Many chat turns can use 7B without quality loss. |
| Demo Matter PDFs trigger an unexpected retrieval failure | Pre-warm: run all expected demo questions on Day 9, freeze any caches. |
| Lawyer asks a "trap question" outside corpus coverage | The validator's "(unverified)" path turns this from "embarrassing fabrication" into "honest can't-answer." |
| Network blip during demo | Run the demo against localhost. Disable wifi-dependent features. |

### 11.4 Confidentiality boundaries

Even on-premise, certain features can leak data externally:
- ALKIS WFS calls (`opengeodata.lgln.niedersachsen.de`, `wfs.nrw.de`)
- Nominatim geocoding (`nominatim.openstreetmap.org`)
- HuggingFace model downloads (currently disabled via
  `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in
  `docker-compose.yml`)

For the demo, verify the HF offline flags are set; the geocoding /
ALKIS calls are not part of the core chat flow and should not fire.

---

## 12. First-hour action items

Concrete commands to run in the first hour:

### 12.1 Verify environment

```bash
cd /data/projects/lai/LAI
ls processed/pipeline_local.db                # should be ~350 GB
docker network inspect lai_network            # should exist
docker ps --filter name=lai_                  # what's already running
```

### 12.2 Kick off the embedding-completion job

(Verify exact flags by reading `src/lai/pipeline/embed.py` first.)

```bash
cd /data/projects/lai/LAI
mkdir -p logs
nohup .venv/bin/python -m lai.pipeline.cli embed --resume --batch-size 32 \
    --device cuda:1 \
    > logs/embedding_completion.log 2>&1 &
echo "embedding PID: $!"
```

Then verify progress every hour with the snippet in section 5.2.

### 12.3 Branch + start the gate-removal patch

```bash
git checkout -b v1-demo
# Edit src/lai/api/serve_rag.py — section around line 1017-1028
# Replace the EXTERNAL_LAW_REFS gate with always-on retrieval when
# a contract is uploaded.
```

### 12.4 Curate the demo Matter

This is product work, not engineering. It deserves a half-day from
the project lead personally. Select 6-8 PDFs that produce **good demo
answers**:

- A Pachtvertrag with a clear Schriftform issue (§ 550 BGB).
- A BImSchG-Bescheid with named Auflagen and Nebenbestimmungen.
- A relevant OVG ruling (e.g., the Niedersachsen Denkmalschutz one).
- An Enercon Wartungsvertrag with named warranty terms.
- A Lageplan / Flurstücke list.
- A Versicherungsschein (or its absence — flag in the demo).
- A Netzanschlussvertrag (or its absence — flag in the demo).

The demo is only as good as the documents in it.

### 12.5 Assign ownership

The 10-day plan needs ~3 people:
- **Backend engineer** — retrieval, citations, validator, auth, Matter
  data model.
- **Frontend engineer** — chat UX, citation panel, Matter workspace,
  bilingual toggle.
- **Project lead** (you) — orchestration, the embedding job, demo
  curation, end-to-end testing, the lawyer relationship.

---

## Appendix A: Demo script

Five-minute pitch to a German wind-energy partner:

1. **"Here's a fresh installation, running on a server in your office.
   Nothing leaves the building."** [point at confidentiality badge]
2. **"I'll create a Matter — 'Windpark Lamstedt acquisition'."** [30 s]
3. **"Drop your 4 PDFs in."** [30 s; parsed indicator]
4. **"Now I'll ask in English — because I'm not a German lawyer."**
   Type: *"Is the Rückbau security sufficient under § 35 Abs. 5 BauGB?"*
5. **Answer appears in 8 seconds**, citing both the contract page
   [M-3] and BeckOK § 35 Rn. 158 [C-7]. Click the [C-7] citation. Side
   panel shows the BeckOK excerpt. Click [M-3]. PDF opens at the bond
   clause.
6. **"Switch to German now."** Toggle language. Same question. Same
   answer, in German.
7. **"Total elapsed time: under 5 minutes. Same task billed at
   €1,200/hour normally takes 4-6 hours."**

---

## Appendix B: USP statement (for sales)

> **LAI is the on-premise German legal AI for renewable-energy
> due-diligence. Pre-indexed 350 GB corpus of German statutes,
> commentaries, court rulings, and past VDRs. Every answer is
> citation-grounded — click a sentence to see its source paragraph.
> Operates in German and English, so international counsel can work
> with German documents without leaving English. Runs in your office
> or your dedicated EU GPU — never sends Mandanten-Daten to US cloud
> providers, fully BRAO § 43a and DSGVO compliant.**

---

## Appendix C: Key file references

| File | Role |
|---|---|
| [`src/lai/api/serve_rag.py`](../src/lai/api/serve_rag.py) | Current chat backend. The file to modify in Day 1. |
| [`src/lai/api/main.py`](../src/lai/api/main.py) | Dead but well-designed alternate FastAPI. Target for v1.1 unification. |
| [`src/lai/auth/`](../src/lai/auth/) | Existing auth scaffolding to wire on Day 7. |
| [`src/lai/search/eval.py`](../src/lai/search/eval.py) | `Corpus`, `load_embeddings`, `retrieve_dense`, `retrieve_bm25`, `rrf_fuse`. The retrieval kernel. |
| [`src/lai/pipeline/embed.py`](../src/lai/pipeline/embed.py) | Embedding step. Verify resume flag before kicking off the Day 1 background job. |
| [`micro-services/ddiq_report.py`](../micro-services/ddiq_report.py) | The 1-2 hour report pipeline. Out of v1; basis for v1.1 render-from-conversation. |
| [`docker-compose.yml`](../docker-compose.yml) | Runtime services: analyzer, embedding, postgres, redis. |
| [`scripts/ops/start.sh`](../scripts/ops/start.sh) | Starts the host process + Docker stack. |
| [`processed/pipeline_local.db`](../processed/pipeline_local.db) | The 350 GB SQLite knowledge base. The single source of truth. |
| [`data/lai-raw/`](../data/lai-raw/) | 671 GB raw corpus. Source material only — not queried at runtime. |

---

*End of document. For changes, edit this file directly and commit on the
`v1-demo` branch.*
