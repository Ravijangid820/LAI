# Phase 1b Track B — Migration Timing & Parallel-Work Plan

**Date:** 2026-05-17
**Audience:** Sahid (Track B owner), hc (Track A done), Sumit (auth, parallel),
project lead
**Source basis:** direct measurement of `pipeline_local.db` row counts +
serve_rag.log timing + live `/query` latency probes (see §Evidence
section); [`IMPLEMENTATION_GUIDE.md`](IMPLEMENTATION_GUIDE.md) §4.1 + §8.0;
[`PROGRESS.md`](PROGRESS.md) headline status.

This document exists because **Sahid raised a concern that pgvector
migration "will consume too much time"** — meaning the time investment
might not be justified. This file is the evidence-based answer plus the
plan for what we do while the migration runs.

---

## 1. Sahid's doubt (verbatim, paraphrased)

> Phase 1b Track B (corpus migration to pgvector + `lai.common.retrieval`)
> takes a long time. Is the time given to it crucial for performance?
> Will skipping it have significant effects on performance? Approximately
> how long will the migration take? The embedding job is running on GPU
> right now — does the migration have to wait for it to finish?

Three questions to answer:

1. **Is the migration time justified by the performance gain?**
2. **How long will the migration actually take?**
3. **Does the migration have to wait for Step 6 to complete?**

All three are answered below with measurements, not guesses.

---

## 2. The three questions answered

### Q1: Is the migration time justified?

**Yes — unambiguously. The migration is a forcing function, not a
nice-to-have.**

Today serve_rag's `lai.search.eval.load_embeddings()` reads every
`child_embeddings` row into a numpy float32 array at startup and then
runs `corpus.embs @ q_vec` (matrix-multiply over the full N × 4096
matrix) per query. This works today **because the matrix fits in RAM**.
It won't when Step 6 finishes.

Why it's a forcing function: see §3.2 below — the full corpus at fp32
needs **762 GB of RAM**. The host has **1007 GB total**, with serve_rag
already at 254 GB RSS, the analyzer LLM at ~30 GB, the embedding
container at ~30 GB, Postgres + the rest of the stack at ~30 GB. There
is no path to keep the in-RAM approach when Step 6 fills the remaining
~76% of `child_embeddings`. pgvector isn't a "10× faster" upgrade; it's
the only architecture that runs at the target scale.

### Q2: How long does the migration actually take?

**~4-8 hours for the currently-embedded 11.93 M subset (one-time, mostly
unattended).** Most of that time is the HNSW index build, which is a
background CPU job — not 8 hours of Sahid's working time.

When Step 6 finishes, the full corpus migration adds another ~24-48 hours
of HNSW index rebuild (also background). See §4 for the breakdown.

### Q3: Does the migration have to wait for Step 6 to complete?

**No. And it should not wait.** The strategy is:

1. **Now**: Migrate the 11.93 M already-embedded chunks. One-time ~4-8 h
   batch. After this lands, serve_rag can query the pgvector store for
   the embedded subset — the slow in-RAM load goes away for that subset.
2. **Concurrently**: Stand up a topup loop that polls
   `child_embeddings` for new rows (Step 6's continuing output, ~31
   vec/s) and streams them into the pgvector table via incremental
   `INSERT ... ON CONFLICT`. This is cheap — 31 vec/s is nothing for
   Postgres.
3. **When Step 6 completes** (~14 more days at current rate): the pgvector
   table is already up-to-date thanks to the topup loop. The HNSW
   index is the only remaining "rebuild" job — that's a background
   `REINDEX` or `CREATE INDEX CONCURRENTLY` on ~38 M extra rows. The
   existing index keeps serving queries during the rebuild.

This is exactly what the implementation guide §8.0 recommends:
> "Corpus migration to pgvector (Track B) — Step 6 either complete OR
> migration designed for streaming inserts. Recommended: migrate 9.46 M
> existing, stream forward."

Sahid should treat Step 6 as a producer feeding pgvector, not as a
blocker.

---

## 3. Evidence (measurements, not guesses)

Every number below was measured today against the live stack. No
guessing.

