# LAI — Final Consolidated Report

**Date:** 2026-05-14 (with corrections through 2026-05-15)
**Scope:** Senior software audit, deep architecture and failure analysis,
market-research verification, and a re-sequenced engineering plan.
**Method:** Direct code reading with `file:line` citations, live-system probing,
PDF re-reads, web verification of all competitive claims. Where a claim is
estimated rather than measured, it is marked. Where the system state has
*changed during the audit*, the change is logged explicitly.

Eight prior working documents are consolidated here: `AUDIT.md`,
`TECH_STACK.md`, `DEEP_RESEARCH.md`, `VERIFICATION.md`, `ARCHITECTURE_BRIEF.md`,
`DDIQ_ROADMAP.md`, `RE_VERIFICATION.md`, `ISSUES_FIXES_METHODS.md`. Each remains
available; this is the single source of truth.

---

# 0. Executive summary

LAI is a German legal-AI platform for wind-energy due diligence — a real corpus
(672 GB), strong models (Qwen3.6-27B / Qwen3-Embedding-8B / Qwen3-Reranker-8B),
a working extraction pipeline, and demonstrably sophisticated legal reasoning
(the smoke-test report correctly parsed a complex OVG ruling and caught a real
cross-document inconsistency).

It is **not** ready to compete or to be shown to clients. The smoke-test report
printed "Manual review required (findings extraction failed)" inside a client
deliverable, geocoded all six turbines to the city of Bremen (~65 km from the
actual Lamstedt site), produced four contradictory turbine counts in the same
report, and rendered 25 of 37 sections as defensive "no information" paragraphs.

**Verdict on the architecture: the pieces are solid; the wiring is not.** Three
parallel codebases, ~3,200 lines of dead code, ~1,500–2,000 lines duplicated,
storage split across SQLite and Postgres by drift, no validation layer, no
reconciler, no auth, no tenant isolation, and a DD engine that cannot reach the
672 GB legal corpus it sits next to. **The fix is structural re-wiring, not a
rewrite.** None of the good pieces above need to be thrown out.

**The plan**, respecting the boss's locked decisions (on-prem only, no further
budget, feedback-loop learning, DDiQ engine first, no specific competitor
benchmark):

1. **Phase 0** — close the open security gaps (auth + tenant isolation) and
   pick one authoritative deployment model. *(The runtime outage finding from
   the initial audit has since been resolved — see §2.)*
2. **Phase 1** — consolidation (`lai.common`, delete the dead stack) followed
   by two parallel tracks: reliability fixes to the DDiQ engine **and** the
   keystone migration (corpus → pgvector + a shared `lai.retrieval` package).
3. **Phase 2** — once the keystone lands, ground every section in the legal
   corpus and add free public-registry connectors (Marktstammdatenregister,
   Handelsregister, expanded ALKIS).
4. **Phase 3** — feedback loop: lawyer corrections captured into a correction
   memory that few-shots into future prompts. The "should learn" piece.

**Strategic correction:** "local models on-prem" is *not* a defensible moat —
Legartis already ships fully sovereign legal AI in Switzerland with GDPR + ISO
27001 certification. LAI's defensible ground is **vertical depth in German
wind-energy due diligence** — the cadastral plumbing across 12 federal-state
ALKIS endpoints, the 10H setback logic, the 672 GB German legal corpus, the
statutory-anchor extraction prompts. The honest positioning is *best at one
thing*, not *Harvey alternative*.

**The "no contact a lawyer" requirement** splits cleanly: removing the
in-analysis hedge language rides along in Phase 1 (low-risk, prompt + cleanup
layer). Removing the formal liability footer and adopting lawyer-replacement
positioning is the **roadmap's success condition**, not a step — it requires
the reliability, citation-integrity, grounding, coverage, and improvement
gates all met, plus counsel sign-off on the German RDG question.

**Success bar** (re-run the Lamstedt smoke test and grade on these eight):
non-empty findings chapter; turbines on the correct map; one consistent
turbine count; every "missing" item cited to its governing statute; one
language throughout; no hedge filler; every fact carries a visible source
tag; tables render cleanly (already true). Eight items, all measurable.

---

# 1. What LAI is

LAI is built from **three deployable units** plus a batch data pipeline, over a
shared model + data layer:

| Unit | Role | Port |
|------|------|------|
| `serve_rag` (host process) | Conversational chat / RAG / contract analyzer | 18000 |
| `lai-backend` / DDiQ (Docker) | Multi-document due-diligence report generator | 18001 |
| `LAI-UI` (React/TS, own repo) | Web frontend | 5173 (dev) |
| `lai.pipeline` (CLI) | 6-step batch corpus build | — |

Shared layer (all running on 2× NVIDIA RTX PRO 6000 Blackwell, 96 GB VRAM each):

- **`lai_analyzer_llm`** — Qwen3.6-27B via vLLM, GPU 0, port 8005, thinking
  mode + prefix caching
- **`lai_embedding`** — Qwen3-Embedding-8B via vLLM, GPU 1, port 8003, 4096-dim
- **`lai_reranker`** (in-process) — Qwen3-Reranker-8B in `serve_rag.py`; DDiQ
  reaches an external reranker container on `:8004` via HTTP
- **`lai_postgres_main`** — `pgvector/pgvector:pg16`, port 5434 (DDiQ tables)
- **`lai_redis`** — `redis:7-alpine`
- **SQLite** — `LAI/processed/pipeline_local.db` (350 GB) holds the legal corpus
  that `serve_rag` reads at startup

Runtime topology:

```
                ┌──────────────┐
   browser ───▶ │   LAI-UI     │ ─┐
   :5173        │   (React)    │  │
                └──────────────┘  │ talks to TWO backends, different contracts
                                  │
         ┌────────────────────────┴──────────────────────┐
         │                                               │
   ┌─────▼─────────────────┐               ┌─────────────▼──────────────┐
   │  serve_rag :18000     │               │  lai-backend (DDiQ) :18001 │
   │  host process, GPU 1  │               │  Docker container          │
   │  + in-proc reranker   │               │  ThreadPoolExecutor worker │
   │  155 GB RAM corpus    │               │  reranker via HTTP :8004   │
   └──┬─────────┬──────────┘               └──┬───────────┬─────────────┘
      │         │                              │           │
      ▼         ▼                              ▼           ▼
  SQLite     vLLM embedding/analyzer       Postgres    ALKIS WFS
  corpus     :8003 / :8005                 pgvector    Nominatim
  350 GB                                   :5434

  (DDiQ does NOT touch the SQLite corpus — see §6.)
```

---

# 2. Current state (verified live)

The "live system is down" finding from the initial audit (`AUDIT.md` C3) **was
true at first probe**: `lai-backend` was up-but-crashed, unable to resolve
`lai_postgres_main`; `serve_rag` was not running; the host-process Postgres on
port 5435 was empty. That state has been **resolved during the audit window**:

