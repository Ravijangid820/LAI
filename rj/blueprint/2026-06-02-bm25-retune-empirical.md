# Plan — BM25 retune, empirically gated this time

**Date:** 2026-06-02 · **Owner:** rj · **Status:** PROPOSED
**Why a plan:** the 2026-05-31 BM25 retune (top-3 AND with punct-strip)
was shipped on the strength of a 1-query smoke test and reverted on a
−23 pp Recall@30 regression once the in-RAM harness was actually run.
We now have a production-fidelity recall harness (`scripts/eval/retrieval_recall.py`,
[d4de720](https://github.com/Ravijangid820/LAI/commit/d4de720)) that
scales to the 35.7 M-child corpus. Before another retune attempt, fix
the eval discipline that failed last time — every variant gets the same
recall gate, in advance.

## What we know
- BM25 over the SQLite FTS5 child index dominates production
  `retrieve_s` (~2.7 s warm; dense ANN is ~120 ms warm).
- The current expression in `lai.search.eval._bm25_match_expr` is
  *"top-6 longest distinct tokens, len>4, joined with OR"* — broad
  enough to retrieve millions of FTS5 rows that get sorted in-DB.
- The 2026-05-31 *"top-3 AND with punct-strip"* attempt cut latency
  but dropped Recall@30 from 37 % → 14 % (the in-RAM 50-query eval).
  Reverted at the comment block above `_bm25_match_expr` (lines 280–300
  of `src/lai/search/eval.py`).
- The 2026-06-02 dense-only baseline at n=200 shows **56.5 % of gold
  parents do not appear in the top-100 dense candidates** — i.e. BM25
  is doing most of the recall heavy lifting in hybrid mode. A BM25 that
  is *fast but worse* is a worse pilot demo than one that is *slow but
  right*.

## Goal
Find a BM25 expression that is **at least 2× faster than OR-of-6** while
keeping Recall@30 within **1 pp** of the OR-of-6 baseline at hybrid
mode. If no variant clears that bar, ship a *better-monitored* version
of OR-of-6 (e.g. with a per-query latency log) and accept the cost.

## Approach (run every variant through the same gate)

### Step 1 — pin the OR-of-6 baseline
Run the new harness in hybrid mode at n=200 with the current
`_bm25_match_expr`. Record `Recall@10/30/100`, MRR, per-query embed /
retrieve / hydrate ms. This is the line every variant must beat or
match.

### Step 2 — enumerate variants
Each variant is a one-function-replacement in
`src/lai/search/eval.py:_bm25_match_expr`. Branching done via an env
var (`LAI_BM25_VARIANT={v1,v2,v3,...}`) so we don't fork the code:

| Tag | Expression | Hypothesis |
|---|---|---|
| `v1` | top-6 OR len>=5 | current (control) |
| `v2` | top-4 OR len>=6 | narrower pool — fewer FTS5 rows scored |
| `v3` | top-6 NEAR/10 (proximity) | tighter relevance, similar pool |
| `v4` | top-6 OR len>=5 + LIMIT 5000 | hard cap on FTS5 rows |
| `v5` | top-6 OR len>=5 + DE-stopword filter | drop noise tokens before selection |
| `v6` | top-6 OR with prefix-glob (e.g. `"genehm*"`) | catch morphological variants |
| `v7` | length-routed: AND-of-3 if query ≥ 8 tokens, OR-of-6 otherwise | best of both regimes per query |

### Step 3 — measure
For each variant: hybrid mode, n=200, candidate-k=200, output the JSON
+ per-row CSV. Collate into one summary table:
``ef_search,recall_at_10,recall_at_30,recall_at_100,mrr,retrieve_ms``.

### Step 4 — decision rule (encoded, not vibe-checked)
1. **Drop** any variant whose Recall@30 is more than 1 pp below v1.
2. From the survivors, pick the one with lowest `retrieve_ms`.
3. If there's a tie within 5 % on latency, prefer the smaller code
   change.

### Step 5 — ship
- Replace `_bm25_match_expr` with the winner.
- Update the rationale comment above it with the new measured numbers.
- Add a one-line entry to PROGRESS_V2 with the recall delta + latency
  delta vs. v1.

## What this plan does NOT do
- It does **not** touch the in-process reranker — the harness measures
  pre-reranker Recall, which is the real input quality. A retune that
  improves pre-rerank Recall@candidate_k feeds the reranker more
  signal; one that worsens it is invisible at rerank but caps the
  ceiling.
- It does **not** change HNSW. That's a parallel sweep
  (`scripts/eval/hnsw_ef_search_sweep.py`) handled separately.
- It does **not** introduce a query classifier or learned-sparse
  retrieval. Both are interesting but well beyond a one-day retune.

## How to abort cleanly
If no variant clears the recall gate, revert and add a comment block
to `_bm25_match_expr` listing the variants tested + their recall
deltas. Future-self should never have to re-run the same experiment.

## Lessons folded in from 2026-05-31
1. A 1-query smoke test is not a recall test.
2. The eval harness must use the **same indexes production uses**
   (pgvector HNSW + SQLite FTS5), not an in-RAM proxy.
3. Decision rules must be written **before** the experiment, not
   after — otherwise the threshold drifts to match the favourite
   variant.