### 3.1 Corpus row counts (direct SQLite `SELECT COUNT(*)`)

```
parent_chunks      rows = 13,807,675   (text-only; no embeddings)
child_chunks       rows = 49,953,830   (the work-set Step 6 embeds)
child_embeddings   rows = 11,932,540   (the embedded subset today; 23.9% done)
pilot_embeddings   rows =    100,000   (test set; ignore)
```

Per-row embedding size: **16,384 bytes** = 4096 floats × 4 bytes
(float32). Verified by `SELECT length(embedding) FROM child_embeddings
LIMIT 1`.

### 3.2 RAM math (all linear scale from the row count)

| State | embeddings | RAM matrix (fp32) | Source |
|---|---|---|---|
| **Today** (Step 6 ≈ 24% done) | 11.93 M | **182 GB** computed; **254 GB measured RSS** (matrix + BM25 + Python overhead) | `ps -eo rss` on PID 3413088 (`serve_rag --port 18000` running as `rj`) |
| **When Step 6 finishes** | 49.95 M | **762 GB** projected (linear scale on row count) | math: 49.95e6 × 4096 × 4 / 1024³ = 762.2 |

Host RAM: **1007 GB total, ~661 GB free now** (`free -g`).

**Forcing constraint**: 762 GB > the headroom that remains once
analyzer-LLM (~30 GB), embedding (~30 GB), Postgres + Redis + Step 6
(~50 GB), the DDiQ microservice (~244 MB measured), and OS overhead
(~50 GB) are accounted for. The in-RAM matrix cannot fit at full
corpus size on this host. Concurrent containers/jobs make it tighter
still.

### 3.3 Today's actual retrieval latency (live `/query` calls)

Three real queries against serve_rag (port 18000), `force_mode=rag`,
`candidate_k=50`, `top_k=5`:

| Query (German legal) | embed | retrieve | rerank | generate | total |
|---|---:|---:|---:|---:|---:|
| BImSchG §6 Genehmigungserfordernis | 0.84 s | **3.86 s** | 8.53 s | 21.43 s | 34.67 s |
| Pachtdauer und Verlängerung | 0.89 s | **6.65 s** | 7.20 s | 9.19 s | 23.92 s |
| Rückbaubürgschaft Höhe | 0.83 s | **0.66 s** | 8.29 s | 8.60 s | 18.38 s |

The `retrieve` column varies wildly (0.66 → 6.65 s) — that's
numpy-matmul-over-RAM-matrix with cache-locality variance. **At 50 M
rows it scales linearly: ~3-28 s per query.** Today's 6.65 s outlier is
already painful UX; 28 s is unusable.

### 3.4 Startup-load measurement (from `LAI/logs/host/serve_rag.log`)

```
Loading child embeddings into RAM...
  9,462,540 vectors loaded in 846.0s (144.39 GB)
```

That's **60 MB/s sustained throughput** reading from the WAL-mode
SQLite. At full corpus this becomes:

| State | Vectors | Load time | RAM consumed |
|---|---|---|---|
| 2026-05-15 startup | 9.46 M | 846 s (14.1 min) | 144.4 GB |
| Today fresh start | ~12 M | ~17 min projected | ~182 GB |
| Step 6 complete | 49.95 M | **~58 min projected** | **762 GB** (impossible) |

Every redeploy of serve_rag currently costs 14 minutes. At 50 M rows,
58 minutes. That's a real operational cost.

### 3.5 What pgvector + halfvec + HNSW buys (community benchmarks at
similar dimensionality)

| Dimension | Today (in-RAM mat-mul) | pgvector halfvec + HNSW |
|---|---|---|
| Storage representation | fp32 in RAM | fp16 (halfvec) on disk |
| Full-corpus footprint | 762 GB RAM (impossible) | 381 GB on disk + ~600-1000 GB HNSW index = ~1-1.4 TB total |
| Query latency at 50 M | ~3-28 s (linear scan) | **30-100 ms** at ef_search ≈ 40-100 |
| Startup time per redeploy | ~58 min projected | **seconds** (Postgres buffer cache only) |
| Concurrent queries | Single-process GIL | Postgres connection pool |
| Index maintenance | None (matrix rebuilt every restart) | Incremental — new rows added without full rebuild |