| Service | Status (re-verified 2026-05-14) |
|---------|----------------------------------|
| `lai_postgres_main` | Up ~1 hour, healthy, 5434 |
| `lai_embedding` | Up ~1 hour, healthy, 8003 |
| `lai_analyzer_llm` | Up ~1 hour, healthy, 8005 |
| `lai_redis` | Up ~1 hour, healthy |
| `lai-backend` | Up 25 hours, **healthy** (`/health` 200) |
| `serve_rag.py` | Running (PID 3413088, port 18000), `/health` returns `{"ok":true,"loaded":true,"llm_model":"qwen3.6-27b","n_sessions":12}` |
| `lai_neo4j` | Up 25 hours, healthy (origin/purpose not yet documented) |
| `lai-user-doc-processor` | Up 25 hours, **unhealthy** (HTTP 500 on /health) |

The full intended runtime topology now matches `LAI/docker-compose.yml`. The
deployment drift that *caused* the outage (Docker DNS name vs host-process
fallback) is still a latent risk — pick one model, make it authoritative.

`lai_postgres_main` (re-verified) contains the 9 DDiQ tables, no corpus tables.
The corpus continues to live in `pipeline_local.db` as SQLite, **not** in
Postgres — that drift is unchanged. The corpus migration into pgvector is the
Phase 1b keystone in §10.

---

# 3. The smoke-test failures — verified

The boss reviewed a DD report (`LAI/docs/smoke_test_report.pdf`, Windpark
Lamstedt, generated 2026-04-29). Six failures were verified by re-reading the
PDF and tracing the root cause in code. (Two additional claims from a separate
critique document were checked against the same PDF and **did not match the
file** — see §15.)

| # | Failure | Evidence | Root cause (verified in code) |
|---|---------|----------|-------------------------------|
| A | **"Manual review required (findings extraction failed)"** printed in the client deliverable. Action Items chapter contains essentially nothing. | PDF page 14 | `generate_findings()` makes one batched `llm_json()` call over all flagged rows; any malformed JSON → bottom `except` → placeholder. `ddiq_report.py:1543-1648`. The 27B in thinking-mode at `max_tokens=4096` very likely blew the token budget or returned invalid JSON. |
| B | **All six turbines geocoded to Bremen Überseestadt (~65 km from Lamstedt).** | PDF map p.13; coords 53.094 / 8.785 (Bremen), Lamstedt is 53.622796 / 9.147855 (web-verified) | `geocode_address()` (`ddiq_report.py:571`) passes whatever the LLM returned for the location field — *a paragraph* — verbatim to Nominatim with `limit=1`, no plausibility check, no bounding-box validation, first hit accepted. |
| C | **Cadastral parcels wrong / mislabeled.** Table shows 3 parcels (10/2, 26/3, 44/5); the body references 9 different Flurstücke. Synthetic estimated polygons render with `source="ALKIS WFS"`. | PDF p.6 vs p.12 | Knock-on from B: ALKIS WFS queried at wrong (Bremen) coords → empty → fall through to `make_parcel_polygon()` synthetic rectangles (`ddiq_report.py:1481-1486`). Parcel area is computed as `round(2.0 + (hash(pnum) % 20) / 10, 1)` — a hash of the parcel number, fictitious. |
| D | **Four conflicting turbine counts in one report:** 7/3/10 in text, 11 in capacity math, "10 von 11" in title, 6 in table. | PDF pp.2-3, p.12, title | Four independent derivations, never reconciled. `parse_wea_count` (`ddiq_report.py:838-839`) is `re.search(r"(\d+)", value)` — grabs the first integer from a value the prompt deliberately fills with multiple numbers. `extract_wea_statuses` produces `len(weas)` with its own context window and its own expansion logic. `check_cross_doc_consistency` narrates contradictions via LLM instead of reconciling. No code forces D1=D2=D3. |
| E | **Action Items near-empty.** | PDF p.14 | Direct consequence of A. |
| F | **WEA "Address" column = full paragraph.** "Bundesland: Niedersachsen…; Landkreis: Cuxhaven; Gemeinde: nicht explizit…" appears as the address value. Identical paragraph repeated 6× across 6 turbines. | PDF p.13 | The LLM was asked to return `{address: "municipality, state"}` but produced the paragraph. The system displays it untouched and also feeds it to the geocoder, which is the trigger for B. |

**Plus, from re-reading the report:**

