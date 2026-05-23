# LAI v1 — Demo Status

**Date:** 2026-05-18
**Branch:** `v2-restructure`
**Demo deadline:** 10-day sprint (Day 8 of 10)

One-stop view of where v1 stands against the strategy doc. Every row says
whether the work is **built**, whether it is **wired into the live
chat pipeline**, and what's left.

---

## Headline

- Backend: **the four lawyer-blockers from the 2026-05-15 review are all fixed and wired into `serve_rag`.**
- Frontend: **citation chips, jurisdiction warnings, PDF preview, streaming, and the feedback buttons are all wired in.**
- Knowledge base: **24.6 % of the 49.95 M corpus chunks embedded; pgvector migration running in parallel; chat works against the embedded subset already.**
- Demo blockers: **auth + LAI-UI demo expansion uncommitted (Sumit), demo seed PDFs not curated, lawyer rehearsal not done.**

Estimate: **3 focused working days** between today and a demo we can put in front of the lawyer.

---

## What's done — and is it wired?

"Wired" = the lawyer can see / use it end-to-end via the UI talking to `serve_rag`.

### 1 · Lawyer's four v0 blockers — all fixed

| v0 complaint | Fix shipped | Wired in pipeline |
|---|---|---|
| "Bremen instead of Cuxhaven" (wrong-Bundesland citations) | `lai.common.jurisdiction.check_jurisdiction` + `JurisdictionWarning` route output | ✅ Wired in `/query` + `/query/stream`; UI renders amber chip |
| "(unverified)" / hallucinated citations | `lai.common.citation.validate_citations` — strips fabricated `[C-n]`/`[M-n]`, rewrites sentence to end `(unbelegt)` | ✅ Wired post-LLM in both query endpoints; UI shows "N unbelegt sources stripped" badge |
| "No clickable citations" | `[C-n]` / `[M-n]` handles in retrieved chunks; `CitationChip` + `CitationPanel` in UI | ✅ Every grounded reply renders chips; click opens excerpt panel or PDF preview |
| "Findings extraction failed" placeholder | DDiQ `generate_findings` rewritten per-row with structured `[extraction_failed]` markers | ✅ Shipped in `ddiq_report.py`; DDiQ is **not on the demo path** (deferred to v1.1) |

### 2 · Core chat features (strategy doc §9)

| Feature | Built | Wired |
|---|---|---|
| Chat against uploaded PDFs + corpus, always both | ✅ — `EXTERNAL_LAW_REFS` gate removed; RAG fires whenever a contract exists | ✅ |
| `[C-n]` / `[M-n]` citation tags in every reply | ✅ | ✅ |
| Click `[C-n]` → side panel with corpus excerpt | ✅ | ✅ |
| Click `[M-n]` → native browser PDF preview via `<object>` (no pdfjs) | ✅ `GET /sessions/{id}/document` + `CitationPanel` fetch | ✅ |
| Streaming token output (SSE) | ✅ `POST /query/stream` + `streamQuery` | ✅ DashboardChat uses `streamQuery` |
| Conversation memory across turns | ✅ Persistence + history loader + pinned session meta | ✅ |
| `(unbelegt)` badge | ✅ | ✅ |
| Jurisdiction-mismatch warning | ✅ | ✅ UI chip in DashboardChat |
| Bilingual EN / DE toggle | ✅ `target_language` on `/query` + `LanguageToggle` | ✅ |
| "On-Premise · BRAO § 43a · DSGVO · EU AI Act" badge | ✅ `ConfidentialityBadge` mounted in sidebar | ✅ |
| Lawyer thumbs-up / thumbs-down feedback | ✅ `POST /feedback` + per-bubble buttons in `ChatMessage` | ✅ Optimistic UI + persisted-verdict rehydration on reload |
| Prometheus `/metrics` + Grafana dashboard | ✅ `prometheus-fastapi-instrumentator` + `lai.api.metrics` + 9-panel dashboard | ✅ Backend emits; stack runs via `docker compose -f infra/monitoring/docker-compose.yml up` |

### 3 · Foundation work (Phase 1a + 1b Track A)