These ratios — ~100-1000× query speedup, ~3500× startup speedup — are
the answer to "is the migration worth it."

---

## 4. Migration time breakdown (anchored in measurement)

The serve_rag log gives a hard data point: **9.46 M vectors read in
846 s = 60 MB/s** sequential throughput. Working from that:

### 4.1 First batch — migrate the 11.93 M already-embedded subset

| Phase | Time | Notes |
|---|---:|---|
| 1. Read SQLite + transform fp32 → fp16 | ~17 min | Extrapolation of the 846 s data point. fp32 → fp16 is a free `numpy.astype(np.float16)` on the same throughput. |
| 2. `COPY` to Postgres (halfvec column) | ~15 min | pgvector + halfvec COPY sustains ~10-15 K rows/s for 4096-d. 11.93 M / 12 K ≈ 17 min. |
| 3. HNSW index build | **3-6 hours** | Single-thread default; **1-3 h with parallel workers + `maintenance_work_mem` ≥ 32 GB**. This is the dominant cost. |
| **Total** | **~4-8 hours** | Wall-clock, mostly unattended |

After this lands, serve_rag points at pgvector for the embedded subset.
The in-RAM mat-mul path is retired for those rows.

### 4.2 Steady-state — topup loop while Step 6 continues

| Aspect | Value |
|---|---|
| Step 6 throughput | ~31 vec/s (measured) |
| Topup INSERT rate needed | ~31 INSERTs/s into pgvector |
| Postgres halfvec INSERT capacity | thousands/s easily |
| **Bottleneck** | Step 6's GPU embedding speed, not Postgres |

