# LAI — Issues, Fixes, and Methods (Complete)

**Date:** 2026-05-14
**Purpose:** Single catalog of every issue we've identified across the system,
the fix for each, and the concrete method to deliver it. No timeline, no
sequencing — this is the punch list, not the roadmap (`DDIQ_ROADMAP.md` covers
sequencing separately).

Every issue cites the evidence (`file:line` or a probe). Severity grading is
intentionally honest, not inflated.

> **State note (2026-05-14):** the "live system is down" finding from the
> initial audit has been resolved — `lai_postgres_main`, `lai_embedding`,
> `lai_analyzer_llm`, `lai_redis` are all up and healthy; `lai-backend` returns
> `/health` OK; `serve_rag` is running on `:18000`. Only `lai-user-doc-processor`
> remains unhealthy. So the catalog below is about **quality and structure**,
> not "the service is offline."

---

## Is the current architecture solid?

**The pieces are solid. The wiring is not.** That distinction matters because
it determines what we're doing — re-wiring, not rewriting.

### What is solid (do not throw away)

- **The model choices.** Qwen3.6-27B analyzer, Qwen3-Embedding-8B (4096-dim,
  #1 multilingual MTEB), Qwen3-Reranker-8B — all served via vLLM. Strong, modern,
  multilingual, locally-hosted.
- **The legal reasoning capability.** The smoke test correctly parsed a complex
  OVG ruling, distinguished BImSchG §§4/6/10/15 statuses, caught a real
  cross-document inconsistency (E-79 vs E-70 between permit and maintenance
  contract). That quality of reasoning is hard to fake and worth keeping.
- **The 672 GB German legal corpus.** Real, embedded (9.46M chunks at 4096-dim
  so far), with parent/child chunking, domain classification, contextual
  enrichment. Not throwaway.
- **The data pipeline.** Six idempotent steps with two-stage SIGINT handling,
  keyset-pagination resume, dual-write NPZ backups, schema versioning. Above
  typical research-code maturity.
- **The cadastral / wind-energy domain integration.** 12 federal-state ALKIS
  WFS endpoints, INSPIRE CadastralParcel schema parsing, 10H rule, 13-step
  parcel workflow — competitor depth that no generalist has.
- **The `analyzer` package design pattern.** `reconciler.py` does the
  arithmetic *in code*, lets the LLM only interpret — exactly the right
  philosophy. It just hasn't been adopted by DDiQ yet.
- **The DDiQ database schema** (9 tables: `ddiq_documents`, `ddiq_reports`,
  `ddiq_doc_chunks`, `ddiq_geocode_cache`, `ddiq_parcel_cache`,
  `ddiq_project_areas`, `ddiq_contracts`, `ddiq_contract_parcels`,
  `ddiq_classified_parcels`). Sensible normalization.

### What is not solid (the wiring)

- The DD engine cannot see the legal corpus — two halves of the product are
  siloed.
- Three parallel codebases (~3,200 LOC dead, ~1,500–2,000 duplicated across
  `serve_rag.py`, `api.py`, `ddiq_report.py`).
- The corpus lives in SQLite while DDiQ lives in Postgres — drift, not design.
- No validation layer between the AI's output and the user-facing report.
- No reconciliation layer — multiple passes can produce contradictory numbers
  that all make it into the document.
- No retrieval router — `rag_context()` is hardcoded to one source.
- No connector abstraction — ALKIS/Nominatim are bolted into a 2,463-line god-file.
- No authentication, no tenant isolation, fake auth on the frontend.
- Single GPU model serves every product surface — one OOM brings down chat,
  contract analyzer, and DDiQ together.

**Verdict:** competent in pieces, incoherent as a system. The fix is structural
re-wiring (and a few new components), not a rewrite. None of the "solid" list
above gets discarded.

---

# The catalog

## 1. Report output quality (the smoke-test failures)

| Issue | Fix | How |
|---|---|---|
| Findings chapter literally prints **"Manual review required (findings extraction failed)"** to the client. `generate_findings()` makes one batched `llm_json()` call over all flagged rows; any malformed JSON → bottom `except` → the placeholder. (`ddiq_report.py:1543-1648`) | Per-finding iteration + schema-enforced output | Replace the single batched call with a loop, one small LLM call per flagged row. Use vLLM **guided decoding** with a Pydantic JSON schema so the model is forced to return valid JSON (the `analyzer` package already does this — DDiQ doesn't). Strip `<think>` reasoning traces before parse. Each call gets a cheap retry. Partial success becomes useful: 6/8 succeed → 6 findings, not 0. |
| All 6 turbines geocoded to **Bremen** (~65 km from the actual Lamstedt site). `geocode_address()` passes whatever the LLM wrote (a paragraph) to Nominatim with `limit=1`, no plausibility check. (`ddiq_report.py:571`) | A new validation gate on every geocode | (a) A location-normalization pass returns *structured* fields (`gemeinde`/`gemarkung`/`landkreis`/`bundesland`), never free text. (b) Plausibility gate: the Nominatim result must fall inside the named Bundesland/Landkreis bounding box AND clear an `importance` threshold — otherwise the location is marked `unverified` in the report and *not* used to drive cadastral queries. (c) Add `cached_at` TTL to `ddiq_geocode_cache` so a bad coord doesn't poison the cache forever. |
| ALKIS parcels show synthetic rectangles labeled as **real `source="ALKIS WFS"`** — fabricated geometry mislabeled as cadastral truth. Parcel `area` is `round(2.0 + (hash(pnum) % 20) / 10, 1)` — a hash of the parcel number. (`ddiq_report.py:1516`, `cadastral_pipeline.py:805`) | Provenance as a first-class data field | Every fact in the report carries a typed `source` tag from a closed enum: `uploaded_doc` / `legal_corpus` / `registry` / `estimated` / `unverified`. Synthetic `make_parcel_polygon()` output **cannot** carry an ALKIS source — the render layer enforces honest labeling and shows estimated geometry visually distinct. The "hash → area" fabrication is replaced with `area = None` when ALKIS didn't return one. |
| **Four conflicting turbine counts** in one report: 7/3/10 in text, 11 in capacity math, "10 von 11" in title, 6 in table. Four independent derivations, never reconciled. `parse_wea_count` (`:838-839`) is `re.search(r"(\d+)")` — grabs the first integer from a value the prompt deliberately fills with multiple numbers. | Deterministic reconciliation stage | After all extraction passes complete, a new reconciliation stage runs *in code* (not LLM). Replace `parse_wea_count` with a multi-group parser that handles `errichtet + genehmigt + geplant`. Force `len(weas)` back into the overview row. Emit *one* canonical count. Contradictions become *one* finding ("text says 10 but only 6 turbines were extracted — investigate"), not 4 printed numbers. Port the deterministic pattern from `lai/analyzer/reconciler.py`. |
| 25 of 37 sections say "Die vorliegenden Kontextausschnitte enthalten keine Angaben…" — defensive paragraphs explaining what's missing. | Statutory grounding + a structured "missing" state | Two changes. (a) The retrieval router pulls the relevant statute from the legal corpus per section, so a gap becomes *"§35(5) BauGB requires a Rückbaubürgschaft — absent from the data room, request from client"* with the statute cited. (b) "Missing" is a typed state in the data model that the renderer shows as a red-flag icon + one-line action item, not a paragraph. |
| Same "Bundesland: Niedersachsen… Gemeinde: nicht explizit…" paragraph dumped **6×** in the WEA address column; same owner blob 6× in the Owner column. | A canonical facts ledger | One reconciled "project facts" object every render reads from. Identical values appear once and are *referenced* across rows, not regenerated per row. Address-paragraph repetition disappears structurally. |
| **Denglisch** — English category labels ("Number of WEA", "Project Status"), German content, mixed mid-sentence ("Formal permit status: An Änderungsgenehmigung…"). | Single-language enforcement in the validation layer | Output guardrail enforces one language per report (configurable per customer). Mid-sentence switches are detected and the section is re-prompted. Section labels match content language. |
| Reflexive "consult a Fachanwalt" filler — the AI hedges constantly inside answers. | Output-cleanup pass + tighter system prompts | New cleanup pass strips disclaimer-class phrases. System prompts say "answer as a decisive Fachanwalt; do not refer to other lawyers." The formal "does not substitute legal review" footer stays until reliability is proven AND counsel clears the RDG question — see open decision Q5 in `DDIQ_ROADMAP.md`. |
| WEA technical specs (hub height, rotor diameter, rated power) often return `null` despite the datasheet being indexed. | Dedicated specs-only extraction + better PDF table parsing | New focused prompt that targets the numeric spec table only, no narrative context. Use Docling's `TableFormerMode.ACCURATE` for datasheets (already imported, just needs invoking). |

## 2. The DD engine cannot reach our knowledge base

| Issue | Fix | How |
|---|---|---|
| `search_doc_chunks` queries `FROM ddiq_doc_chunks … WHERE doc_id = ANY(doc_ids)` — only the uploaded PDFs. The 672 GB legal corpus is unreachable. (`ddiq_report.py:475`) | Unify retrieval over one store | Migrate the 9.46M-embedding corpus from SQLite `pipeline_local.db` into Postgres + pgvector as `halfvec(4096)` + HNSW (this was the README's documented intent — never finished; `vector` type caps HNSW at 2000 dims, `halfvec` supports 4096). DDiQ becomes a plain SQL join across `corpus_chunks` + `ddiq_doc_chunks`. |
| Two storage engines (SQLite for corpus, Postgres for DDiQ) — drift, not design. `cli.py:869` is literally titled "Step 6: Embeddings → pgvector" while the code writes SQLite. | One Postgres, everything in it | The migration above. `pipeline_local.db` retires as a serving store; the pipeline keeps it as a staging artifact if useful, but production reads pgvector. |
| **Step 6 is ~81% incomplete** — 9.46M of 50M child chunks embedded; ~40.5M with `embedding IS NULL`. (`cli.py:917-933`) | Finish embedding (policy decision) | `resume_step6.sh` already exists. Choose one of: (a) migrate the existing 9.46M now and let Step 6 keep filling forward into pgvector via online upserts, or (b) finish Step 6 in SQLite first then bulk-migrate. Decision belongs in the keystone planning. |
| `rag_context()` is hard-wired to a single source. (`ddiq_report.py:525`) | A retrieval router | Replace the one-liner with a router: per question, decide which of {uploaded docs, legal corpus, public registries} to hit, return ranked chunks **with provenance tags**. This becomes the single funnel every analysis pass calls. Pluggable sources behind one interface. |
| No external public data is queried (the smoke test had no `HRB` lookup, no MaStR verification, etc.). | A `lai/connectors/` package — public-only (no budget for paid sources) | Define a `Connector` ABC (`fetch`, `cache_key`, `parse`, `source_tag`). Refactor ALKIS WFS and Nominatim into the package as the first two implementations. Add **Marktstammdatenregister** (free public API — confirms turbine registration, commissioning, capacity → directly fixes WEA-count and EEG-status questions), **Handelsregister** (public — project-company verification, fixes the missing HRB), **OpenStreetMap tiles** (free — already used). Grundbuch stays a "request from client" action item; paid bureaus are out of scope. |

## 3. The codebase is fragmented and partly dead

| Issue | Fix | How |
|---|---|---|
| **~3,157 LOC of dead code** in `LAI/src/lai/`: `api/main.py` (119), `api/pipeline.py` (128), `auth/` (168), `documents/` (634), `extraction/` (597), `generation/` (548), `infra/` (338), `search/{routes,repository,hybrid_search,reranker,query_analyzer}.py` (625). Imported by nothing live. | Delete | Pure delete after a final grep confirmation that no live module reaches them. Salvageable patterns from these modules (JWT validation in `auth/jwt.py`, `citation_verifier.py`) get ported into the new `lai.common` package — the *patterns* are useful, the modules-in-isolation are not. |
| **~1,500–2,000 LOC duplicated** across `serve_rag.py`, `api.py`, `ddiq_report.py`: PDF extraction (3×), text chunking (2×), embedding client (3×), reranker client (2×), LLM client (3×), JSON-salvage (3×). | One shared library: `lai.common` | Extract one of each: `PdfExtractor` (PyMuPDF + Tesseract fallback with quality gate), `Chunker`, `EmbeddingClient`, `RerankerClient`, `LlmClient` (with `<think>` strip + schema-enforced output + retries), `JsonSalvage`. Every fix lands once instead of three times. |
| `ddiq_report.py` is a **2,463-line god-file** mixing 12 endpoints, 9 inline table DDLs, 8 extraction passes, the ALKIS WFS client, GeoJSON/GML parsers, the LLM-orchestration helpers, and lifecycle hooks. | Decompose into modules | Split (no logic change): `db.py` (schema + pooling), `models.py` (the ~18 Pydantic models), `extractors/` (one file per pass), `routes.py` (the 12 endpoints), `pipeline.py` (the worker + reconciler), `connectors/` (post §2 refactor). |
| `analyzer/reconciler.py` has the right pattern ("LLM never does the arithmetic") but DDiQ imports nothing from `lai.analyzer`. | Adopt the pattern in DDiQ | The reconciliation stage in Section 1 ports the same design: `parse_german_number`, deterministic severity bands, in-code arithmetic. |

## 4. No security or tenant isolation

| Issue | Fix | How |
|---|---|---|
| No auth on either backend — every endpoint open. (`serve_rag.py:944-1460`, `ddiq_report.py:1655-2463`) | Real JWT + `Depends` on every route | A `POST /auth/login` issues JWTs (bcrypt-hashed passwords). A `get_current_user` dependency validates the token. Shared `AUTH_SECRET` so both backends accept the same token. The JWT-validation code in `auth/jwt.py` is correct — port it into `lai.common.auth` and mount on the live apps. |
| No `user_id` on DDiQ tables → data globally visible. **GDPR blocker.** | Add `user_id` + filter every query | Migrate: add `user_id NOT NULL` to `ddiq_documents`, `ddiq_reports`, `ddiq_doc_chunks`, `sessions`. Populate on insert from the JWT claims. Every SELECT/UPDATE/DELETE adds `WHERE user_id = current_user.id`. Listings (`GET /ddiq/reports`, `GET /ddiq/documents`) filter the same way. |
| Frontend `AuthContext.login()` accepts any credentials, mints an **unsigned base64** "token" (`AuthContext.tsx:55-82`, `utils/jwt.ts:26-36`), and never sends it to any backend. | Replace with real backend auth | Login/signup call the new backend endpoints; the JWT comes from the server; an interceptor attaches `Authorization: Bearer …` to every fetch in `ragApi.ts` and `ddiqApi.ts`. The browser-side base64 helper is deleted. |
| `CORS allow_origins=["*"]` on both backends, paired with no auth. | Origin allow-list driven from env | In production only the on-prem UI host is allowed. `.env.example` documents this. |
| **Live HuggingFace token** in plaintext at `Docker/inference_engine/.env:11` (gitignored, but on a shared multi-project host). | Rotate + move to a real secret store | Revoke the token, issue a new one, store in `docker secret` or a file with `chmod 600` outside the repo. Removed from `Docker/inference_engine/.env` (which is also dead-stack config and can go away entirely). |
| Hardcoded default credentials in source (`lai_test_password_2024`, `superStrongPassword123!`, `CHANGE-ME-IN-PRODUCTION`) — if `.env` is ever missing the app runs on known creds. | Fail closed | Remove the defaults from `core/config.py:38,84,273`; mark required env vars without fallback. The DDiQ microservice's compose already does this correctly with `DB_PASSWORD:?Set DB_PASSWORD in .env` — same pattern everywhere. |

## 5. The engine has no fault-tolerance discipline

| Issue | Fix | How |
|---|---|---|
| ~45 LLM calls per report (37 section questions + 8 dedicated passes); **8 of those are single points of failure** for a whole report chapter. At p=0.97 per critical pass, `p^8 ≈ 0.78` → roughly 22% of reports lose a chapter to JSON-parse failures. | Schema-enforced output + typed empty fallback per pass | Section 1's guided-decoding change applies system-wide. Each of the 8 critical passes gets a typed empty fallback so a failed pass produces an empty section *with a logged warning*, not a thrown exception that aborts later phases. The 22%-chapter-loss math drops near zero. |
| `_parse_alkis_feature` Flur/area loops at lines 705, 712 — `except (ValueError, TypeError): pass; break`. On parse *success* the loop continues (last matching key wins); on *failure* it breaks. Inverted control flow. Severity Medium — only impacts ALKIS features with multiple competing keys present. | Fix the control flow | Move `break` out of the `except` clause: break after a successful parse, not on failure. One-line fix per loop. |
| `llm_json` double-failure is uncaught — the second `json.loads(raw2)` has no guard and propagates `JSONDecodeError` up. (`ddiq_report.py:516-523`) | Wrap; return typed empty | Catch `JSONDecodeError` on the retry path; return `{}` or `[]` per caller expectation. Callers already handle empty gracefully. |
| `request_fingerprint` index is plain `CREATE INDEX`, not UNIQUE → TOCTOU: two identical concurrent async requests both queue a 30–60 min pipeline. (`ddiq_report.py:140`) | Make the partial index UNIQUE + atomic claim | `CREATE UNIQUE INDEX … ON ddiq_reports(request_fingerprint) WHERE request_fingerprint IS NOT NULL`. The queueing path uses `INSERT … ON CONFLICT DO NOTHING RETURNING id` to atomically claim the fingerprint. |
| Sync `/report/generate` sets `request_fingerprint` only *after* the pipeline completes — during the 30–60 min run dedup misses it. (`ddiq_report.py:2199-2206`) | Set fingerprint at row creation, both paths | Mirror the async path's create-then-update pattern in the sync path. |
| Sync path has no try/except around `_generate_report_core` → a mid-pipeline crash leaves the row at its column default `status='done'`. `/reports` lists a half-built report as complete. (`ddiq_report.py:133`) | Wrap; mark `failed` on exception | Standard try/except: update status to `failed` with the error message on any exception. |
| Aux-table writes (`ddiq_project_areas`, `ddiq_contracts`, `ddiq_classified_parcels`) have no `ON CONFLICT` → retry/re-run produces duplicate rows. The code comment at line 2133 admits it. | Add `ON CONFLICT` or delete-then-insert keyed on `report_id` | Each aux insert becomes an upsert keyed on the natural unique tuple. |
| `ddiq_geocode_cache` and `ddiq_parcel_cache` poison permanently — no TTL, no invalidation. A bad geocode survives every report run. | TTL + bust-on-regenerate | Use the existing `cached_at TIMESTAMPTZ DEFAULT NOW()` column to reject reads older than N days (configurable per cache); on report regeneration, delete cache rows the previous run wrote. |
| Evidence rollup silently drops out-of-range LLM-supplied indices → a finding can end up with zero evidence and no warning. (`ddiq_report.py:1620`) | Detect + downgrade | If an LLM evidence-index is out of range, log it and downgrade the finding's confidence; do not silently produce a finding with empty evidence — that defeats the "click to source" promise. |
| Pydantic `.dict()` doesn't serialize `_evidence`/`_anchor` stashed on `row.__dict__` → checkpointed JSONB on disk loses evidence; only the in-memory object has it. (`ddiq_report.py:1028, 1793`) | Promote to real Pydantic fields | Change `evidence: list[Evidence] = []` and `anchor: Optional[str] = None` as proper fields on the row model. Serialization works automatically. |
| OCR fallback triggers on `len(text) < 50` per page — short legitimate pages get needless OCR; pages with 50+ chars of garbage extraction *never* get OCR'd. (`ddiq_report.py:417`) | Quality gate, not just length | Add a heuristic: if text is short *or* if alphabetic character ratio is below threshold *or* if "ÿü" / mojibake patterns appear → OCR. |
| No retries on any LLM/embedding/external HTTP call (pipeline steps 3/4/5/6, DDiQ LLM calls, ALKIS WFS). A single transient hiccup drops a chunk or aborts a 48 h run. | `tenacity` retries with exponential backoff | `tenacity` is already a declared dependency. Wrap each external HTTP call: 3 attempts × exponential backoff, jitter, longer wait on 5xx. ALKIS gets specific retry-on-HTTP-530. Step 6 retries the batch instead of `break`. |

## 6. The operational layer is missing

| Issue | Fix | How |
|---|---|---|
| **Zero automated tests** anywhere. `LAI/tests/{unit,integration,e2e}` are empty directories; the frontend has none either. | Start with pure functions — cheapest, biggest gain | Pytest tests for `german_splitter`, `text_cleaner`, the new reconciler, the multi-group `parse_wea_count` replacement, validation gates (bounding-box check, plausibility), `JsonSalvage`. Integration tests once the keystone migration lands (the corpus is in one place to test against). Frontend: Vitest for the API clients, especially the not-found-vs-unreachable distinction in `ragApi.ts:172-181`. |
| `Docker/monitoring/` is configured with a valid `prometheus.yml` but **no Prometheus/Grafana container is running**; scrape targets don't match container names. | Deploy + correct targets | Bring the monitoring compose up; fix targets to the actual container names (`lai_analyzer_llm`, `lai_embedding`, `lai-backend`); expose `/metrics` on both backends via `prometheus-client` (already a declared dependency). Wire latency histograms, LLM-call counters, retrieval-time buckets, embedding-cache hit rate. |
| No streaming on `/query` — users wait for the full answer. `sse-starlette` is a declared dependency but unused. | SSE via `sse-starlette` | `StreamingResponse` over an async generator that yields per-token output. vLLM already supports streaming. Frontend wires up `EventSource` to feed the chat UI incrementally. |
| `:latest` image tags pervasive (`vllm/vllm-openai:latest`, `prometheus:latest`, `grafana:latest`, `minio:latest`, `mlflow:latest`). `vllm:latest` has already broken a CLI flag once (documented in `start-host.sh`). | Pin everything | Pin Docker images by digest (or at least specific version tag). `vllm` should track a known-working release. |
| The deployed DDiQ microservice uses `>=` ranges in `requirements.txt` with no lockfile — builds aren't reproducible. The core `LAI/` package has `uv.lock`; the microservice doesn't. | Lockfile for the microservice | `uv pip compile requirements.in -o requirements.txt` or migrate to `uv` end-to-end. |
| The intended Compose topology and the running system have drifted historically (we just saw it reconverge). | Make one deployment authoritative | Choose all-Docker or all-host as the canonical model and remove the other path. The `LAI/docker-compose.yml` + `micro-services/docker-compose.yml` pair, brought up via `scripts/ops/start.sh`, is the most coherent target. |
| **Docs drift from running reality.** `WORKFLOW.md` says "8 M embeddings (~127 GB)" — actual 9.46M / 155 GB. Says "the fuller design also has CRAG and citation verification in `lai.generation`" — that whole package is dead code. Says backends "share the same PostgreSQL" — only the *models*, not the database, are shared in the live state. | Targeted edits | The three specific lines in `WORKFLOW.md` (see `harsh/VERIFICATION.md`); update `serve_rag.py` docstring; update the README's evaluation numbers. |
| Frontend deployed via Vercel/Cloudflare (`wrangler.json`, Vercel config) — conflicts with the on-prem mandate. | Serve from the on-prem host | Build the React app once; serve `dist/` from Nginx or Caddy on the same box behind the auth layer. Delete `wrangler.json` and the empty `worker/` stub. `VITE_BACKEND_URL` points to the local backend. "Contracts never leave the building" becomes a real claim. |

## 7. Corpus and data quality

| Issue | Fix | How |
|---|---|---|
| Step 6 embedding is **~81% incomplete** — 40.5M of 50M child chunks still have `embedding IS NULL`. | Resume + monitor + decide policy | `resume_step6.sh` is the existing runner. Policy decision: finish-before-migrate vs migrate-9.46M-then-stream. Either way, embedding throughput becomes a tracked operational metric. |
| The legacy `data_processing/` directory (top-level, 43 GB) is dead code — not git-tracked, imported by nothing, superseded by `LAI/src/lai/pipeline/`, targets the old 1024-dim `law_chunks` schema. | Archive or delete | After confirming nothing actively reads from it (last verified — nothing does), move to `older_versions/` or delete entirely. Confusion liability. |
| FTS5 BM25 index is built once and goes stale on new rows (`eval.py:139` only builds if absent). | Rebuild as Postgres FTS after migration | Once the corpus is in pgvector, BM25 becomes Postgres `tsvector` + `tsquery` maintained per row automatically. |
| **15.8% fabricated citations** in the synthetic training data (`audit_results.json` confirms 84.16% verified). Fine-tuning is shelved as a result. | Verification loop inside generation | Step 5's `generate.py` adds a post-generation grounding check: regex-extract `§` / `Art.` / `Klausel` references from each answer, confirm presence in the source chunk, reject + regenerate on failure. The existing `audit_training_data.py` becomes a CI gate that fails the build if verified-citation rate drops below threshold. Fine-tuning can then resume on clean data. |
| No corpus reindex endpoint — corpus growth requires a cold `serve_rag` restart (multi-minute downtime as 155 GB reloads). | Online ingest into pgvector | Solved by the keystone migration — pgvector supports online upserts; the reload model goes away. |
| No feedback capture today. The `lai_feedback` table already exists in `pipeline_local.db` but is unused. | `POST /feedback` + correction memory | Capture original/corrected/reason keyed by session + message id (message-id plumbing already exists in `persistence.add_message`). Store in pgvector as a small `corrections` table; on each new extraction pass, retrieve the most similar past corrections and inject them as few-shot guidance ("on similar documents, lawyers corrected X→Y because Z"). No GPU retraining — the model improves from inference-time context. This is the "should learn" piece. |

---

# What the new building blocks are

Five components don't exist today — these are the pieces the fixes above keep
referencing. They are not "code fixes." They are new architectural elements:

1. **`lai.common` — the shared library.** One PDF extractor, one chunker, one
   embedding client, one reranker client, one LLM client (with `<think>` strip
   + schema-enforced output + retries), one JSON-salvage helper. Section 3.
2. **`lai.retrieval` — the retrieval router.** Per question, decides which
   sources to query (uploaded docs / corpus / connectors), returns ranked
   chunks with provenance tags. Replaces the hardcoded `rag_context()`.
   Section 2 + 1.
3. **`lai.connectors` — the external-data plugin layer.** `Connector` ABC +
   registry. ALKIS and Nominatim refactored into it; MaStR, Handelsregister
   added on top. All free/public — fits the no-budget constraint. Section 2.
4. **The reconciliation stage + facts ledger.** One canonical `ProjectFacts`
   object every pass reads from and writes into; a deterministic reconciler
   forces consistency. Section 1.
5. **The validation/guardrail layer.** One pipeline stage between extraction
   and rendering that enforces: location plausibility, single language,
   schema compliance, source-tag honesty, no defensive prose. Section 1.

Plus the structural moves:
- **Delete the dead stack** (~3,200 LOC). Section 3.
- **Migrate the corpus into pgvector** (`halfvec(4096)` + HNSW). Section 2.
- **Add auth + tenant isolation.** Section 4.
- **Move the frontend on-prem.** Section 6.

---

# How "good output" gets measured

When all of the above lands, the same Lamstedt smoke test should produce — and
this is the bar to grade against:

1. A non-empty Findings chapter — no "extraction failed" placeholder.
2. Turbines plotted in Landkreis Cuxhaven (or flagged `unverified` honestly).
3. One consistent turbine count across text, math, title, and table.
4. Every "missing" item rendered as a red-flag with the governing statute cited
   (e.g. "§35(5) BauGB Rückbaubürgschaft — request from client").
5. One language end-to-end.
6. No "consult a Fachanwalt" filler in the body.
7. Every fact carries a visible source tag.
8. The report PDF renders as clean tables (already true in the current PDF;
   re-confirmed in `harsh/VERIFICATION.md`).

If we can't deliver those eight, the rest doesn't matter. If we can, the
remaining gap to "best legal AI for German wind-energy DD" is the
public-registry connectors and the feedback loop — and those are not
architectural risks, they are scope.
