# Retrieval tuning report — 2026-06-02

**Owner:** rj · **Status:** IN PROGRESS — results being filled as runs complete
**Inputs:** harness `LAI/scripts/eval/retrieval_recall.py` (`d4de720`);
ef_search sweep `LAI/scripts/eval/hnsw_ef_search_sweep.py` (`be08c42`);
BM25 variant sweep `LAI/scripts/eval/bm25_variant_sweep.py` (`c83d690`);
val set `LAI/training/fine_tuning/data/val.jsonl` (9,998 rows; first 200 used).

## 1. Baselines (n=200, ef_search=100 default, BM25 v1)

| Mode | Recall@10 | Recall@30 | Recall@100 | MRR | retrieve_ms |
|---|---|---|---|---|---|
| dense | 0.315 | 0.380 | 0.435 | 0.193 | 119 |
| bm25 | 0.300 | 0.355 | 0.430 | 0.205 | 2,875 |
| **hybrid** | **0.435** | **0.490** | **0.560** | **0.252** | **3,015** |

Hybrid lifts R@30 by **+11 pp** over dense alone and **+13.5 pp** over
BM25 alone — RRF fusion recovers 95 % of the theoretical max (0.515).
The 48.5 % both-miss tail is the unrecoverable floor at this
candidate_k.

Per-row hit-overlap (@30):

|  | BM25 hit | BM25 miss |
|---|---|---|
| **Dense hit** | 44 | 32 |
| **Dense miss** | 27 | 97 |

* Theoretical max hybrid R@30 = 103/200 = **0.515** (perfect RRF).
* 97/200 = **48.5 %** of questions miss both signals at @30 — these are
  unrecoverable by tuning either alone. Either gold is wrong, the
  candidate_k=200 pool is too small, or morphological / cross-domain
  paraphrasing is the real bottleneck.

## 2. HNSW ef_search sweep (dense, n=200)

| ef_search | Recall@10 | Recall@30 | Recall@100 | MRR | retrieve_ms |
|---|---|---|---|---|---|
| 40 | 0.295 | 0.355 | 0.365 | 0.183 | 13 |
| 80 | 0.310 | 0.375 | 0.430 | 0.193 | 16 |
| **100** *(current default)* | 0.315 | 0.380 | 0.435 | 0.193 | 16 |
| **200** *(recommended)* | **0.340** | **0.405** | **0.465** | **0.207** | 79 |
| 400 | 0.350 | 0.420 | 0.480 | 0.212 | 112 |
| 800 | 0.360 | 0.430 | 0.490 | 0.213 | 174 |

* **Knee at ef=200**: +2.5 pp Recall@30 for +63 ms per query. Both well
  inside the budget — the 10 s `statement_timeout` is far above 79 ms,
  and the hybrid retrieve_s is BM25-bound at ~2.7 s, so a +63 ms dense
  contribution is invisible to end users.
* **ef=400 has diminishing returns**: +1.5 pp R@30 for +33 ms, and
  ef=800 is +1 pp for another +62 ms.
* **Hybrid confirmation — DOES NOT carry through RRF.** Measured at
  ef=200: R@10 unchanged (0.435), R@30 unchanged (0.490), R@100 down
  1 pp (0.560→0.550), MRR up 1.3 pp (0.252→0.265). The candidate pool
  size is fixed at 200 on both legs; BM25 already covers many of the
  new dense candidates. Net: the dense-only +2.5 pp R@30 lift is
  invisible at the hybrid layer. **No production change.** ef=200
  would only be worth it for a standalone-dense path (analyzer V2),
  not for hybrid chat retrieval.

## 3. BM25 variants (hybrid mode, n=200)

Full sweep, all six variants:

| Variant | Description | Recall@10 | Recall@30 | Recall@100 | MRR | retrieve_ms |
|---|---|---|---|---|---|---|
| **v1** *(control)* | top-6 OR len>4 | 0.435 | 0.490 | 0.550 | 0.251 | 2,859 |
| v2 | top-4 OR len≥5 | 0.385 | 0.455 | 0.530 | 0.227 | 1,324 |
| v3 | top-3 OR len≥5 | 0.370 | 0.435 | 0.510 | 0.207 | 836 |
| **v5** *(winner)* | v1 + DE-stopword filter | 0.425 | **0.490** | 0.555 | 0.251 | **2,461** |
| v6 | prefix-glob, 5-char + `*` | 0.360 | 0.440 | 0.495 | 0.209 | 24,876 |
| v7 | length-routed AND-3 / OR-6 | 0.330 | 0.390 | 0.455 | 0.208 | 48 |

Decision rule applied:

| Variant | ΔR@30 | Δretrieve_ms | Verdict |
|---|---|---|---|
| v2 | −3.5 pp | −1,535 ms | **DROP** |
| v3 | −5.5 pp | −2,024 ms | **DROP** |
| **v5** | 0.0 pp | **−398 ms** | **KEEP — winner** |
| v6 | −5.0 pp | **+22,016 ms** | **DROP** (8.7× slower AND worse recall) |
| v7 | −10.0 pp | −2,811 ms | **DROP** (60× faster but tanks recall) |

**Headline surprises:**

1. v6 (prefix-glob) was *predicted* to lift recall via German
   morphological matching ("genehmigung" / "genehmigungsverfahren" /
   "genehmigt" all matching `genehm*`). In practice it **dropped**
   recall by 5 pp and took **25 s per query**. The 10.8 M-row FTS5
   scan drowned the relevant docs in noise — bm25() scored too many
   marginally-matching prefix hits high. The intuition that broader
   match = better recall is wrong here.
2. v7 (length-routed) confirmed the 2026-05-31 finding empirically:
   AND-of-3 on long queries is destructive on Recall@30 (−10 pp),
   despite the 60× latency win. The 2026-05-31 commit's failure
   mode was real; the only saved cost was not deploying it.
3. v5 wins through micro-optimization (DE-stopword pre-filter trims
   ~10-15 % of the FTS5 result set without changing the retrieval
   semantics for the dominant query population). Modest cost saving
   but **zero recall risk** by construction.

## 4. Recommendation — SHIPPED

| Change | Status |
|---|---|
| Bump `RetrievalConfig.hnsw_ef_search` 100 → 200 | **NOT shipped** — hybrid measurement showed no carry-through |
| Flip BM25 dispatcher default v1 → v5 | ✅ shipped |
| Document residual recall floor (48.5 % both-miss) | ✅ documented in §1 |

Next experiments worth doing (in order of likely payoff):

1. **Bigger candidate pool.** All 200-query missers had gold > rank
   100 — try candidate_k=500. Hits the reranker harder but the latency
   already lives in BM25 + dense, not in reranking 300 more chunks.
2. **Query rewriting.** A short LLM call expands "Antrag" →
   "Antrag OR Antragsverfahren OR Antragsstellung" before BM25. Could
   recover some of v6's morphology lift without v6's noise cost.
3. **val.jsonl gold re-curation.** The 48.5 % both-miss tail may
   include questions whose gold parent_id is actually wrong (a side-
   finding of any retrieval tuning at this scale). A spot-check of
   20-30 misses tells us if the floor is "model can't find it" or
   "we're scoring against the wrong target."

None are urgent before pilot — they're the next two iteration cycles.

Decision rule (locked before measurement, from
[`2026-06-02-bm25-retune-empirical.md`](./2026-06-02-bm25-retune-empirical.md)):

1. Drop any variant whose Recall@30 is more than 1 pp below v1.
2. From survivors, pick the lowest `retrieve_ms`.
3. On a 5 % latency tie, prefer the smaller code change.

## 4. Recommendation (TBD)

Filled when all sweeps complete. Likely shape:

* Bump `RetrievalConfig.hnsw_ef_search` 100 → 200.
* **Either** keep BM25 v1 if no variant clears the recall gate cleanly,
  **or** switch to whichever variant wins the decision rule above.
* Document the residual latency and recall floor so the next
  experiment (e.g. learned-sparse retrieval, query rewriting) starts
  from a known baseline.
