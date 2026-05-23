# LAI — Deep Research: Q1/Q3 Resolution + Failure & Architecture Analysis

**Date:** 2026-05-14
**Purpose:** Resolve the two open decisions that block Phase 2 sizing (Q1 corpus
access, Q3 corpus canonical home) and provide the deep failure/architecture
analysis requested. All claims cite `file:line` or a probe; uncertainties are
marked *unverified*.

---

# PART A — Q3: Where the corpus actually lives (RESOLVED)

The earlier roadmap guess ("`app.db` is the only populated store") was **wrong**.
Corrected by direct probe:

**The live corpus that `serve_rag` loads is `LAI/processed/pipeline_local.db` —
350.6 GB SQLite.** Confirmed: `search/eval.py:40` and `serve_rag.py:50` both set
`DB = LAI_DIR / "processed" / "pipeline_local.db"`.

### Contents of `pipeline_local.db` (probed)

| Table | Rows | Note |
|-------|------|------|
| `parent_chunks` | 13,807,675 | parent chunks, text |
| `child_chunks` | 49,953,830 | child chunks + FTS5 mirror |
| `child_embeddings` | **9,462,540** | the searchable vector set — 4096-dim float32 (16,384 bytes/blob, confirmed) |
| `child_chunks_fts` | 49,953,830 | SQLite FTS5 BM25 index |
| `training_samples` | 200,006 | synthetic fine-tune data |
| `pilot_embeddings` | 100,000 | — |
| `chunk_classifications` | 134,474 | — |

### What this means

1. **`app.db` (304 GB) and `db_export/pipeline.db` (1.1 GB) are stale snapshots**
   (April 11–12), with the *old* schema (`chunks` 25.8M, `law_chunks`,
   `trading_chunks`). They are exports, not the live store. They also contain a
   copy of the `ddiq_*` tables (8 reports) — i.e. the smoke-test report was
   generated when DDiQ's Postgres was alive; that data was snapshotted into
   `app.db`.
2. **Only 9.46M of 50M child chunks are embedded** (~19%). Either by design or
   because Step 6 (embedding) is incomplete — confirmed in `RE_VERIFICATION.md`
   §C: 9.46M of 50M chunks embedded, ~40.5M with `embedding IS NULL`.
   `ops/resume_step6.sh` exists and
   was modified this session, which points to *embedding still in progress*.
   **Confirm before sizing Phase 2** — the corpus may still be growing.
3. **serve_rag's RAM load is 155 GB**, not the 127 GB its docstring claims:
   `9,462,540 × 4096 × 4 bytes = 155.0 GB` (`load_embeddings`, `eval.py:119-126`).
   The docstring is stale.
4. Embedding dimension is **4096** everywhere that's live (`eval.py:46`
   `EMBED_DIM=4096`; blob = 16,384 bytes). The 1024-dim references are all in
   dead/legacy config.

---

# PART B — Q1: How DDiQ should reach the corpus (RECOMMENDATION)

**The problem:** DDiQ retrieves only over `ddiq_doc_chunks` in Postgres
(`ddiq_report.py:475`); the legal corpus is in SQLite `pipeline_local.db`. They
share nothing — different engine, different schema, different retrieval code.
The only commonality is the embedding *model* (Qwen3-Embedding-8B, 4096-dim), so
vectors are at least dimensionally compatible.

### Three options (from cheapest to soundest)

| Path | What | Effort | Verdict |
|------|------|--------|---------|
| **1. HTTP to serve_rag** | Add a `/retrieve` endpoint to serve_rag (today it only has `/query`, which also generates); DDiQ calls it. | S/M | **Stopgap only.** Couples DDiQ's uptime to serve_rag — a 155 GB-RAM host process that *isn't even running right now*. Doesn't solve corpus expansion. Doesn't unify storage. |
| **2. Corpus → pgvector** | Migrate the 9.46M embeddings into Postgres as `halfvec(4096)` + HNSW index; one `lai.retrieval` module both services import. | **L** (one-time migration is multi-hour to multi-day) | **Recommended target.** DDiQ already runs on Postgres; pgvector is already in the stack; `halfvec(4096)` + HNSW *is* the originally documented design intent. Unifies storage, kills the SQLite-as-prod-corpus problem, enables online upserts (= continuous corpus expansion), makes DDiQ↔corpus a plain SQL join. |
| **3. Dedicated vector store (Qdrant)** | Stand up Qdrant as the single retrieval backend for both services. | L+ | Over-engineering for 9.46M vectors right now. pgvector handles this scale. Revisit only if multi-replica scaling becomes the bottleneck. |

