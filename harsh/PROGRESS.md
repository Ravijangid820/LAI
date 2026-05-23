# LAI V1 — Build Progress

**Date:** 2026-05-21
**Branch:** `v2-restructure` (shared — both Harsh + Sahid commit here)
**Scope:** committed work, the keystone landing, the report-throughput
fix, live infra state, and what's next.

Companion docs: `harsh/IMPLEMENTATION_GUIDE.md` (§9 catalog),
`harsh/REMAINING_TASKS.md` (issue→commit cross-ref, now itself stale —
this file supersedes it), `harsh/SECURITY_RISKS.md` (SR-1).

---

## Headline status

**Two keystones landed since the 2026-05-19 snapshot:**

1. **Track B keystone is IN — `5230e86`.** Sahid shipped the pgvector
   `_do_rag` swap + `lai.common.retrieval` (S-1, S-2, S-4, S-5, S-6).
   serve_rag now retrieves from pgvector (HNSW, ~12.5 ms) instead of the
   144 GB in-RAM matrix. The "shipped-but-not-connected" gap the boss
   called out on retrieval is closed.

2. **DDiQ report generation now completes — fast — `8d9c3e5` + the E/A waves.**
   The report used to time out (0 findings). Root cause turned out to be
   a `lai.common.llm` client bug: `chat_template_kwargs` was nested under
   `extra_body`, which vLLM ignores, so `thinking_mode_enabled=False` was
   silently a no-op and every DDiQ call ran ~65–150 s in thinking mode.
   Fixed (top-level) → **structured calls now ~0.3–1.5 s** (verified live
   in `lai-backend`). Combined with the §14 report-quality fixes, a full
   Lamstedt report now completes with real content.

**serve_rag is no longer stale** — it has been restarted: auth enforced,
`/query/stream` live, citation/jurisdiction fields present, llm remote
qwen3.6-27b. Verified end-to-end this session.

Live processes:

| Process | Status | Notes |
|---|---|---|
| HNSW index | ✅ `indisvalid=t`, 334 GB | incrementally absorbs topup rows |
| Corpus → pgvector | 🟢 **22.39 M / 49.95 M (~45%)** | topup ~128 k rows/hr, all 4000-d halfvec |
| Step 6 embed (GPU) | 🟢 GPU 1 at 100% | ETA ~10 days to full corpus |
| serve_rag (:18000) | ✅ **fresh, pgvector-backed** | auth + streaming + thinking-off live |
| DDiQ backend/worker | ✅ healthy | thinking-off, Celery limits 120/150 min |

---

## §14 success bar — measured against the live Lamstedt report

Re-ran the real 4-document Lamstedt report through the pipeline three
times as fixes landed:

| Run | State | Findings |
|---|---|---|
| v1 (pre-fix) | ❌ timed out 120 min | 0 |
| v2 (parallel findings) | ⚠️ timed out on tail | 18 |
| v3 (parallel sections) | ✅ **completed** | 21 (real legal content) |

**v3 grade (first complete report):** 4 sections, **21 findings** (OVG
Niedersachsen Rückbau-Urteil, § 16 BImSchG Änderungsgenehmigung,
L6/L7/L9), reconciled `turbineCount=10` + `capMW≈21.8` + bundesland,
jurisdiction warning fired (H-2), Rückbau present, parcels honestly
tagged `estimated` (ALKIS WFS was 530-down — A3 handled it gracefully).

§14 criteria: **1 ✅ findings · 2 ⚠️→fixed (precise geocode 8d9c3e5:
Lamstedt→(53.636, 9.098), no more Bremen) · 3 ✅ one count · 4 ⏳ A5
statute-on-missing (now unblocked) · 5 ✅ single-language (A8) · 6 ✅ no
filler · 7 ✅ source tags · 8 ⚠️ specs partial.**

A clean single-pass re-measure on the now-fast pipeline (thinking
actually off + precise geocode + S-1) is the immediate next step — it
should complete in minutes and confirm the grade with the correct map.

---

## §9 catalog — current status (supersedes REMAINING_TASKS.md)

| Area | Done | Open |
|---|---|---|
| **9.1 Report quality** | A1, A2, A3, A4, A6, A7, A8, A9, A10 | **A5** statutory grounding (unblocked by lai.retrieval) |
| **9.2 Corpus keystone** | **B1, B2, B4, B5** (S-1/S-2 landed), B-retrieval | B6 MaStR/Handelsregister (Phase 2B); B3 = Step 6 streaming (~45%) |
| **9.3 Fragmentation** | C1, C2, C3 (H-5), C4 | — |
| **9.4 Security** | S1, S2 (audit pass), S3, S4, S5 (moot) | **S6 → SR-1** (DB password rotation, rj-coordinated) |
| **9.5 Fault tolerance** | E1, E2, E3, E4, E6, E7, E8, E12, E13 | E5 partial, E9/E10/E11 partial, E14 (serving capacity, deferred) |
| **9.6 Ops** | O1 (tests), O3 (SSE live), O4 (pinned) | **O2** monitoring, **O5** frontend on-prem |
| **9.7 Data quality** | D4 (feedback) | D2/D3, D4 correction-memory |
| **9.8 Drift** | T3/T4/T6/T7/T8 done-or-moot | T1 (neo4j orphan — flagged), T2 (retire host pg) |

