# LAI v1 — Remaining Tasks (cross-referenced with IMPLEMENTATION_GUIDE §9)

**Date:** 2026-05-19
**Branch:** `v2-restructure`
**Live state when this was written:**
- HNSW build: **95.14% loading-phase complete** (PID 246654, 21h+ runtime, 201 GB on disk)
- Step 6 embed: 🟢 running (PID 3465973)
- Topup daemon: 🟢 streaming new rows to pgvector (PID 1925389)
- Watcher: 🟢 polling (PID 600743 → 600746)

This file walks every issue in `IMPLEMENTATION_GUIDE.md §9` (the
canonical task catalog) and marks each as one of:

| Symbol | Meaning |
|---|---|
| ✅ | Done + committed + (where applicable) verified live |
| 🟢 | In flight / background process running |
| 🔄 | Code committed but live runtime is stale — needs serve_rag restart |
| 🟡 | Partial / scoped down |
| ❌ | Not addressed |
| ⛔ | Intentionally deferred (with reason) |

---

## §9.1 — Report output quality

| # | Issue | Status | Evidence |
|---|---|---|---|
| **A1** | `generate_findings` per-finding iteration | ✅ | Commit `45dd415` — verified live: 146s LLM call, structured Finding |
| **A2** | Geocoding plausibility gate + cache TTL | ✅ | Commit `e58951a` — bbox check via Nominatim-sourced 16 Bundesland boxes; verified rejects Cuxhaven→Bremen |
| **A3** | Provenance enum (`source_kind ∈ uploaded_doc/legal_corpus/registry/estimated/unverified`) | ❌ | Citation chips have `corpus/matter` (commit `8431797`); cadastral parcels still don't carry typed provenance. Renderer + ALKIS-vs-synthetic distinction not yet done. |
| **A4** | Deterministic reconciler | ✅ | Commit `76b753e` — `Candidate` + `Reconciled` + precedence `cadastral > llm > regex > fallback`; wired for `total_capacity_mw`, `turbine_count`, `bundesland` |
| **A5** | Statutory grounding via retrieval router | ❌ | Blocked on `lai.retrieval` package — which is blocked on HNSW completing |
| **A6** | Facts ledger (`ProjectFacts` canonical object) | ❌ | Not started. WEA rows still re-extracted; "same paragraph 6×" failure mode still possible |
| **A7** | Structured location model | ❌ | Address column still flattened to paragraph in `WEAStatus.address` (`ddiq_report.py`) |
| **A8** | Single-language enforcement | 🟡 | `_guardrail.py` `scrub_finding_text(target_language=...)` flags mixed-language rows; backend `target_language` field exists in `QueryReq`. **Mid-sentence switch detection + re-prompt** not done. |
| **A9** | Hedge-strip / disclaimer strip | ✅ | Commit `3b318a4` — `_guardrail.detect_defensive_ai` patterns sourced from real `ddiq_reports` rows |
| **A10** | WEA specs prompt + Docling TableFormerMode.ACCURATE | ❌ | Not addressed |

**Remaining in 9.1:** A3, A5, A6, A7, A8 (re-prompt half), A10. **5.5 of 10 done.**

---

## §9.2 — The corpus silo (the keystone)