### Recommendation

**Target Path 2, but treat it as a Phase-2 prerequisite, not part of feature
work.** The migration + a shared `lai.retrieval` package is the keystone: once
it exists, "DDiQ grounds answers in the legal corpus" (roadmap 2A) drops from a
big unknown to an **M** — just extend `rag_context()` to query a second source.

Important nuance on pgvector + 4096 dims: the `vector` type caps HNSW at 2000
dims (this is why the README says they fell back to exact cosine search). But
the **`halfvec` type supports HNSW up to 4096 dims** — so `halfvec(4096)` + HNSW
is viable and halves vector storage (~77 GB vs 155 GB). This was the README's
stated intent; it was just never finished.

**If a fast interim win is needed:** Path 1 can ship first as a bridge (DDiQ
gets *some* corpus grounding while Path 2 is built), then is retired. But don't
mistake the bridge for the destination.

### Honest cost of Path 2

- One-time migration of 9.46M × 4096 vectors into pgvector + HNSW build: plan
  for hours-to-days of compute, ~80 GB disk for `halfvec`, RAM for the index
  build.
- **Blocked on:** the live Postgres being populated at all (it is currently
  empty — see `AUDIT.md` C3). Phase 0 must land first.
- **Blocked on confirming** whether Step 6 embedding is complete (Part A point 2).

---

# PART C — Deep failure analysis (DDiQ report engine)

### C.1 Report-generation control flow (`_generate_report_core`, `ddiq_report.py:1884`)

19 sequential stages, each writing its own slice of `DDiQReportData`, **with no
reconciliation stage anywhere**:

```
 1. Dedup/fingerprint check        (:1825)
 2. Gather uploaded text           (:1894)  get_all_text_for_docs
 3. Doc metadata rows              (:1898)
 4. LLM metadata pass              (:1905)  — 1 llm_json  [SPOF for project name]
 5. Build shell + checkpoint       (:1922)  projectCenter hardcoded {53.0, 9.0}
 6. Section analysis ×4            (:1933)  — 39 questions = 39+ llm_json calls
 7. Geocode project center         (:1941)  — depends on §6 output
 8. WEA extraction                 (:1947)  — 1 llm_json + per-turbine geocode
 9. Infrastructure                 (:1952)  — 1 llm_json + per-point geocode
10. Cadastral pipeline (13 steps)  (:1959)  — depends on §8 coords; ALKIS WFS
11. Total-capacity regex parse     (:2034)
12. Findings                       (:2045)  — 1 BATCHED llm_json  [SPOF]
13. Timeline                       (:2053)  — 1 llm_json  [SPOF]
14. Cross-doc consistency          (:2061)  — 1 llm_json  [SPOF]
15. Rückbaubürgschaft              (:2069)  — 1 llm_json  [SPOF]
16. Grundbuch match                (:2077)  — 1 llm_json  [SPOF]
17. Promote derived findings       (:2085)
18. Aux-table writes               (:2138)  — single txn, NO ON CONFLICT
19. Return + set fingerprint       (:2199)
```

Stages 7→8→10 form a **silent dependency chain on geocoding**. Stage 14
(`check_cross_doc_consistency`) *looks* like reconciliation but only asks an LLM
to *narrate* contradictions — it computes nothing and corrects nothing.

### C.2 Failure D traced — four un-reconciled turbine counts

The smoke test showed 7/3/10 (text), 11 (capacity math), "10 von 11" (title), 6
(table). Root cause: **four independent derivations, never forced equal:**

| Source | Location | Derives count from |
|--------|----------|--------------------|
| D1 — `overview/Number of WEA` row text | `SECTION_QUESTIONS["overview"]`, `ddiq_report.py:910` | LLM free-text; the prompt *deliberately* asks for errichtet + genehmigt + geplant — so the value contains multiple numbers by design |
| D2 — `parse_wea_count()` | `ddiq_report.py:838-839` | `re.search(r"(\d+)")` — grabs the **first integer in the string**. "7 errichtet, 3 genehmigt" → 7. Structurally broken for multi-number values. |
| D3 — `len(weas)` | `extract_wea_statuses`, `:1318` | A *separate* LLM call with its own context + its own expansion logic (`:1356`); no contract with D1/D2 |
| D4 — `Total Capacity` MW | `:2036`, regex `([\d,.]+)\s*MW` | A capacity figure; stage 14 then feeds D3 *and* D4 to an LLM and asks it to flag mismatches → narrates a fourth "count" |