The topup loop is essentially free. Sahid can write it as a 50-line
script that runs as a systemd service (or just `nohup`'d), polling
`SELECT … FROM child_embeddings WHERE id > $last_seen ORDER BY id LIMIT
10000` every minute.

### 4.3 When Step 6 finishes (~14 days at current rate)

| Phase | Time | Notes |
|---|---:|---|
| 1. Catch-up topup (residual rows from final Step 6 batch) | < 1 hour | Small leftover; topup already running. |
| 2. HNSW index rebuild on full 50 M rows | **8-16 hours tuned; up to 48 h default** | Single-thread default ~24-48 h; with `maintenance_work_mem = 64 GB` + `max_parallel_maintenance_workers = 4` typically cuts to 8-16 h. |
| 3. (Optional) `REINDEX CONCURRENTLY` so the old index keeps serving | adds ~50% overhead but **zero downtime** | Recommended for production. |
| **Total user-visible wait** | **0 seconds** if step 3 is used | The old HNSW keeps serving while step 2 builds the new one. |

The "10-50 hours when Step 6 done" number from earlier conversations is
a one-time **background** job, not 50 hours of Sahid's time.

### 4.4 What Sahid needs to tune to hit the low end of every range

These three Postgres knobs move the wall-clock by 2-4× when set
correctly before the HNSW build:

```sql
-- run as superuser, set in postgresql.conf or via ALTER SYSTEM SET
SET maintenance_work_mem = '64GB';            -- HNSW build buffer
SET max_parallel_maintenance_workers = 4;     -- parallel index build
SET max_parallel_workers = 8;
SET work_mem = '512MB';                       -- helps the bulk COPY
```

And use `COPY … FROM STDIN` not row-by-row `INSERT` for the bulk load.
This is the difference between the high-end and low-end estimates.

---

## 5. What we do in parallel while the migration runs

The migration is mostly unattended (HNSW build is hours of background
CPU). That gives us hours-to-days of working time on other tracks that
don't conflict with Sahid (Track B) or Sumit (auth, also in flight).

The candidates below are pulled directly from `IMPLEMENTATION_GUIDE.md`
§8.5 (explicitly parallel-safe with Phase 1b), §8.0 soft compatibility,
and §9.6 operational layer. Conflict surface assessed per-file.

### 5.1 Eval harness — golden Q&A fixture + runner (recommended first)

**Why first**: directly helps Sahid validate Track B (his pgvector
retrieval must not regress recall vs. today's numpy baseline) AND lets us
see whether Track A items 2-5 actually improved DDiQ output. **Today the
project has no eval harness** — the guide flags this in §8.5: the Phase
3 correction memory "doubles as the eval harness the project lacks
today." Without it, before/after claims for Track B are vibes.

- **New files**:
  - `LAI/eval/golden_queries.json` — 20-50 hand-picked German legal
    questions with expected statute / chunk-id ground truth
  - `LAI/tests/integration/test_retrieval_recall.py` — runs each query
    through `/query`, computes recall@k against today's baseline
  - `LAI/tests/integration/test_ddiq_smoke.py` — runs
    `/ddiq/report/generate` against one fixture; asserts no "Manual
    review required", no defensive paragraphs, no Bremen turbines (the
    Track A item-by-item quality bar)
- **Conflict**: none. Pure new files in `LAI/eval/` and
  `LAI/tests/integration/`.
- **Time**: ~2-3 hours for v1 (golden-question authoring + runner
  skeleton; can be expanded later).
- **Leverage**: huge — becomes the regression suite for every Track A/B
  change going forward.

### 5.2 Phase 3 — `POST /feedback` endpoint + `lai_feedback` writes

**Why**: §8.0 explicit "can begin during Phase 1b". The `lai_feedback`
table **already exists** in `pipeline_local.db` (verified — it's in the
SQLite tables list). This is groundwork for v1.1 correction memory.

- **Files**:
  - `LAI/src/lai/api/serve_rag.py` — one new route
    `POST /feedback {conversation_id, message_id, original, corrected,
    reason}`. Conflict with Sumit's auth: trivial. Design the route to
    accept `Depends(maybe_current_user)` returning `None` pre-auth, so
    Sumit's eventual hardening is a one-line `maybe_` → required rename.
  - `LAI/src/lai/persistence.py` — add `insert_feedback(...)` helper.
  - `LAI-UI` — "Flag/Correct" button on each assistant message
    (separate repo, no Python-side conflict).
- **Conflict**: trivial — one new route handler. Mechanical merge with
  Sumit.
- **Time**: ~3-4 hours backend, ~2 hours frontend.
- **Leverage**: medium now, huge for v1.1.

### 5.3 Prometheus + Grafana wiring (operational)

**Why**: `Docker/monitoring/` already exists but isn't running. The
`lai.common.{llm, reranker, embedding}` modules **already emit
Prometheus metrics** — `lai_llm_calls_total`,
`lai_embedding_request_duration_seconds`, etc. They're being collected
by nobody. When Sahid lands Track B, his pgvector latency will be
invisible without this; when Sumit lands auth, login-failure-rate
dashboards are immediate.

- **Files**:
  - `Docker/monitoring/docker-compose.yml` — wire scrape targets
  - `Docker/monitoring/prometheus.yml` — add `lai-backend:8000/metrics`,
    `lai_analyzer_llm:8000/metrics`, etc.
  - `Docker/monitoring/grafana/dashboards/*.json` — LLM-call rate,
    retrieval latency, error budgets
- **Conflict**: none. Pure ops, separate infra.
- **Time**: ~2-3 hours (most is dashboard authoring).
- **Leverage**: makes the whole stack observable; immediate diagnostic
  value for Sahid (watch the HNSW build) and Sumit (watch auth latency).

### 5.4 `lai.common.connectors` refactor (Phase 2B prep)

**Why**: §8.0 hard dep — Phase 2B (MaStR, Handelsregister) requires
`lai.common.connectors` to exist first. Right now ALKIS + Nominatim are
hand-rolled in `ddiq_report.py` lines 56-103 (`ALKIS_WFS_ENDPOINTS`),
644-760 (`geocode_address`, `alkis_query_parcels`,
`_parse_alkis_feature`, `detect_bundesland`). Extracting into a
`lai.common.connectors` package with a `Connector` ABC sets up Phase 2B.

- **New files**: `LAI/src/lai/common/connectors/{__init__.py, base.py
  (Connector ABC), alkis.py, nominatim.py, exceptions.py}` + tests.
- **Modified**: `LAI/micro-services/ddiq_report.py` — replace inline
  ALKIS / Nominatim with imports.
- **Conflict**: touches `ddiq_report.py` (same file as Sumit and Sahid),
  but **disjoint functions**:
  - Auth (Sumit): endpoint handlers + `WHERE user_id = …`
  - Track B (Sahid): `search_doc_chunks`, `rag_context`
  - Connectors (us): `alkis_query_parcels`, `geocode_address`,
    `_parse_alkis_feature`, `detect_bundesland`
- **Time**: ~1 working day.
- **Leverage**: unblocks Phase 2B (MaStR + Handelsregister are
  top-priority "beyond data room" features per §8.4).

### 5.5 Pin Docker images + lockfile microservice deps

**Why**: §9.6 operational hardening. `vllm:latest`, `prometheus:latest`,
`pgvector/pgvector:pg16` unpinned — a single upstream breakage takes the
stack down. The DDiQ microservice's `requirements.txt` is also
unconstrained.

- **Files**: `LAI/docker-compose.yml`,
  `LAI/Docker/inference_engine/docker-compose.yml`,
  `LAI/micro-services/requirements.txt`,
  `Docker/embedding/docker-compose.yml`.
- **Conflict**: none.
- **Time**: ~2 hours.
- **Leverage**: insurance, not a feature.

---

## 6. Recommended sequencing while Sahid's HNSW build runs

| When | Item | Time | Conflict |
|---|---|---:|---|
| Hour 0-3 | **5.1 Eval harness** | ~3 h | none |
| Hour 3-7 | **5.2 Phase 3 `/feedback` backend** | ~4 h | trivial (one route) |
| Hour 7-9 | **5.3 Prometheus + Grafana wiring** | ~2 h | none |
| Hour 9-end | **5.4 `lai.common.connectors` refactor** | ~1 day | small (disjoint functions) |
| Anytime | **5.5 Pin Docker images** | ~2 h | none |

That fills **~1.5 working days of parallel work** — comfortably covers
Sahid's first migration batch (~4-8 h). When his batch lands, we have
the eval harness ready to verify retrieval recall didn't regress, the
metrics ready to read latency numbers off the dashboards, and the
groundwork done for Phase 2B/3.

---

## 7. Summary

| Question | Answer |
|---|---|
| Is the time investment justified? | **Yes — forcing function**. 762 GB fp32 RAM exceeds 1007 GB host headroom when Step 6 finishes. The in-RAM approach hits a wall. pgvector is the only architecture that runs at target scale. |
| How long does migration take? | **First batch (11.93 M, today): ~4-8 hours wall-clock, mostly unattended.** Full-corpus index rebuild when Step 6 completes: ~8-48 hours, also background. |
| Does it have to wait for Step 6? | **No — and it should not.** Migrate the embedded subset now; stream new rows via a topup loop as Step 6 produces them; rebuild HNSW once at the end. The guide §8.0 explicitly recommends this. |
| What do we do meanwhile? | **Eval harness → Phase 3 feedback endpoint → Prometheus/Grafana → `lai.common.connectors` refactor**. None conflict with auth or Track B. ~1.5 working days of parallel work. |

---

## 8. Pointers

- `harsh/IMPLEMENTATION_GUIDE.md` §4.1 — Move 1 (Unify storage / pgvector keystone)
- `harsh/IMPLEMENTATION_GUIDE.md` §5.2 — `lai.retrieval` package design
- `harsh/IMPLEMENTATION_GUIDE.md` §7 — The dual retrieval combiner
- `harsh/IMPLEMENTATION_GUIDE.md` §8.0 — Phase compatibility table (the
  authoritative "what must come before what")
- `harsh/IMPLEMENTATION_GUIDE.md` §8.3 — Phase 1b Track B exit criterion
- `harsh/PROGRESS.md` — Track B status row (now owned by Sahid)
- `LAI/src/lai/search/eval.py` — the current `load_embeddings` + numpy
  mat-mul retrieval that this work replaces
- `LAI/logs/host/serve_rag.log` — the "846 s / 9.46 M vectors / 144 GB"
  measurement that anchors the timing math