- "**Denglisch**" — English category labels ("Number of WEA", "Project Status"),
  German content, mixed mid-sentence ("Formal permit status: An
  Änderungsgenehmigung…"). Confirmed.
- "**Defensive paragraphs**" — *"Die vorliegenden Kontextausschnitte enthalten
  keine Angaben…"* appears in 25 of 37 sections. By design, not a bug: the DD
  engine only reads the 4 uploaded PDFs and cannot reach the 672 GB legal
  corpus. See §6.

---

# 4. The compounding-failure math

A full DD report makes **~45 LLM calls** (37 section questions + 1 metadata +
1 WEA + 1 infra + 1 cadastral contract + 1 findings + 1 timeline + 1 cross-doc
+ 1 Rückbau + 1 Grundbuch), plus ~37 embedding calls and ~37 reranker calls.

The 37 section calls degrade gracefully (per-row fallback). **Eight of the
remaining passes are single points of failure for an entire report chapter.**

If each critical pass returns usable JSON within its single retry with
probability `p`, the chance *all eight* succeed is `p^8`. At a generous `p =
0.97`, `p^8 ≈ 0.78` → **roughly 22% of reports lose a whole chapter** to
LLM-JSON fragility alone, before counting geocoding/ALKIS/network failures. At
`p = 0.90`, the rate is ~43%. The smoke test catching six failures in one
report is consistent with this math, not an outlier.

*Note: `p` is illustrative, not measured. The structural claim (8 SPOF passes,
section pass graceful) is verified in the control flow.*

This is the most important quantitative finding. The fix — `<think>`-trace
stripping + schema-enforced output via vLLM guided decoding + caught
double-failure (§9.5) — moves `p` close to 1.0 and essentially eliminates the
chapter-loss rate.

---

# 5. Is the current architecture solid?

**The pieces are solid. The wiring is not.** This is the central architectural
finding and it determines the response: re-wire, do not rewrite.

### What's solid (do not throw away)

- **The model choices.** Qwen3.6-27B analyzer, Qwen3-Embedding-8B (#1
  multilingual MTEB, 4096-dim), Qwen3-Reranker-8B — all locally hosted via
  vLLM. Strong, modern, multilingual.
- **The legal-reasoning capability.** The smoke test correctly parsed a
  complex OVG ruling, distinguished BImSchG §§4/6/10/15 statuses, and caught a
  real cross-document inconsistency (E-79 vs E-70 between permit and
  maintenance contract). That quality of reasoning is hard to fake and worth
  preserving.
- **The 672 GB German legal corpus** — real, parent/child-chunked, domain-
  classified, contextually enriched. 9.46M chunks already embedded; another
  ~40.5M still pending (Step 6 incomplete — see §16).
- **The data pipeline** — six idempotent steps with two-stage SIGINT handling,
  keyset-pagination resume, dual-write NPZ backups, schema versioning. Above
  typical research-code maturity.
- **The cadastral / wind-energy domain depth** — 12 federal-state ALKIS WFS
  endpoints, INSPIRE CadastralParcel schema, 10H rule, 13-step parcel workflow.
  Competitor depth no generalist has.
- **The `analyzer` package design pattern** — `reconciler.py` does the
  arithmetic in code, lets the LLM only interpret. Exactly the right
  philosophy. Just not adopted by DDiQ yet.
- **The DDiQ database schema** — 9 sensibly normalized tables.

### What's not solid (the wiring)

- The DD engine cannot see the legal corpus — the two halves of the product
  are siloed (`ddiq_report.py:475`).
- Three parallel codebases — `LAI/src/lai/` (chat + pipeline + analyzer),
  `LAI/micro-services/` (DDiQ), and a dead third (`api/main.py` +
  `search/{routes,…}` + `generation/` + `auth/` + `documents/` + `extraction/`
  + `infra/`) imported by nothing.
- ~**3,200 LOC dead** + **~1,500–2,000 LOC duplicated** across the live codebases
  (PDF extract 3×, embedding client 3×, reranker client 2×, LLM client 3×,
  JSON salvage 3×, session memory 3×).
- Storage split across SQLite (corpus) and Postgres (DDiQ) — drift, not design
  (`cli.py:869` is literally titled "Step 6: Embeddings → pgvector" but writes
  SQLite).
- No validation/guardrail layer between AI output and the user-facing report.
- No reconciler — multiple passes can produce contradictory numbers that all
  make it into the document.
- No retrieval router — `rag_context()` is hard-wired to one source.
- No connector abstraction — ALKIS and Nominatim are bolted into the 2,463-line
  `ddiq_report.py` god-file.
- No authentication or tenant isolation — every user sees every report.
- One GPU model serves every product surface — a single restart kills chat,
  contract analyzer, and DDiQ together.

**Verdict: competent in pieces, incoherent as a system.** The remediation is
structural re-wiring (and five new components — §11) plus the consolidation
(§12).

---

# 6. The architectural facts that explain the failures

### 6.1 DDiQ is siloed from the legal corpus

`search_doc_chunks` in `ddiq_report.py:475` queries `FROM ddiq_doc_chunks …
WHERE doc_id = ANY(doc_ids)` — *only* the user's uploaded PDFs. The 672 GB
corpus is unreachable from the DD engine.

This is why 25 of 37 sections returned "no information": the system is working
as designed — it can only see the 4 uploaded files in the data room. The fix
is the keystone migration (§10).

### 6.2 Storage is split by drift, not design

| | Today | Should be |
|--|------|-----------|
| Legal corpus (9.46M embeddings, 4096-dim) | SQLite `pipeline_local.db`, 350 GB, loaded entirely into 155 GB RAM at startup | Postgres `pgvector` `halfvec(4096)` + HNSW (the README's documented intent — never finished because `vector` caps HNSW at 2000 dims; `halfvec` supports 4096) |
| DD engine data (`ddiq_*` tables) | Postgres `lai_postgres_main` | Same |
| Sessions / chat history | SQLite `sessions.db` | Either |

Two engines for one logical product. The README itself flags the original
intent (pgvector), and the implemented reality (SQLite-as-RAM-loader) is
treated by `cli.py:869` as a placeholder. Migration is overdue.

### 6.3 The DD engine has no fault tolerance discipline

- `request_fingerprint` index is `CREATE INDEX` (not `CREATE UNIQUE INDEX`)
  — TOCTOU race: two identical concurrent requests both queue a 30–60 min
  pipeline (`ddiq_report.py:140`).
- Sync `/report/generate` sets the fingerprint only *after* the pipeline
  completes — during the 30–60 min run dedup misses it (`:2199-2206`).
- Sync path has no try/except around `_generate_report_core` — a mid-pipeline
  crash leaves the row at column default `status='done'`, and `/reports`
  shows it as complete (`:133`).
- Aux-table writes (`ddiq_project_areas`, `ddiq_contracts`,
  `ddiq_classified_parcels`) have no `ON CONFLICT` — retries duplicate rows
  (`:2138-2170`; the comment at `:2133` admits it).
- `ddiq_geocode_cache` and `ddiq_parcel_cache` have no TTL — a wrong coord
  poisons forever.
- `llm_json` double-failure is uncaught — the retry's `json.loads` raises and
  propagates (`:516-523`).
- `_parse_alkis_feature` has an inverted control flow at lines 705/712 — on
  parse *success* the loop continues and a later matching key can overwrite;
  on *failure* it breaks. Severity Medium; only manifests when multiple
  candidate keys are simultaneously present.

### 6.4 Security is effectively absent

- **Backends:** no authentication on any endpoint, no tenant isolation, no
  `user_id` columns on DDiQ tables. Every user sees every report. **GDPR
  blocker.** (`serve_rag.py:944-1460`, `ddiq_report.py:1655-2463`.)
- **Frontend:** `AuthContext.tsx:55-82` accepts any credentials, mints an
  *unsigned base64* "token" in the browser (`utils/jwt.ts:26-36`), and never
  sends it to a backend. Comments in the file say so.
- **CORS** `allow_origins=["*"]` on both backends, paired with the absent
  auth.
- **A live HuggingFace token** sits in plaintext at
  `Docker/inference_engine/.env:11`. Gitignored, never reached the repo —
  but on a shared multi-project box. **Rotate.**
- **Default credentials** (`lai_test_password_2024`, etc.) appear as fallback
  defaults in ~9 compose files and `core/config.py` — if `.env` is ever
  missing, the app runs on known creds.

### 6.5 Operational gaps

- **Zero automated tests** anywhere — `LAI/tests/{unit,integration,e2e}` and
  the frontend `src/` all empty of test files.
- **Monitoring stack configured but not deployed** — `Docker/monitoring/` has
  a valid `prometheus.yml` but no Prometheus or Grafana container runs;
  scrape targets don't match container names. `prometheus-client` is a
  declared dep but no `/metrics` is exposed.
- **No streaming** on `/query` — `sse-starlette` is declared but unused. Users
  wait for the full answer.
- **Image versions unpinned** — `vllm:latest`, `prometheus:latest`,
  `grafana:latest`, `minio:latest`. `vllm:latest` has already broken a CLI
  flag once (documented in `start-host.sh`).
- **DDiQ microservice requirements unpinned** (`>=` ranges with no lockfile);
  the core `LAI/` package has `uv.lock` but the deployed service doesn't use
  it.

### 6.6 Citation / data quality

- **No live citation verification.** The `generation/citation_verifier.py`
  lives in the dead code stack and is imported by nothing. Live `serve_rag.py`
  and `ddiq_report.py` accept whatever `citations` array the LLM emits.
- **15.8% of synthetic training-data citations are fabricated** by the 72B
  teacher (project's own `audit_results.json`: 84.16% verified). Fine-tuning
  is shelved as a result — the correct call.

---

# 7. The 5 critical / high findings (priority order)

Re-stated from §6, ranked by what blocks shipping:

1. **No auth, no tenant isolation** — GDPR breach; blocks any second customer.
2. **The DD engine cannot reach the legal corpus** — root cause of "no
   information" in 25/37 sections. Architectural, not a bug.
3. **`generate_findings()` is a single point of failure** for the report's
   most important chapter — the smoke test demonstrated it.
4. **Geocoding has no plausibility gate** — produced the Bremen failure with
   cascading damage to ALKIS, the project polygon, and the map.
5. **No reconciler** — the same fact derived four ways with no ground truth.

These five plus the schema-enforced LLM output (§11) account for every
verified smoke-test failure plus the ~22% chapter-loss rate.

---

# 8. The target architecture

```
   ┌──────────────────────────────────────────────────────────────┐
   │  LAI-UI (React) — served ON-PREM, behind real auth           │
   └───────────────────────────┬──────────────────────────────────┘
                               │  one backend contract
                ┌──────────────▼──────────────┐
                │  Unified LAI backend         │
                │  • chat / RAG                │
                │  • DDiQ (as a router)        │
                │  • shared lai.common         │
                │  • a RETRIEVAL ROUTER in     │
                │    front of every pass        │
                │  • a VALIDATION LAYER         │
                │    before every render        │
                └──────────────┬───────────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        │                      │                          │
   ┌────▼─────────┐   ┌────────▼──────────┐   ┌───────────▼────────┐
   │ ONE database  │   │ Public connectors │   │ Feedback store     │
   │ Postgres +    │   │ • MaStR (free)    │   │ — lawyer           │
   │ pgvector      │   │ • ALKIS (free)    │   │   corrections      │
   │ halfvec(4096) │   │ • Handelsregister │   │   captured & fed   │
   │ + HNSW:       │   │   (free)          │   │   back as          │
   │ • corpus      │   │ • OSM (free)      │   │   few-shot context │
   │ • uploaded    │   └───────────────────┘   └────────────────────┘
   │   docs        │
   │ • all in ONE  │      AI models: same 2 GPUs;
   │   place       │      corpus served by Postgres, not RAM.
   └───────────────┘
```

The four structural moves:

1. **Unify storage** — move the corpus from SQLite into Postgres + pgvector.
   Kills the 155 GB RAM-load, cold-restart, and the silo problem.
2. **Unify the code** — delete the dead stack, extract `lai.common`. Every
   fix lands once.
3. **Add a retrieval router** — one funnel every extraction pass calls. Per
   question, decides which sources to hit, returns chunks with provenance.
4. **Add the missing layers** — real auth + tenant isolation, and a feedback
   store.

---

# 9. The complete issue catalog (organized)

Each entry: **what** the issue is → **fix** → **how**. Severities are honest.

### 9.1 Report output quality (the smoke-test failures)

| Issue | Fix | How |
|---|---|---|
| Findings chapter prints "Manual review required" | Per-finding iteration + schema-enforced output | Replace the single batched `llm_json()` in `generate_findings()` with a loop; use vLLM **guided decoding** with a Pydantic JSON schema so the model is *forced* to return valid JSON. Strip `<think>` traces. Per-call retry. Partial success keeps the chapter alive. |
| Turbines geocoded to Bremen | Validation gate on every geocode | A location-normalization pass returns structured fields, never paragraphs. Plausibility gate: Nominatim hits must fall in the named Landkreis bbox AND clear an importance threshold; low-confidence is marked `unverified`. TTL on `ddiq_geocode_cache` so bad coords don't poison forever. |
| Parcels mislabeled / synthetic shown as ALKIS | Provenance as a first-class field | Every fact carries a typed source enum: `uploaded_doc / legal_corpus / registry / estimated / unverified`. Synthetic polygons cannot carry an ALKIS tag; renderer enforces honest labels. Hash-derived `area` becomes `None` when ALKIS didn't return one. |
| Four conflicting turbine counts | Deterministic reconciler | After all extraction passes, a new in-code stage replaces `parse_wea_count` with a multi-group parser; forces `len(weas)` back into the overview row; emits one canonical count. Contradictions become *one* finding, not four printed numbers. Port the `analyzer/reconciler.py` philosophy. |
| 25 of 37 sections "no information" | Statutory grounding + structured "missing" state | Retrieval router pulls the relevant statute from the corpus per section, so a gap becomes *"§35(5) BauGB requires a Rückbaubürgschaft — absent from the data room, request from client"*. "Missing" is a typed state, rendered as a red-flag icon, not a paragraph. |
| Same paragraph 6× | Canonical facts ledger | One reconciled `ProjectFacts` object every render reads from. Repetition disappears structurally. |
| Address column = paragraph | Structured location model | Separate displayed string from geocoded fields. |
| Denglisch | Single-language enforcement in the validation layer | One language per report, configurable. Mid-sentence switches detected and re-prompted. |
| Reflexive "consult a Fachanwalt" filler | Output cleanup pass + tighter prompts | Strip disclaimer-class phrases. System prompts say "decisive Fachanwalt, do not refer to other lawyers." The formal footer stays until the reliability bar is met AND counsel clears the RDG question (§14). |
| WEA specs (hub/rotor/power) often `null` | Dedicated specs prompt + Docling table mode | New focused prompt targets the numeric spec table; use Docling's `TableFormerMode.ACCURATE` for datasheets. |

### 9.2 The DD engine cannot reach the corpus (the keystone)

| Issue | Fix | How |
|---|---|---|
| `search_doc_chunks` queries `ddiq_doc_chunks` only | Unify retrieval | Migrate the 9.46M-embedding corpus from SQLite `pipeline_local.db` into Postgres + pgvector as `halfvec(4096)` + HNSW. DDiQ becomes a plain SQL join. |
| Two storage engines by drift | One Postgres | Above. `pipeline_local.db` retires as a serving store. |
| Step 6 ~81% incomplete (40.5M chunks pending `embedding IS NULL`) | Finish or stream | Decision: finish-before-migrate vs migrate-9.46M-now-and-stream-forward. `resume_step6.sh` is the existing runner. |
| `rag_context()` hardwired to one source | Retrieval router | Per question, route to {uploaded docs, legal corpus, public registries}; return ranked chunks with provenance. Becomes the single funnel every analysis pass calls. |
| No external public data queried | `lai/connectors/` plugin layer | `Connector` ABC; refactor ALKIS + Nominatim into it; add Marktstammdatenregister (free — fixes WEA-count and EEG status), Handelsregister (free — fixes missing HRB), OSM. All free/public — fits the no-budget constraint. Grundbuch stays a "request from client" action item. |

### 9.3 Codebase fragmentation

| Issue | Fix | How |
|---|---|---|
| ~3,200 LOC dead code | Delete | After grep-confirmation, pure delete. Salvageable patterns (JWT validation logic from `auth/jwt.py`, citation-verifier design from `generation/`) port into the new `lai.common`. |
| ~1,500–2,000 LOC duplicated | One shared library | Extract `lai.common`: `PdfExtractor`, `Chunker`, `EmbeddingClient`, `RerankerClient`, `LlmClient` (with `<think>` strip + schema-enforced output + `tenacity` retries), `JsonSalvage`. Every fix lands once. |
| `ddiq_report.py` is a 2,463-line god-file (12 endpoints, 9 inline DDLs, 8 extraction passes, ALKIS client, parsers, LLM orchestration) | Decompose | Split (no logic change): `db.py`, `models.py`, `extractors/`, `routes.py`, `pipeline.py` (worker + reconciler), `connectors/`. |
| DDiQ has no reconciler (analyzer has the pattern) | Adopt it | The reconciliation stage in §9.1 ports the same deterministic-arithmetic design. |

### 9.4 Security & tenant isolation

| Issue | Fix | How |
|---|---|---|
| No auth on either backend | JWT + `Depends` on every route | `POST /auth/login` issues JWTs (bcrypt-hashed passwords); `get_current_user` dependency validates on every route. Shared `AUTH_SECRET`. The validation logic in `auth/jwt.py` is correct — port it into `lai.common.auth`. |
| No `user_id` on DDiQ tables → data globally visible (GDPR) | Add `user_id` + filter every query | Migration: `user_id NOT NULL` on every table; populate from JWT on insert; every SELECT/UPDATE/DELETE filters by it. |
| Frontend `AuthContext` is fake | Real backend auth | Login/signup call the new backend endpoints; an interceptor attaches `Authorization: Bearer` to every fetch. Delete the browser-side base64 helper. |
| `CORS allow_origins=["*"]` | Env-driven allow-list | In production only the on-prem UI host is allowed. |
| Live HF token in `.env:11` | Rotate + secret store | Revoke, reissue, store via `docker secret` or a `chmod 600` file outside the repo. |
| Hardcoded default credentials | Fail closed | Remove defaults from `core/config.py:38,84,273`. The DDiQ microservice's `DB_PASSWORD:?Set DB_PASSWORD in .env` is the right pattern. |

### 9.5 Engine fault tolerance

| Issue | Fix | How |
|---|---|---|
| ~45 LLM calls per report; 8 are SPOFs for a whole chapter; ~22% chapter-loss rate | Schema-enforced output + typed fallback per pass | The guided-decoding change above; each critical pass has a typed empty fallback so failure yields an empty section with a logged warning, not an exception. Drops the rate near zero. |
| `_parse_alkis_feature` inverted control flow (Medium) | Move `break` to the success path | One-line fix per loop at `:705, 712`. |
| `llm_json` double-failure uncaught | Catch + typed empty | Wrap the retry's `json.loads`; return `{}` / `[]`. |
| `request_fingerprint` index plain → TOCTOU | Make UNIQUE + atomic claim | `CREATE UNIQUE INDEX … WHERE request_fingerprint IS NOT NULL`; `INSERT … ON CONFLICT DO NOTHING RETURNING id`. |
| Sync `/report/generate` sets fingerprint too late | Set at row creation, both paths | Mirror the async path. |
| Sync path has no exception handler | Wrap; mark `failed` | Standard try/except updating status. |
| Aux-table writes lack `ON CONFLICT` → duplicates on retry | Upsert keyed on `report_id` | Each aux insert becomes upsert. |
| Geocode/parcel cache poisons forever | TTL + bust-on-regenerate | Use existing `cached_at` columns; reject stale; delete cache rows the previous run wrote on regeneration. |
| Evidence rollup silently drops out-of-range LLM indices | Detect + downgrade confidence | Log and reduce the finding's confidence; do not silently produce evidence-less findings. |
| `_evidence` on `__dict__` not serialized by `.dict()` | Promote to real Pydantic field | Change to `evidence: list[Evidence] = []` on the row model. |
| OCR triggers on `len(text) < 50` only | Quality gate | Add alphabetic-ratio + mojibake-pattern checks. |
| No retries on any external HTTP call | `tenacity` retries + backoff | Already a declared dep. Wrap embed/LLM/ALKIS/Nominatim calls; ALKIS gets specific retry-on-530. Step 6 retries the batch instead of `break`. |

### 9.6 Operational layer

| Issue | Fix | How |
|---|---|---|
| Zero automated tests | Start with pure functions | Pytest tests for `german_splitter`, `text_cleaner`, the new reconciler, the multi-group `parse_wea_count` replacement, validation gates, `JsonSalvage`. Frontend Vitest tests for the API clients. Integration tests once the keystone lands. |
| Monitoring configured, not deployed | Bring it up; fix targets | Deploy the `Docker/monitoring/` compose; correct scrape targets to the actual container names; expose `/metrics` on both backends. |
| No streaming on `/query` | SSE via `sse-starlette` | `StreamingResponse`; vLLM already supports streaming; frontend wires `EventSource`. |
| `:latest` image tags + `>=` deps | Pin everything | Pin Docker images by digest or specific tag; lockfile for the microservice (`uv pip compile`). |
| Docs drift from reality | Targeted edits | The three specific lines in `WORKFLOW.md` (8M → 9.46M, "shared PostgreSQL", "CRAG in lai.generation" describing dead code); the README's eval numbers; the `serve_rag.py` docstring (127 GB → 155 GB). |
| Deployment topology drift (Docker vs host-process) | Pick one, make authoritative | The compose-based topology is the most coherent target; the host-process fallback was the audit-time outage trigger. |
| Frontend on Vercel/Cloudflare | Serve from the on-prem host | Drop Vercel and Cloudflare configs; serve `dist/` from Nginx/Caddy on the box. "Contracts never leave the building" becomes a real claim. |

### 9.7 Corpus and data quality

| Issue | Fix | How |
|---|---|---|
| Step 6 ~81% incomplete | Resume + monitor + decide migration policy | Above. |
| Top-level `data_processing/` is dead legacy code | Archive or delete | Not git-tracked, imported by nothing, superseded by `LAI/src/lai/pipeline/`. |
| FTS5 index goes stale on new rows | Migrate to Postgres FTS during the keystone | `tsvector` + `tsquery` is maintained per row. Moot after migration. |
| 15.8% fabricated citations in synthetic training data | Verification loop inside generation | Step 5's `generate.py` regex-extracts citations from each answer, confirms in source chunk, reject + regenerate on failure. `audit_training_data.py` becomes a CI gate. Fine-tuning can resume on clean data. |
| No corpus reindex endpoint — cold restart only | Online ingest into pgvector | Moot after migration; pgvector supports online upserts. |
| No feedback capture today | `POST /feedback` + correction memory | `lai_feedback` table already exists unused. Capture (original, corrected, reason) keyed by session + message id. Build correction memory in pgvector that retrieves similar past corrections and few-shots them into prompts. No retraining. |

---

# 10. The keystone — corpus into pgvector

**Q1 (corpus access) and Q3 (corpus canonical home) — RESOLVED.**

The live corpus is **`LAI/processed/pipeline_local.db`** — 350 GB SQLite,
**9.46M embedded child chunks at 4096-dim**. Confirmed by reading
`eval.py:40` and `serve_rag.py:50`. The `db_export/app.db` (304 GB) is a
stale April snapshot, not authoritative.

The migration target: **Postgres + pgvector, `halfvec(4096)` + HNSW**,
exposed through a shared `lai.retrieval` package both `serve_rag` and DDiQ
import. `halfvec` supports HNSW to 4096 dims (`vector` caps at 2000 — which is
why the README fell back to exact search). This was the documented design
intent; it was simply never finished.

**Why this is the keystone:** one project (effort large) collapses four
problems — DDiQ↔corpus grounding becomes a plain SQL join (Phase 2 becomes
medium not large), continuous corpus expansion gets online upserts, the
SQLite-as-prod-corpus problem dies, and horizontal scaling becomes possible.

**Cost / sizing input:** one-time migration of 9.46M × 4096 vectors + HNSW
build is hours-to-days of compute, ~80 GB disk for `halfvec`. Plus the open
**Q6** decision: Step 6 is incomplete (~40.5M chunks pending) — finish first or
migrate-and-stream-forward? Either is workable.

**Interim option** (optional bridge): a `/retrieve` endpoint on `serve_rag` that
DDiQ calls over HTTP — useful while the migration is in flight, retired after.

---

# 11. The five new building blocks

Five components do not exist today. They are not bug fixes — they are new
structural pieces that the catalog above keeps referencing.

1. **`lai.common`** — one shared library replacing the 2–4× duplicated helpers
   (PDF/OCR, chunker, embedding client, reranker client, LLM client with
   `<think>` strip + schema-enforced output + retries, JSON salvage).
2. **`lai.retrieval`** — the retrieval router that replaces the hardcoded
   `rag_context()`. Per question, decides which of {uploaded docs, legal
   corpus, public registries} to query; returns ranked chunks with provenance.
3. **`lai.connectors`** — `Connector` ABC + registry. ALKIS and Nominatim
   refactored into it; MaStR, Handelsregister added on top. All free/public.
4. **Facts ledger + deterministic reconciler** — one canonical `ProjectFacts`
   object every pass reads from and writes into; arithmetic and consistency
   forced in code, not via LLM. Ports the `analyzer/reconciler.py` philosophy.
5. **Validation / guardrail layer** — one pipeline stage between extraction
   and rendering. Enforces location plausibility, single language, schema
   compliance, source-tag honesty, no defensive prose, no hedge filler.

Plus the structural moves:

- **Delete the dead stack** (~3,200 LOC).
- **Migrate the corpus into pgvector**.
- **Add auth + tenant isolation.**
- **Move the frontend on-prem.**

---

# 12. The plan (phases, no specific dates)

Re-sequenced around the keystone.

**Phase 0 — Foundation.**
- *Status:* the runtime outage from the initial audit is **resolved** (see
  §2). Remaining Phase 0 work:
- Pick one deployment model (all-Docker vs host-process) and retire the
  other.
- Add auth + tenant isolation on both backends. This is the GDPR blocker.

**Phase 1 — Consolidation + two parallel tracks.**

- *1a — Consolidation (cheap, runs first).* Delete the dead stack; extract
  `lai.common`. After this, every reliability fix lands once.
- *1b — runs as two independent tracks in parallel:*
  - **Track A — DDiQ reliability.** All of §9.1 + §9.5 + the in-analysis
    hedge-language strip from §13. Exit: the Lamstedt smoke test passes.
  - **Track B — The keystone.** Corpus → pgvector + `lai.retrieval`. Exit:
    DDiQ can query the corpus with a plain SQL join.

Why parallel: Track A touches `ddiq_report.py` logic; Track B touches infra
and a package refactor. Different files, different streams.

**Phase 2 — Beyond the data room.** Gated on Track B.

- 2A — statutory grounding (the retrieval router pulls from the corpus per
  section).
- 2B — public-registry connectors (MaStR, Handelsregister, expanded ALKIS).
- 2C — provenance tagging on every fact + citation-integrity enforcement.

**Phase 3 — Feedback loop.** Can start as early as 1b.

- Capture corrections via `POST /feedback`; build the correction-memory store
  in pgvector; inject similar past corrections as few-shots in future prompts.
- No GPU retraining; the model improves from inference-time context.
- Doubles as the eval harness the project does not have today.

**(B) Drop the formal liability disclaimer.** Not a step — the program's
definition of "done." Removing the footer is a one-line frontend change *only*
when Phases 1–3 exit criteria are met AND counsel has cleared the German RDG
question. The last 1 %, not the first.

---

# 13. The "no contact a lawyer" requirement

Two layers, opposite handling:

- **(A) In-analysis hedge language** — "consult a Fachanwalt", "as an AI…",
  filler caveats. Sources are the live model's defaults and stale dead code
  (`constants.py:326`, `pipeline/generate.py:84`); there is **no live
  post-processing layer** that strips them. The legacy
  `MAX_DISCLAIMERS`/`REMOVE_AI_REFERENCES` controls were the right idea in the
  wrong place (now dead). **Fix:** the new validation/cleanup layer (§11) strips
  them, system prompts are tightened ("decisive Fachanwalt; do not refer to
  other lawyers"). Rides along in Phase 1. Low risk.
- **(B) The formal "does not substitute legal review" footer + adopting
  lawyer-replacement positioning.** Sources: `ReportDownloadPanel.tsx:833,
  2103`. **This is the company's liability shield, not filler.** Dropping it is
  gated by: reliability proven (smoke test passes), citation integrity solved
  (the 15.8% fabrication problem), grounding/provenance in every fact, coverage
  (no more "no information" ×25), demonstrable improvement (the feedback
  loop), AND counsel clearance on **Germany's Rechtsdienstleistungsgesetz**
  (whether marketing as a legal-services provider is permissible). Counsel
  question, not a code change.

---

# 14. Strategic positioning (and the competitive correction)

### 14.1 The competitor landscape (web-verified)

| Competitor | Verdict |
|-----------|---------|
| **Luminance** (UK) | Real, well-funded ($75M Series C early 2025), works inside Microsoft Word, multi-agent contract architecture, 1,000+ enterprises in 70+ countries. |
| **Harvey** (US) | Strong Germany presence. **Deutsche Telekom adopted Harvey in early 2024 — the entire Law & Integrity team uses it.** Data encrypted and stored in Germany was a non-negotiable adoption condition. "Harvey Agents" are real end-to-end workflow agents. |
| **Bryter** (Germany) | Frankfurt HQ, no-code legal/compliance automation, ~201 employees, ~$66M raised, "Cool Vendor 2025", clients incl. McDonald's, ING, Linklaters. Active. |
| **Leverton** (Berlin) | **Acquired by MRI Software in July 2019.** Pivoted to real-estate / lease abstraction. **Not an independent competitor.** The market research listed it as if it were one — factually outdated. |
| **Legartis** (Switzerland) | Swiss, since 2017. **Hosts all data AND all LLMs locally in Switzerland/Europe; GDPR-compliant; ISO 27001-certified.** Handles German/Swiss/Austrian + English. **Directly ships LAI's pitched differentiator.** |
| **Spellbook** (Canada) | Native Microsoft Word add-in; drafting/redlining/review; GPT-4o-based; 4,000+ legal teams across 80+ countries. |

### 14.2 The strategic correction

"Local models on-prem" is **not** a defensible moat. Legartis already ships
fully sovereign legal AI with GDPR + ISO certification; Harvey stores data in
Germany for Deutsche Telekom under explicit contract. Saying "we keep your data
on-prem" is table stakes for a German legal client, not differentiation.

**What LAI can defensibly win on is vertical depth in German wind-energy due
diligence:**

- 12 federal-state ALKIS WFS endpoints (verified in
  `ddiq_report.py:61` — Niedersachsen, NRW, SH, Brandenburg, MV, ST, Hessen,
  Thüringen, Sachsen, RLP, Bayern, BW). No generalist has this.
- The 10H setback rule actually wired in to the cadastral clearance pipeline.
- 200K synthetic Q&A samples across 7 task types in 12 wind-energy domains.
- The 672 GB German legal corpus, when the keystone makes it reachable.
- Statutory-anchor extraction prompts that reference BImSchG / BauGB / BNatSchG
  / EEG by section. Built for this domain.

The honest pitch is *best at German wind-energy DD, period* — not *Harvey
alternative*. Narrow and deep beats broad and shallow for a company this size,
and the on-prem story is a *credential* (which is real) rather than a
*differentiator* (which it isn't).

### 14.3 Compliance posture

- **GDPR / BDSG** — until the auth + tenant-isolation work in Phase 0 ships,
  the product technically violates data-protection law (data globally visible).
  Cannot onboard a second customer until fixed.
- **EU AI Act** — the bulk of obligations land **2 August 2026**: high-risk
  Annex III systems, Article 50 transparency, support measures for innovation.
  GPAI obligations have applied since August 2025. Whether a commercial legal-
  DD assistant counts as "high-risk Annex III" is genuinely debatable — Annex
  III's justice category concerns AI used *by judicial authorities*, not
  commercial tools used by law firms. LAI more likely faces Article 50
  transparency obligations + GPAI-downstream considerations. **This is a
  counsel question, not a given.**

Practical actions regardless of how counsel rules: audit logging, data
retention policy, soft-delete capability, model-decision provenance — all are
good practice and all line up with the architecture work above.

---

# 15. What the boss should hear (the message)

(See `BOSS_BRIEF` / `BOSS_MEMO`-style draft for the version to send; key
points consolidated here.)

1. **Status update:** the runtime stack is back up; the system is no longer
   down. The remaining problem is output quality, which is what you actually
   called out.
2. **Honest diagnosis:** the smoke-test failures are not random bugs — three
   structural causes (DD engine can't see the corpus; AI output trusted
   unchecked; no ground-truth reconciliation) produce all of them.
3. **What changes for the lawyer**, in concrete user terms (the eight-point
   success bar in §17).
4. **Architecture:** competent pieces, incoherent wiring. **Re-wiring, not a
   rewrite.** Five new components + four structural moves.
5. **Constraints respected:** on-prem only — frontend moves on-prem too;
   "contracts never leave the building" becomes a real claim. No further
   budget — the registries we need are free public APIs.
6. **Strategic correction:** "local" is not a moat. The defensible play is
   **best at German wind-energy DD**, not generalist legal AI.
7. **Measurable success bar:** the eight items in §17. If we deliver them, the
   smoke test stops being embarrassing. If we can't, the rest doesn't matter.

---

# 16. Open decisions

Three locked (per earlier sessions):

- **Blended scope** — fix the data room AND reach beyond it.
- **Learning = feedback loop**, not GPU retraining.
- **DDiQ engine is the priority surface.**

Three new locks (this session):

- **On-prem only**, no cloud. Frontend moves on-prem too.
- **No further budget** — public/free registries only.
- **No code changes this session** — this is the planning deliverable.

Two open, both counsel-territory:

- **Q5 — German RDG / EU AI Act.** May LAI be marketed as a legal-services
  provider / lawyer-replacement; how does removing the liability disclaimer
  change the company's exposure? **Counsel decision. Gates dropping the
  footer.**
- **Q2 (now closed by the budget lock).** Paid sources (credit bureaus, paid
  Grundbuch access) are out of scope. Grundbuch stays a "request from client"
  action item — which is correct DD practice anyway.

One open, operational:

- **Step 6 status — RESOLVED (incomplete).** ~9.46M of 50M embedded;
  ~40.5M pending. Decision needed: finish-before-migrate or migrate-and-stream
  during Phase 1b Track B.

---

# 17. Success bar — how we measure "good output"

Re-run the Lamstedt smoke test. Grade on these eight, all measurable:

1. **Non-empty Findings chapter** — no "extraction failed" placeholder.
2. **Turbines on the correct map** (Landkreis Cuxhaven), or explicitly flagged
   `unverified`. No more Bremen.
3. **One consistent turbine count** across text, math, title, and table.
4. **Every "missing" item rendered with the governing statute cited** — e.g.
   *"§35(5) BauGB Rückbaubürgschaft — request from client"*. No more defensive
   paragraphs.
5. **One language end-to-end** (no Denglisch).
6. **No "consult a Fachanwalt" filler** in the body.
7. **Every fact carries a visible source tag** (`uploaded_doc / legal_corpus /
   registry / estimated`).
8. **Clean table rendering in the PDF** (already true — re-confirmed in §15
   verification).

If we can deliver those eight, the smoke test stops being embarrassing and LAI
is positioned to be evaluated on its real strengths (legal reasoning, cadastral
depth, statutory grounding). If we cannot, the rest doesn't matter.

---

# 18. Verification and corrections

Honest record-keeping. Re-verified directly in this session:

- `pipeline_local.db` row counts: 9,462,540 / 49,953,830 / 13,807,675 (probed).
- `ddiq_report.py` size: 2,463 lines / 129,035 bytes (`wc -l -c`).
- 155 GB RAM load: 9,462,540 × 4096 × 4 = 155.04 GB (math).
- `parse_wea_count` first-int regex bug: read directly at `:838-839`.
- `request_fingerprint` index is `CREATE INDEX`, not `CREATE UNIQUE INDEX`:
  read DDL directly.
- `lai.api.main` imported by no live module: re-grepped.
- `citation_verifier` imported by no live module: re-grepped.
- `config.py:40` `pool_max_size` default 10: read directly.
- Zero test `.py` files in `LAI/tests` or `LAI-UI/src`: verified.
- Lamstedt coordinates 53.622796 N / 9.147855 E vs Bremen ~53.094 / 8.785
  (~65 km off): web-verified.
- HF token in `Docker/inference_engine/.env:11`: re-read; **rotate**.

Corrections to earlier drafts (full audit in `RE_VERIFICATION.md`):

- "Live system broken right now" was true at the time of the initial audit
  and is **no longer current** as of 2026-05-14 — the runtime stack is up
  and healthy.
- "~6,000 LOC of dead code" was overstated; actual is **~3,200 LOC**
  (line-counted directly).
- "DDiQ has 14 router endpoints" was wrong (`@router.on_event` lifecycle hooks
  miscounted); actual is **12 endpoints**.
- `SECTION_QUESTIONS` total was 39 (claimed); actual is **37** — overview 11
  + land 8 + permits 8 + economics 10.
- "~49 LLM calls per report; p^10 → 1-in-4 chapter loss" was based on the
  39-question count and 10 SPOFs; corrected to **~45 LLM calls; p^8 → ~22%
  chapter loss** (8 SPOFs).
- `_parse_alkis_feature` severity was tagged Critical; corrected to **Medium**
  — the bug only manifests when multiple candidate keys are simultaneously
  present in one ALKIS feature; most records have only one.

Honest gaps (not independently re-measured):

- "~1,500–2,000 LOC duplicated" is an estimate, not a line-by-line count. The
  qualitative claim (helpers duplicated 2–4×) is solid.
- The `p` in the compounding-failure math is illustrative, not measured.
- "15.8% fabricated citations" comes from the project's own
  `audit_results.json` — we did not rerun the audit.

### Smoke-test critique (separate document) — what matched the PDF and what didn't

| Critique claim | Verdict |
|----------------|---------|
| "AI explicitly admits failure" | ✓ Confirmed in PDF + code |
| "CSV bleed / broken tables" | **Not in this file.** Re-read pages 2/12/13 — all tables render clean. PDF is generated client-side via `window.print()` from React HTML. The quoted text is consistent with select-and-copy out of a PDF table, not the PDF generator. |
| "Denglisch" | ✓ Confirmed |
| "Hallucinations & repetition" | Repetition confirmed (blob ×6). "Map prints a chaotic list of labels" misdiagnosed — the map *renders*; it's just showing Bremen, which is a geocoding bug. |
| "Defensive I-don't-know responses" | ✓ Confirmed |

### Market-research correction summary

Five of six competitors accurate; Leverton wrong (acquired 2019, now real
estate); Legartis under-weighted (already ships LAI's pitched differentiator).
"max_tokens one-line fix at line 504" imprecise (line 504 default is 2048; the
4096 is at lines 517/521) and the fix is oversimplified (cutting to 1024 risks
truncating valid long JSON). "Citation regex too strict" premise is wrong (no
live citation verification at all).

---

# 19. Sources

**Web-verified competitor claims:**
- Luminance — https://www.luminance.com/
- Harvey / Deutsche Telekom — https://www.harvey.ai/customers/deutsche-telekom
- Bryter — https://bryter.com/
- Leverton / MRI Software acquisition — https://www.mrisoftware.com/news/mri-software-acquires-ai-real-estate-pioneer-leverton-turn-unstructured-data-business-insights/
- Legartis — https://www.legartis.ai/
- Spellbook — https://www.spellbook.legal/
- EU AI Act timeline — https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai · https://artificialintelligenceact.eu/implementation-timeline/
- Lamstedt coordinates — https://www.findlatitudeandlongitude.com/l/Seth,+Lamstedt,+Samtgemeinde+B%C3%B6rde+Lamstedt,+Landkreis+Cuxhaven,+Lower+Saxony,+21769,+Germany/8230282/

**Code basis (file:line citations throughout this report):**
- `LAI/micro-services/ddiq_report.py` (the DD engine god-file)
- `LAI/micro-services/api.py`, `cadastral_pipeline.py`
- `LAI/src/lai/api/serve_rag.py` (the live chat backend)
- `LAI/src/lai/search/eval.py` (retrieval over SQLite)
- `LAI/src/lai/analyzer/reconciler.py` (the design pattern DDiQ should adopt)
- `LAI/src/lai/pipeline/{cli,embed,classify,enrich,generate,chunk,convert}.py`
- `LAI/processed/pipeline_local.db` (the live corpus, probed directly)
- `LAI/docs/smoke_test_report.pdf` (re-read pages 2, 12, 13, 14)
- `LAI-UI/src/react-app/contexts/AuthContext.tsx`, `utils/jwt.ts`,
  `components/ReportDownloadPanel.tsx`, `lib/{ragApi,ddiqApi}.ts`
- All `Docker/**/docker-compose.yml`, `LAI/docker-compose.yml`,
  `LAI/micro-services/docker-compose.yml`, all `.env`/`.env.example` files
- Live `docker ps`, `ss -tlnp`, `nvidia-smi`, `docker exec … getent hosts …`

---

*End of consolidated report. Eight prior working documents
(`AUDIT.md`, `TECH_STACK.md`, `DDIQ_ROADMAP.md`, `DEEP_RESEARCH.md`,
`VERIFICATION.md`, `ARCHITECTURE_BRIEF.md`, `RE_VERIFICATION.md`,
`ISSUES_FIXES_METHODS.md`) remain in the same folder for reference; this
document is the single consolidated source of truth.*