No code ever asserts D1 = D2 = D3. **Fix:** replace `parse_wea_count` with a
deterministic multi-group parser, and add a reconciliation stage after §8 that
writes `len(weas)` back into the overview row (or flags the delta in code, not
via LLM).

### C.3 Blast radius of the three known failures

- **Findings (A):** the *same* fragile batched-`llm_json` pattern is also in
  `crossDocFindings` (`:1157`). When `generate_findings` returns its fallback,
  derived findings still append — so the chapter looks *near*-empty (1 junk + a
  few derived), and `/reports` computes `finding_count` from
  `jsonb_array_length` (`:2222`), showing "3 findings" in the UI — **the total
  extraction failure is hidden from the browser.**
- **Geocoding (B):** wrong coords propagate to project center → all WEA pins →
  cadastral convex-hull project area → ALKIS query points → clearance zones →
  GeoJSON export. **And it is cached permanently:** `geocode_address` writes the
  bad `address→(lat,lng)` into `ddiq_geocode_cache` with `ON CONFLICT DO NOTHING`
  (`:583`), no TTL, no invalidation. `alkis_query_parcels` caches the resulting
  wrong parcels into `ddiq_parcel_cache` (`:668`). **Re-running the report after
  a code fix still returns poisoned data** until cache rows are manually deleted.
  The cadastral validator only checks a Germany-wide bbox (`cadastral_pipeline.py:752`)
  — a turbine in the wrong German *city* passes validation.
- **Parcels (C):** `make_parcel_polygon` synthetic rectangles can be emitted with
  `source = "ALKIS WFS (GML)"` when ALKIS returns an empty polygon
  (`cadastral_pipeline.py:805`) — **fabricated geometry mislabeled as real
  cadastral data**, a liability issue in a legal product. Parcel `area` for
  regex/LLM parcels is `round(2.0 + (hash(pnum) % 20) / 10, 1)` (`:1516`) — the
  area is a **hash of the parcel number**, pure fiction, not flagged as
  estimated.

### C.4 Adjacent/latent failures the smoke test didn't surface

| Severity | Failure | Evidence |
|----------|---------|----------|
| Medium | `_parse_alkis_feature` Flur/Area loops have **inverted control flow** — `except (...): pass; break` puts `break` in the except clause, so on parse *success* the loop continues and a later matching key overwrites the earlier value; on *failure* it breaks. *(Severity downgraded from Critical: the bug only manifests when multiple candidate keys are simultaneously present in one ALKIS feature — most records have only one, so the function returns the right value in practice. Real bug, limited blast radius. See `RE_VERIFICATION.md` B6.)* | `ddiq_report.py:705, 712` |
| Critical | `llm_json` double-failure is **uncaught** — if the one retry also returns non-JSON, `json.loads(raw2)` raises and propagates out of `llm_json`. | `ddiq_report.py:516-523` |
| High | TOCTOU race on `request_fingerprint` dedup — lookup + INSERT not atomic; the index is **not UNIQUE** (`:140`). Two identical concurrent requests both launch full 30–60 min pipelines. | `:1825-1848`, `:140` |
| High | Sync `/report/generate` sets `request_fingerprint` only *after* the whole pipeline finishes — during the 30–60 min run the row is invisible to dedup. | `:2199-2206` |
| High | Sync path has no try/except around `_generate_report_core`; a mid-pipeline crash leaves the last checkpoint row at its column-default `status='done'` — `/reports` lists a half-built report as complete. | `:133`, `:2199` |
| High | Aux-table writes (`ddiq_project_areas`, `ddiq_contracts`, `ddiq_classified_parcels`) have no `ON CONFLICT`; a re-run duplicates rows (code comment admits it). | `:2138-2170` |
| Medium | Evidence rollup silently drops out-of-range LLM indices — a finding can end up with **zero evidence and no warning**, defeating the "click to source" guarantee. | `:557`, `:1620` |
| Medium | `_evidence`/`_anchor` stashed on `row.__dict__` are **not serialized** by Pydantic `.dict()` — the checkpointed JSONB loses evidence; only the in-memory object has it. | `:1028`, `:1793` |
| Medium | OCR fallback triggers on `len(text) < 50` per page — short legitimate pages get needless OCR; pages with 50+ chars of *garbage* extraction never get OCR'd. No quality gate. | `:417` |