| Module | Status | Used by |
|---|---|---|
| `lai.common.llm` (SyncLlmClient + metrics + strip_think + json_salvage) | ✅ 100 % cov | `serve_rag.llm_generate` (remote path), DDiQ `llm_call` / `llm_json` |
| `lai.common.reranker` | ✅ 100 % cov | `lai.search.eval.Reranker` (used in `serve_rag._do_rag`) |
| `lai.common.embedding` | ✅ 100 % cov | `lai.search.eval.embed_query` (used in `serve_rag._do_rag`) |
| `lai.common.pdf` | ✅ 86 % cov | Standalone; not yet swapped into upload path |
| `lai.common.chunk` | ✅ 100 % cov | Standalone; not yet swapped into ingest path |
| `lai.common.citation` | ✅ 100 % cov | `serve_rag` post-LLM validator |
| `lai.common.jurisdiction` | ✅ 100 % cov | `serve_rag._run_jurisdiction_check` |

### 4 · Eval + ops

| Item | Status |
|---|---|
| Golden-question fixture (`tests/fixtures/golden_de.json`, 5 questions) | ✅ |
| Golden retrieval sanity runner (`scripts/eval/golden_retrieval_sanity.py`) | ✅ 5/5 PASS against live serve_rag |
| Demo seed loader scaffold (`scripts/ops/load_demo_matter.py`, session `lamstedt-demo`) | ✅ scaffold; **PDFs not curated yet** |
| Prometheus + Grafana stack (`infra/monitoring/`) | ✅ |

### 5 · Knowledge base

| Item | State |
|---|---|
| Step 6 corpus-embedding job | 🟢 Running. 12.27 M / 49.95 M = **24.6 %** complete. ETA ~14 days at ~38 vec/s |
| pgvector migration (parents) | ✅ 13.8 M rows, done in 33 min |
| pgvector migration (children) | 🟢 Running. ~226 K / 12.27 M = ~2 %, ETA ~16 h |
| HNSW index build | ⏳ Auto-chains after children |
| `topup` daemon (streams new Step-6 embeddings into Postgres) | ⏳ Auto-chains after index |
| serve_rag retrieval source | **Still SQLite in-RAM matrix.** Switch to pgvector is a separate commit, post-index. |

The 24.6 % embedded subset is **enough for the demo** — every retrieval-sanity question lands on rows that are already embedded. Wind-energy questions hit 100 % embedded coverage.

---

## What's left for the demo

Three buckets: **must commit before demo**, **must build before demo**, **must rehearse**.

### Must commit (work is done; sitting in working trees)

- **Sumit's auth** — `api/auth_router.py`, `common/auth/`, the `001_auth_and_tenant_isolation.up.sql` migration, modifications to `serve_rag.py`. Still uncommitted on this branch. **Demo cannot ship without this** (lawyer expects login).
- **LAI-UI demo expansion** (separate repo) — auth pages, LanguageToggle, ConfidentialityBadge, jurisdiction warning rendering, SSE wiring in DashboardChat, PDF preview in CitationPanel. Still uncommitted in `LAI-UI`. Each session-end without a commit risks losing it.
- **Today's `/feedback` + `/metrics`** — landed in working tree (this session). Commit as one feature commit on `v2-restructure`.

### Must build before demo

| Item | Owner | Effort | Why |
|---|---|---|---|
| Curate **demo seed PDFs** for "Windpark Lamstedt — Acquisition DD" | operator (Sahid) | ½ day | The `load_demo_matter.py` scaffold expects 6–8 curated PDFs in `demo-seed/lamstedt/`. Empty today. Without this, the demo opens to a blank Matter. |
| **Matter workspace data model** (strategy doc §9 Day 5–6) | backend | 1 day | `matters` table, `matter_documents` join, `/matters` routes. Today every session is its own "Matter". |
| **Sidebar Mandanten list with Bundesland pill** | UI | ½ day | Frames the demo as "real workspace, not a chat playground". |
| **Loading skeletons, error states, empty states** | UI | ½ day | Strategy doc §9 lists this as MUST. Today the empty state is "Click to upload". |
| **Switch serve_rag retrieval to pgvector** | backend | 2–4 h once HNSW finishes | Optional for demo — the SQLite in-RAM matrix already works. Do this only if migration completes in time and the bench shows a clear win. |

### Must rehearse

- Run the **5-minute demo script** (strategy doc Appendix A) **3 times end-to-end** the day before. Fix the top 5 paper-cuts.
- **Pre-warm `serve_rag`** before the lawyer arrives — startup is ~5 min to load the in-RAM matrix.
- **Pre-cache the Lamstedt session** — open the demo URL once on the demo laptop so embeddings + reranker are warm.