Roughly **~40 of 58 §9 issues closed (~70%)**, up from 33% on 2026-05-19.

---

## What shipped this stretch (2026-05-19 → 05-21)

**Harsh (DDiQ + lai.common + ops):** H-1..H-6 + H-6b (embedding/jurisdiction
wiring, connectors, Celery worker, god-file split, 200+ tests); Wave 1
fault-tolerance E5/E7/E9/E10/E11 (`e145611`); report-quality A3/A7
(`48052bc`), A6 (`f7a34ac`), A10/A8 (`642e27f`, `8e0113b`); O4 deps pin
(`43627c0`); T8 env drift (`dac2f4d`); E1 thinking-off + parallel
findings (`45d4461`), E1b parallel sections (`b4940fd`), reconcile
checkpoint (`0ec1195`); CORS fixes DDiQ + serve_rag (`37d8122`,
`f535719`). SR-1 logged.

**Sahid (serve_rag + retrieval):** **S-1/S-2/S-4/S-5/S-6 pgvector keystone**
(`5230e86`); **thinking-mode root-cause + precise geocode + Celery
limits** (`8d9c3e5`); **multi-document Matter chat** [M-1]..[M-n]
(`8d0c376`).

---

## Connectivity (verified this session)

Frontend (`:5173`, LAN IP) ↔ both backends is **connected + working**:
backends healthy, auth enforced, CORS allows `:5173` on both. (A second
non-canonical `:3000` frontend instance was CORS-gapped — fixed in
config, applies on next serve_rag restart, not urgent since `:5173` is
the real one.)

**Document persistence (user-specific, durable):** every uploaded PDF is
retained per `user_id` across days — DDiQ docs in Postgres
(`ddiq_documents`, accumulates), chat docs in `sessions.db` Matters.
Uploading a new PDF never forgets the old. Note: DDiQ-side and chat-side
uploads are *separate* libraries today (unify? — open decision).

---

## Storage footprint

- **Now ≈ 2.1 TB**: Postgres `lai_db` 578 GB (corpus 542 GB incl. 334 GB
  HNSW) + SQLite source 530 GB + raw 671 GB + embeddings 182 GB +
  segments 50 GB + models ~90 GB.
- **When ready (49.95 M chunks)**: pgvector grows ~2.23× → **~1.25 TB**
  (HNSW ~745 GB). Steady-state **~2 TB** if the SQLite source (−530 GB) +
  embedding artifacts are retired post-migration; **~2.8 TB** peak during
  final migration.
- ⚠️ **Headroom**: corpus pgvector needs **+~700 GB** to reach full size;
  confirm free space on the HNSW tablespace disk + plan the SQLite reclaim.

---

## What's next (see "Plan" section the team is tracking)

1. **Clean §14 re-measure** on the now-fast pipeline → confirm the grade.
2. **Resolve the uncommitted batching** (redundant now that thinking is
   actually off → revert) + fix the obsolete `test_falls_back_to_tokens`.
3. **A5 statutory grounding** — the last §14 quality criterion, now
   unblocked by `lai.common.retrieval`.
4. **Lower priority:** O2 monitoring, O5 frontend on-prem, SR-1 password
   rotation (rj), unified document library decision, disk headroom plan,
   E14 serving capacity, B6 Phase-2B connectors.

---

## Open decisions

| # | Question | Status |
|---|----------|--------|
| Q-DL | Unify DDiQ-side + chat-side uploaded-document libraries into one per-user store? | **Open** |
| Q-BATCH | Keep section-question batching (37→4 calls) now that thinking-off makes calls ~1 s? | **Recommend drop** (redundant + per-section parse risk) |
| Q-DISK | Confirm +700 GB headroom for corpus growth + schedule SQLite reclaim | **Open — rj** |
| SR-1 | Rotate the committed-default DB password | **Open — rj** (`harsh/SECURITY_RISKS.md`) |
| Q-A5 | DDiQ statutory grounding via lai.retrieval — scope for v1 vs v1.1 | **Open** |
| E14 | Serving capacity (2nd 27B replica / faster extraction model) | **Deferred** — only matters at >single-digit concurrency |

---

## Honest notes

- **The two things the boss judged "awful" are now fixed end-to-end**:
  retrieval is wired to pgvector (S-1), and the DDiQ report actually
  completes with real findings. Both verified live, not just committed.
- **Shared branch** — Harsh + Sahid both commit to `v2-restructure`; work
  has overlapped (both touched thinking-mode). Sahid's `8d9c3e5`
  root-caused the throughput at the client layer; coordinate to avoid
  duplicate effort going forward.
- **Not yet measured cleanly**: a single-pass §14 completion time on the
  fully-integrated pipeline, and a credentialed frontend click-through
  (lawyer rehearsal still pending per DEMO_STATUS).