### C.5 The LLM-call layer + compounding failure math

`llm_call` / `llm_json` (`ddiq_report.py:504-523`): `max_tokens` 2048/4096,
**no `<think>`-trace stripping** (DDiQ uses the 27B thinking-mode model and
never strips reasoning tokens — a likely cause of malformed JSON), exactly **one**
retry inside `llm_json` and **zero** retry in `llm_call`, 300 s hard timeout,
minimal JSON salvage (strips code fences only).

**One full report ≈ 45 LLM calls** (1 metadata + **37** sections + 1 WEA + 1 infra
+ 1 cadastral-contract + 1 findings + 1 timeline + 1 cross-doc + 1 Rückbau + 1
Grundbuch), plus ~37 embed calls + ~37 rerank calls. **8 of those LLM calls are
single points of failure for an entire report chapter** (the section pass
degrades gracefully per-row; the other 8 do not). *(Original draft said 10
critical SPOFs and ~49 calls; recounted in `RE_VERIFICATION.md` B4/B5 — the
actual section question count is 37 and the SPOF passes are 8.)*

If each critical call has probability `p` of returning usable JSON within its
one retry, the chance *all 8* succeed is `p^8`. At a generous `p = 0.97` that
is `≈ 0.78` — **roughly 22% of reports lose a whole chapter** purely to
LLM-JSON fragility, before counting geocoding/ALKIS/network failures. The smoke
test showing 6 failures in one report is consistent with this math, not an
outlier. *(p is illustrative, not measured.)*

### C.6 The reconciliation gap — DDiQ vs. the analyzer package

**Confirmed: DDiQ imports nothing from `lai.analyzer`** (import block
`ddiq_report.py:12-31` — only `cadastral_pipeline`). Yet `lai/analyzer/reconciler.py`
is exactly the missing pattern, built for the contract analyzer: it does the
arithmetic *in Python* (`reconcile_table`, `:184`), classifies severity by
deterministic bands (`_classify`, `:152`), has a robust `parse_german_number`
(`:24`) — and its docstring states the principle outright: *"The LLM never does
the arithmetic; it only interprets the findings this module produces."* DDiQ
does the opposite everywhere: it asks the LLM to extract *and* judge *and*
reconcile. The sounder pattern exists in the codebase; it was never back-ported.

---

# PART D — Deep architecture analysis

### D.1 The three-way code split + duplication

LAI has **three parallel codebases** plus a dead fourth:
`LAI/src/lai/` (serve_rag + analyzer + pipeline) · `LAI/micro-services/` (DDiQ) ·
the dead `api/main.py` domain stack (~3,200 LOC, imported by nothing — recounted in `RE_VERIFICATION.md` B2).

Genuinely duplicated logic (~1,500–2,000 LOC), each implemented 2–4×:

| Logic | Copies | Where |
|-------|--------|-------|
| PDF extraction + OCR | 3 live | `serve_rag.py:570`, `api.py:206`, `ddiq_report.py:413` |
| Text chunking | 2 live + pipeline | `api.py:232`, `ddiq_report.py:424`, `pipeline/utils/` |
| Embedding client | 3 live | `eval.py:163`, `api.py:167`, `ddiq_report.py:438` (byte-identical to api.py) |
| Reranker client | 2 live | `api.py:341`, `ddiq_report.py:496` (+ in-process `eval.py:239`) |
| LLM client | 4 live | `serve_rag.py:415`, `analyzer/llm_client.py:43`, `api.py:356`, `ddiq_report.py:504` |
| Lenient JSON parse | 3 | `serve_rag.py:462`, `ddiq_report.py:516`, analyzer (guided decoding) |
| Session memory | 3 | `persistence.py` (SQLite), `api.py:121` (in-RAM dict, lost on restart), unused tables in `pipeline_local.db` |

### D.2 Two unrelated retrieval stacks

- **serve_rag:** `search/eval.py` — loads 9.46M × 4096 fp32 into a 155 GB NumPy
  array; dense = brute-force `embs @ q_vec` (no ANN index); BM25 = SQLite FTS5;
  RRF fuse; in-process Qwen3-Reranker-8B. Over SQLite. Searches the **full legal
  corpus**.
- **DDiQ:** `rag_context` → `search_doc_chunks` — pgvector `<=>` over
  `ddiq_doc_chunks` filtered to the uploaded `doc_ids`; HTTP reranker container.
  Over Postgres. Searches **only the user's uploaded docs**.