---

## How far from a competitive demo?

**Engineering critical-path:**

- Demo PDFs curation — ½ day
- Matter workspace (data model + routes + sidebar) — 1.5 days
- UX polish (skeletons, empty states, error states) — ½ day
- Sumit's auth merge + smoke test — ½ day
- Rehearsal — ½ day

**Total: ~3 focused working days.**

Everything that made the lawyer dismiss v0 in 30 seconds is fixed at the backend and is rendering in the UI:

| 2026-05-15 complaint | Status today |
|---|---|
| Map of Bremen instead of Cuxhaven | Fixed (jurisdiction validator + warning chip) |
| "Auto-generated, does not substitute legal review" footer | Replaced by `ConfidentialityBadge` (positive framing) |
| `"findings extraction failed"` in tables | Fixed in DDiQ; DDiQ off-demo anyway |
| No clickable citations | Fixed (`[C-n]`/`[M-n]` chips + side panel + PDF preview) |
| No workflow integration | **Still out of scope** for v1 (Beck-online / juris / Outlook deferred) |
| No firm branding on the deliverable | **Still out** — DOCX firm-letterhead is v1.1 |
| No visible moat ("why this and not Harvey?") | Partial — on-prem badge + bilingual + citation rigor + jurisdiction sanity; demo script needs to lean on this |

The two unresolved items (workflow integration, firm-letterhead) are **honest v1.1 deferrals** documented in the strategy doc §9.1. The demo script in §Appendix A is designed to make the lawyer feel the moat without those.

---

## Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Sumit's auth uncommitted** — could lose work; could land breaking changes mid-rehearsal | Sumit commits today / tomorrow; smoke-test the auth flow once on `v2-restructure` |
| R2 | **LAI-UI uncommitted** — same risk on the frontend repo | Same — commit before next session-end |
| R3 | **pgvector migration could fail** during the ~16 h children run | Resumable via `last_child_id` high-water; orchestrator has tenacity retry + SIGTERM-graceful shutdown. **Demo does not depend on this finishing** — SQLite path still works. |
| R4 | **Step 6 only at 24.6 %** — lawyer asks "is the full corpus in there?" | Honest answer: "Wind-energy 100 % embedded; rest streams in over the next 14 days." Strategy doc §4 frames this. |
| R5 | **Demo PDFs not curated** | Operator task; ½ day; blocks `load_demo_matter.py` |
| R6 | **No lawyer rehearsal yet** | Schedule one rehearsal-with-self-on-laptop the day before; one rehearsal-with-Sumit-or-Harsh playing lawyer the morning of |

---

## Open decisions (still need an answer)

| # | Question | Recommended answer |
|---|---|---|
| Q5 | German RDG / EU AI Act positioning in the pitch? | Counsel review — flag to legal advisor before the lawyer asks |
| Q7 | `--gpu1` flag for Step 6 to halve ETA? | Not for demo (24.6 % is enough); decide post-demo |
| Q9 | Legacy-tree lint cleanup? | Defer — not demo-critical |
| Q11 | Trigger latency follow-ups for `generate_findings`? | Defer — DDiQ off-demo |
| Q12 | When do Sumit + the LAI-UI tree get committed? | **Before next session-end.** This is the highest-leverage unblocker. |
| Q13 | Order of remaining parallel work? | Demo PDFs → Matter workspace → UX polish → rehearsal |

---

## Pointers

- [docs/LAI_V1_STRATEGY.md](LAI_V1_STRATEGY.md) — strategy doc; sections 5 (eval), 9 (feature list), 10 (10-day roadmap), Appendix A (demo script)
- [docs/UI_GUIDE.md](UI_GUIDE.md) — per-screen UI design + backend reference table
- [scripts/eval/golden_retrieval_sanity.py](../scripts/eval/golden_retrieval_sanity.py) — retrieval-health probe (5/5 PASS today)
- [scripts/ops/load_demo_matter.py](../scripts/ops/load_demo_matter.py) — demo seed loader (PDFs missing)
- [scripts/ops/resume_migration.sh](../scripts/ops/resume_migration.sh) — pgvector migration wrapper
- [infra/monitoring/docker-compose.yml](../infra/monitoring/docker-compose.yml) — Prometheus + Grafana stack
- `LAI/logs/migration/start_all.latest.log` — live migration log
- `git log --oneline v2-restructure | head -25` — recent commit history
