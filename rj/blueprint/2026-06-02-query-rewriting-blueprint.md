# Plan — query rewriting for BM25 lift (post-v5)

**Date:** 2026-06-02 · **Owner:** rj · **Status:** PROPOSED — empirical decision rule locked before coding
**Why a plan:** today proved BM25 v6 prefix-glob (`genehm*`) crashed
the FTS5 scan to 10.8 M rows AND dropped recall 5 pp. The
intuition was right (German morphology matters) but the execution
was wrong (regex-level globbing is too coarse). Query rewriting
tests a smarter execution: ask the LLM for 3–5 *targeted* morphological
expansions per query, OR them into the BM25 expression.

## What we know

* BM25 v1 OR-of-6: 1.99 M rows scanned, R@30 0.490, 2859 ms.
* BM25 v5 OR-of-6 + DE-stopword filter (now production default):
  same R@30, 2461 ms (~14 % faster).
* BM25 v6 prefix-glob `genehm*`: 10.8 M rows, R@30 0.440 (−5 pp),
  24 876 ms. Too broad.
* ef_search bump and candidate_k bump both → zero hybrid lift.
* Same-question partition by gold-language → DE / non_DE rows have
  essentially identical R@K (0.491 vs 0.488 at R@30).

The remaining lever no experiment has tested: **expand the query in
controlled steps before BM25.** A short LLM call turns "Welche
Genehmigung" → ["Genehmigung", "Genehmigungsverfahren",
"Genehmigungsbescheid", "genehmigt"]. Each is a real word, not a
prefix glob — so FTS5 still uses indexed tokens (fast) and we just
add a few more disjuncts.

## Goal

Find a query-rewriting strategy that:

1. **Lifts hybrid Recall@30 by ≥ 1 pp** vs v5 baseline (0.490).
2. **Keeps retrieve_ms within +1.5× of v5** (≤ ~3700 ms / query).
3. Adds NO new dependency at production-import time (uses the
   existing `:8005` Qwen3.6-27B vLLM client, already wired into
   serve_rag for the analyzer).

If no strategy clears the recall gate, ship NOTHING and document
"query rewriting attempted, no lift" in the retrieval-tuning report.
The 2026-05-31 anti-pattern (ship-then-measure) is explicitly
forbidden.

## Approach

### Step 1 — pin the v5 baseline at n=200

Already measured today: v5 hybrid n=200 R@30=0.490 retrieve=2461ms.
Re-pin with one more confirmation run after the restart (so we know
the production process is what we're measuring against).

### Step 2 — enumerate 3 rewriting strategies

| Tag | Strategy | Hypothesis |
|---|---|---|
| `r1` | **morphology-only**: ask LLM for 3 morphological variants of each top-3 token, then OR them all together with v5's OR-of-6 base | Recovers v6's morphology lift without v6's noise (no globs, just real word stems) |
| `r2` | **synonym-only**: ask LLM for 3 legal-domain synonyms of the query as a whole (e.g. "Genehmigung" → "Bewilligung, Erlaubnis, Konzession") | Catches paraphrased gold without changing token shape |
| `r3` | **morphology + synonym combined**: union of r1 + r2's expansions | Maximizes recall at the cost of more disjuncts (slower) |

Each `r*` calls the LLM ONCE per query (4 tokens out for r1, ~30
tokens out for r2/r3), then OR-joins with the v5 expression. Cached
on (sha256(query)) so re-runs of the harness are deterministic.

### Step 3 — environment-gated dispatch

Mirror the v5 BM25 dispatcher pattern from
`lai.search.eval._bm25_match_expr`: add `LAI_QUERY_REWRITE_VARIANT
∈ {none, r1, r2, r3}` env, default `none` (production unchanged
until a winner is flipped). Eval harness sets the env per
subprocess.

### Step 4 — measure with the existing harness

```
python -m scripts.eval.query_rewrite_sweep \
  --mode hybrid --n 200 --variants none,r1,r2,r3
```

Output: same shape as `bm25_variant_sweep.py` — one CSV row per
variant with R@K, MRR, embed_ms, retrieve_ms, hydrate_ms,
**plus** a new `rewrite_ms` column for the LLM call.

### Step 5 — decision rule (locked BEFORE running)

1. Drop any variant whose R@30 is below 0.500 (i.e. < +1 pp vs v5
   baseline of 0.490).
2. Drop any variant whose retrieve_ms exceeds 3700 ms (+50 % vs
   v5 baseline of 2461 ms).
3. From survivors, pick the lowest `retrieve_ms + rewrite_ms`
   (total chat-path latency cost).
4. If no variant survives, **ship nothing** and log the negative
   result.

## What this plan does NOT do

* Does NOT re-derive embeddings on rewritten queries (the dense
  side keeps the original query for embedding — only BM25 gets the
  expansion). Embedding a rewritten query would muddle the
  comparison and probably hurt.
* Does NOT cache LLM-rewriting results in production (this is a
  measurement experiment; caching is a follow-on if we ship).
* Does NOT touch v5 itself — the rewriter wraps v5's output.

## Cost estimate

* LLM call per query: ~500–1000 ms against `:8005` Qwen3.6-27B
  (analyzer's typical 1-token round-trip).
* Sweep: 4 variants × 200 queries × ~5 s/query (dominated by
  BM25) = ~67 min wall-clock. Cacheable across re-runs.
* Implementation: ~150 LOC for the rewriter + ~80 LOC for the sweep
  runner + tests.

## What I'd reach for if Step 5 fails

If no variant clears the gate, the remaining levers are
architectural, not parameter-tuning:

1. **Learned-sparse retrieval** (e.g. SPLADE) — replaces BM25 with
   a token-importance model. Real lift but real build cost.
2. **Re-rerank with query expansion only at the reranker stage** —
   leave BM25 alone, let the reranker see a richer query.
3. **Accept 0.490 R@30 as the model ceiling** and shift the
   retrieval conversation to candidate quality (already done
   today: val-quality finding + filter).