They share no store, no schema, no code. **There is no shared retrieval
abstraction to extend** — `eval.py` is an eval harness doing double duty as
production retrieval. Unifying them (Part B Path 2) requires creating a
`lai.retrieval` package first.

### D.3 Storage: SQLite-as-prod-corpus is drift, not design

`cli.py:869` is literally titled "Step 6: Embeddings → pgvector" and the
`.env.example` says the same — but the code writes to SQLite (`child_embeddings`
table in `pipeline_local.db`). The intended design was pgvector; SQLite was the
offline eval/training artifact format and was never promoted to a real serving
store. Consequences: single writer (pipeline vs. live reader contend), full
155 GB RAM reload on every restart, no incremental load, no horizontal scaling
(2 replicas = 2× 155 GB RAM, each brute-force-scanning), whole-file backup only.

### D.4 LLM serving = single point of failure

Every generation across **all three products** — RAG chat, contract analyzer,
and the 30–60 min DDiQ report (≈45 LLM calls) — hits the **one** Qwen3.6-27B
container. If it OOMs/restarts, all three product surfaces go down at once. Same
for the single embedding container. `--max-num-seqs 8`, single uvicorn worker on
serve_rag, `ThreadPoolExecutor(max_workers=2)` on DDiQ. Realistic ceiling:
**3–8 concurrent interactive users**; DDiQ reports are effectively a serialized
2-wide batch job that **does not survive a restart** (`ddiq_report.py:2432`
admits it).

### D.5 Extensibility for the roadmap additions

| Roadmap item | Effort | Insertion point |
|--------------|--------|-----------------|
| **(a) Legal-corpus grounding into DDiQ** | M *after* an L prerequisite | `rag_context()` (`ddiq_report.py:525`) is the clean single funnel — but the second source must exist first (Part B). Cheap hook, expensive thing to hook into. |
| **(b) External registry connectors (MaStR, Handelsregister)** | M *if* a small refactor is done first | ALKIS + Nominatim are the de-facto template but **not abstracted** — hand-rolled functions in the 2,463-line god-file. Extract a `lai/connectors/` package with a `Connector` ABC + registry, refactor ALKIS/Nominatim into it, then MaStR/Handelsregister slot in. Otherwise it's death-by-god-file. |
| **(c) Feedback loop (capture)** | S | A `lai_feedback` table **already exists** (unused) in `pipeline_local.db`; message-id plumbing exists (`persistence.add_message`). A `POST /feedback` endpoint is genuinely small. Closing the loop is separate, larger. |

---

# PART E — What this means for Phase 2 sizing

1. **Q3 resolved:** corpus = `pipeline_local.db` (350 GB, 9.46M embeddings,
   4096-dim). `app.db` is a stale snapshot. *Confirm whether Step 6 embedding is
   complete* before committing Phase 2 numbers.
2. **Q1 resolved:** target **corpus → pgvector (`halfvec(4096)` + HNSW)** with a
   shared `lai.retrieval` package. Optional Path-1 HTTP bridge for an interim
   win.
3. **The keystone is now explicit:** the corpus migration + `lai.retrieval`
   package is a Phase-2 **prerequisite (effort L)**. It simultaneously unblocks
   roadmap 2A (DDiQ grounding), continuous corpus expansion, the SQLite-as-prod
   problem, and horizontal scaling — four problems collapse into one project.
4. **Phase 1 gains scope from Part C:** beyond the 6 smoke-test failures, add the
   deterministic reconciliation stage (fixes failure D + its whole class), the
   `_parse_alkis_feature` control-flow fix, `llm_json` hardening (`<think>`
   stripping + JSON salvage + caught double-failure), cache TTL/invalidation,
   and the transactional-soundness fixes (UNIQUE fingerprint index, `ON
   CONFLICT` on aux writes, sync-path crash handling). These are what move the
   "~22%-of-reports-lose-a-chapter" rate down.
5. **Connectors need a refactor-first** (`lai/connectors/` package) or Phase 2B
   becomes unmaintainable.
6. **Phase 0 — outage gate RESOLVED (2026-05-14):** the live Postgres was empty
   at first probe; since resolved — full runtime stack now healthy. The
   corpus migration and everything DDiQ-related depend on it being populated.

*Unverified items flagged above:* whether Step 6 embedding is complete vs. in
progress; the illustrative `p` in the compounding-failure math; whether
serve_rag's runtime concurrency matches the code-inferred model (serve_rag is
not currently running).