| # | Issue | Status | Evidence |
|---|---|---|---|
| **B1** | Unified retrieval over pgvector | 🔄 | Migration done (commit `aac355d` + 4 fix commits); HNSW building (95% loading). `_do_rag` swap to pgvector is the next commit when index is valid. |
| **B2** | One Postgres | 🔄 | Data + parents + children in `lai_postgres_main`. Still need to retire the host-process port-5435 Postgres (T2) and switch `serve_rag` reads. |
| **B3** | Step 6 ~81% incomplete | 🟢 | Running (PID 3465973). At ~24% on 2026-05-19; ETA ~14 days. Topup streams every new row into Postgres as Step 6 produces them. |
| **B4** | Retrieval router (`lai.retrieval`) | ❌ | New package not created. Needs HNSW valid + design for `MatterCollection` filter so auth's `user_id` flows in correctly. |
| **B5** | `EXTERNAL_LAW_REFS` keyword gate | ✅ | Removed earlier (Sahid's pre-v2-restructure work; verified absent from `serve_rag.py:1162-1168`) |
| **B6** | `lai.connectors` package (MaStR, Handelsregister, expanded ALKIS) | ❌ | Not created. ALKIS still hand-rolled in `ddiq_report.py`; Nominatim too. |

**Remaining in 9.2:** B1 finalise (swap), B2 finalise (retire host-process pg), B4 (`lai.retrieval`), B6 (`lai.connectors`). **2 of 6 done outright; 2 in flight; 2 pending.**

---

## §9.3 — Codebase fragmentation

| # | Issue | Status | Evidence |
|---|---|---|---|
| **C1** | ~3,200 LOC dead code delete | ✅ | Commit `8431797` — `auth/`, `documents/`, `extraction/`, `generation/`, `infra/` purged; `api/main.py`, `api/pipeline.py` deleted; Postgres-backed bits of `search/` deleted |
| **C2** | `lai.common` shared library | ✅ | 9 subpackages (`llm`, `reranker`, `embedding`, `pdf`, `chunk`, `citation`, `auth`, `jurisdiction`, `exceptions`) |
| **C3** | `ddiq_report.py` decompose | ❌ | **Still 3,168 LOC god-file** (was 2,463; growing under feature pressure). Split into `db.py`/`models.py`/`extractors/`/`routes.py`/`pipeline.py`/`connectors/` not started. |
| **C4** | Adopt analyzer reconciler pattern | ✅ | Commit `76b753e` — pattern in `_reconcile.py` |

**Remaining in 9.3:** C3 only. **3 of 4 done.**

---

## §9.4 — Security & tenant isolation

| # | Issue | Status | Evidence |
|---|---|---|---|
| **S1** | JWT + `Depends(get_current_user)` on every route | ✅ | Commits `c15f2f1` + `85008f1` — Sumit's auth subsystem; `users`, `refresh_tokens`, `password_reset_tokens` tables exist in `lai_postgres_main`. **Live blocker: serve_rag PID 3413088 is stale (from 2026-05-15); needs restart for auth to actually fire.** |
| **S2** | `user_id` on DDiQ tables | 🟡 | **Per-table verified:** `ddiq_documents`, `ddiq_reports`, `ddiq_project_areas`, `ddiq_contracts`, `ddiq_classified_parcels` HAVE `user_id`. `ddiq_doc_chunks`, `ddiq_contract_parcels`, `ddiq_geocode_cache`, `ddiq_parcel_cache` do NOT (chunks/parcels reach user via FK join; caches are deliberately shared — these are correct per AUTH_PLAN). Filter-every-query still needs to be audited route by route. |
| **S3** | Frontend `AuthContext` rewrite | ✅ | LAI-UI commit `4474388` — `utils/jwt.ts` deleted; real `auth/` package; SSE + token-rotation client wired |
| **S4** | CORS env allow-list | ❌ | `serve_rag.py:1234` still `allow_origins=["*"]`. `micro-services/api.py:53` is env-driven (`_cors_origins`). Half-done. |
| **S5** | Rotate HF token in `Docker/inference_engine/.env:11` + secret store | ❌ | Not done |
| **S6** | Hardcoded default credentials in `core/config.py:38, 84, 273` | ❌ | Not addressed (`core/config.py` survived the dead-stack purge — likely now dead too; needs grep + delete) |

**Remaining in 9.4:** S2 (route audit), S4 (serve_rag CORS), S5, S6. **2 of 6 done; 1 partial.**

---

## §9.5 — Engine fault tolerance

| # | Issue | Status | Evidence |
|---|---|---|---|
| **E1** | Schema-enforced LLM output + typed empty fallback | ✅ | `lai.common.llm` does `response_format: json_schema` (ADR 0004); `llm_json` returns `{}` on hard failure |
| **E2** | `_parse_alkis_feature` invert control flow | ✅ | Commit `1f8682e` — `pass; break` bug fixed; verified with `flurnummer=abc, flur=7` regression case |
| **E3** | `llm_json` double-failure uncaught | ✅ | Commit `501a315` — `llm_json` returns `{}` on hard failure |
| **E4** | `request_fingerprint` UNIQUE index | ✅ | Commit `1f8682e` — `CREATE UNIQUE INDEX … WHERE … IS NOT NULL`; verified `UniqueViolation` on duplicate insert |
| **E5** | Fingerprint set at row creation | 🟡 | Async path was already correct; sync `/report/generate` got the outer try/except wrap in `1f8682e` but the fingerprint is still set AFTER pipeline completes (line ~2609). Concurrent-dedup race still possible on sync path. |
| **E6** | Sync path try/except + mark `failed` | ✅ | Commit `1f8682e` |
| **E7** | Aux-table `ON CONFLICT` | ✅ | Commit `1f8682e` for `ddiq_parcel_cache`. `ddiq_classified_parcels` / `ddiq_contracts` / `ddiq_project_areas` aux-inserts in `_generate_report_core` still need audit. |
| **E8** | Cache TTL on geocode + parcel | ✅ | Commits `e58951a` (geocode, 90-day) + `1f8682e` (parcel, 30-day) — `expires_at TIMESTAMPTZ` column; reads filter `expires_at > NOW()` |
| **E9** | Evidence rollup silent index drop | ❌ | `ddiq_report.py:~1620` still silently drops out-of-range LLM indices |
| **E10** | `_evidence` on `__dict__` → real Pydantic field | ❌ | Still `row.__dict__.get("_evidence")` |
| **E11** | OCR quality gate (alphabetic-ratio + mojibake) | 🟡 | `lai.common.pdf.PdfExtractorConfig.min_chars_per_page=50` matches legacy. **Alphabetic-ratio and mojibake-pattern checks still not implemented.** |
| **E12** | tenacity retries on every external HTTP | 🟡 | LLM ✅ (lai.common.llm), embed ✅ (lai.common.embedding), reranker ✅ (lai.common.reranker). **ALKIS + Nominatim still raw `requests` in DDiQ — no retries.** |
| **E13** | DDiQ async `ThreadPoolExecutor` → Celery worker | ❌ | Not done. Celery declared in `pyproject.toml` but worker not deployed. |
| **E14** | LLM-serving redundancy + 7B chat model | ⛔ | Scaling ceiling, deferred per the guide |

**Remaining in 9.5:** E5 (sync path fingerprint timing), E7 (other aux tables), E9, E10, E11 (ratio/mojibake checks), E12 (ALKIS/Nominatim), E13. **6 of 14 done outright; 3 partial; 4 pending; 1 deferred.**

---

## §9.6 — Operational layer

| # | Issue | Status | Evidence |
|---|---|---|---|
| **O1** | Tests | 🟡 | **24 test files** in `LAI/tests/`. Strong coverage on `lai.common.*` (505 unit + 12 integration). DDiQ + serve_rag have zero tests. The frontend has none. |
| **O2** | Monitoring stack deployed | ❌ | No `lai_prometheus` / `lai_grafana` / `lai_alertmanager` containers running. (`ctcap-geo-*` are from a different project on the same host.) The `lai.common.*` modules emit Prometheus metrics with nobody scraping. |
| **O3** | SSE streaming via `sse-starlette` | 🔄 | Code shipped (commit `a67088c` brought `POST /query/stream`). **Live serve_rag (PID 3413088) returns HTTP 404 — stale runtime; needs restart.** Frontend already calls `streamQuery` (Sumit's `4474388`). |
| **O4** | Pin Docker images + lockfile microservice deps | ❌ | `vllm:latest`, `prometheus:latest`, etc. all unpinned. `requirements.txt` uses `>=`. |
| **O5** | Frontend on-prem | ❌ | `LAI-UI/vercel.json` and `LAI-UI/wrangler.json` **still present**. Frontend on-prem move not started. |

**Remaining in 9.6:** O1 (full coverage), O2, O3 (just needs serve_rag restart), O4, O5. **0 of 5 fully done; 1 staged for restart; 1 partial.**

---

## §9.7 — Corpus & data quality

| # | Issue | Status | Evidence |
|---|---|---|---|
| **D1** | Step 6 ~81% incomplete | 🟢 | Running. ~24% as of 2026-05-19; ETA ~14 days |
| **D2** | Top-level `data_processing/` dead legacy | ❌ | Not audited; likely still there |
| **D3** | 15.8% fabricated citations in synthetic training data | ❌ | Not addressed |
| **D4** | `POST /feedback` + correction memory | ✅ (endpoint) / ❌ (memory) | Endpoint at `serve_rag.py:2380` (commit `85008f1`). **Correction-memory retrieval into prompts NOT done.** Frontend UI: thumbs already wired in `ChatMessage.tsx` (commit `4474388`). |
| **D5** | SQLite FTS5 staleness | 🟡 | Moot post-migration. Interim drop-rebuild not done; will retire entirely once `_do_rag` swap lands. |

**Remaining in 9.7:** D2, D3, D4 (correction memory). **1 of 5 fully done; 2 partial; 2 pending.**

---

## §9.8 — Topology and configuration drift

| # | Issue | Status |
|---|---|---|
| **T1** | `lai_neo4j` running, not in any compose | ❌ — still running, no compose |
| **T2** | Postgres port mismatch (`:5434` vs `:5435`) | 🟡 — `:5434` (lai_postgres_main) is canonical post-migration; `:5435` host-process Postgres can now be retired |
| **T3** | Embedding-dimension drift (1024 vs 4096 in legacy configs) | ❌ |
| **T4** | Reranker three-way definition | ❌ |
| **T5** | Celery declared but no worker | ❌ (related to E13) |
| **T6** | `vllm` Python dep unused | ❌ |
| **T7** | `LAI-UI/.env.example` drift | 🟡 — Sumit's auth work may have touched, verify |
| **T8** | `.env.example` model-set drift | ❌ |

**Remaining in 9.8:** all 8 (most cosmetic, but T1/T2/T3/T8 are real cleanups). **0 of 8 done.**

---

## §4 — The four structural moves (high-level moves, not §9 issues)

| Move | Status |
|---|---|
| **M1 — Unify storage (pgvector keystone)** | 🔄 In progress (HNSW 95% loading) |
| **M2 — Delete dead code + extract `lai.common`** | ✅ Done |
| **M3 — Auth + tenant isolation** | 🔄 Code ✅ (Sumit); needs serve_rag restart + S2 route audit + S4 CORS |
| **M4 — Frontend on-prem** | ❌ Not started (O5) |

---

## §5 — The five new building blocks

| Block | Status |
|---|---|
| **`lai.common`** | ✅ 9 subpackages shipped |
| **`lai.retrieval`** | ❌ Not created |
| **`lai.connectors`** | ❌ Not created |
| **Facts ledger + reconciler** | 🟡 Reconciler ✅; facts ledger ❌ |
| **Validation / guardrail** | ✅ Shipped (`_guardrail.py`) |

---

## Summary — what's actually left

### High-priority, blocks demo

1. **Track B `_do_rag` swap** (HNSW completes → ~few hours from now). Unblocks B1, B2.
2. **serve_rag restart** — bundles live: SSE (O3), auth (S1), citation_validation, jurisdiction_warnings, target_language, the new prompt model. Coordinate after the swap commit.
3. **S4 — serve_rag CORS** drop `["*"]`. One line change.
4. **`lai.retrieval` package** (after `_do_rag` swap) — unblocks A5 statutory grounding.

### Medium-priority, structural

5. **A3 — Provenance enum** for cadastral facts (synthetic vs ALKIS).
6. **A6 — Facts ledger** (`ProjectFacts`).
7. **A7 — Structured location model**.
8. **A10 — WEA specs prompt + Docling TableFormerMode.ACCURATE**.
9. **C3 — Split `ddiq_report.py`** (3,168 LOC god-file).
10. **`lai.common.connectors`** (B6) — extract ALKIS + Nominatim, then add MaStR + Handelsregister (Phase 2B).
11. **E12 — tenacity retries on ALKIS + Nominatim**.
12. **E13 — Celery worker for DDiQ async** (or document the executor + crash-recovery as v1 acceptable).

### Low-priority, observability + housekeeping

13. **O1 — Tests for DDiQ + serve_rag** (`lai.common.*` is well-covered; the call sites aren't).
14. **O2 — Stand up Prometheus + Grafana** (containers + scrape config; metrics already emitted).
15. **O4 — Pin Docker images + lockfile microservice deps**.
16. **O5 — Frontend on-prem move** (delete vercel.json / wrangler.json; serve dist/ from nginx).
17. **D2 — Audit + delete `data_processing/`** dead tree.
18. **D4 — Correction-memory retrieval** into prompts (endpoint exists; memory side not done).
19. **S2 — Full route-by-route audit** that every DDiQ endpoint applies the `WHERE user_id` filter.
20. **S5 — Rotate HF token** + move to secret store.
21. **S6 — Remove hardcoded defaults** in `core/config.py` (likely a dead-stack survivor now).
22. **T1–T8 — Topology drift cleanups** (mostly cosmetic but T1/T3/T4 are real).

### Cumulative count

- **§9 issues**: 16 fully done, 8 partial / staged-for-restart, 24 not started, 1 deferred → **~33%** of §9 closed; with the staged restart that becomes ~45%.
- **§4 moves**: 1 of 4 fully done, 2 in flight.
- **§5 building blocks**: 2 of 5 done, 1 partial.

---

## Live processes right now

```
PID 246654    Postgres HNSW build         95.14% loading-phase    21h+ runtime
PID 600746    Watcher                     polls every 60s         alive
PID 1925389   Migration topup daemon      streaming 4000-d        alive
PID 3465973   Step 6 embedding (GPU)      24.x% of 49.95M         22h+ runtime
PID 3413088   serve_rag (rj's process)    pre-c15f2f1 STALE       3d 8h+ runtime
              (this is the one needing restart after _do_rag swap)
```

When the watcher notifies that the HNSW index is valid, the immediate
plan is:

1. ANALYZE + probe latency (target: 50-150 ms)
2. Write the `_do_rag` swap (replace numpy in-RAM mat-mul with a
   pgvector `ORDER BY embedding <=> $1` kNN query)
3. Commit, push, build serve_rag wheel/image (if container-deployed)
   or coordinate with rj/sa for the host-process restart
4. Restart serve_rag — bundles SSE / auth / citation_validation /
   jurisdiction_warnings / target_language live in one downtime
   (~14 min today on stale; <1 min after the swap because pgvector
   avoids the 144 GB in-RAM matrix load).
