# Plan — query expansion fed to the reranker only

**Date:** 2026-06-03 · **Owner:** rj · **Status:** PROPOSED — decision rule locked before code
**Why a plan:** the 2026-06-02 BM25 sweep proved that OR-ing
LLM-generated expansions into the BM25 expression HURTS Recall@K
(broader BM25 → worse precision). But the *content* of the expansions
("Genehmigungsverfahren", "Immissionsschutzgenehmigung",
"Betriebserlaubnis") was real semantic signal — just fed to the wrong
layer. Today tests the residual hypothesis: feed the same expansions
to the **reranker** instead of BM25, leaving BM25 (v5) and dense
untouched.

## What we know

* Hybrid baseline n=200: R@30 = 0.490, retrieve_ms = 2,577.
* BM25 widening (v6 prefix-glob, r1/r2/r3 LLM expansions): every
  variant dropped R@30 by 2–10 pp because the broader disjunct set
  pulled in lexically-strong-but-topically-wrong docs that displaced
  the gold.
* The harness so far measures PRE-RERANKER recall (dense + BM25 +
  RRF + parent dedup). Production has an in-process Qwen3-Reranker-8B
  on top that scores `(query, candidate)` pairs and re-orders the
  top-K. We've never measured the reranker's contribution at val
  scale.

## Hypothesis

If the reranker sees the original query, it scores candidates by how
well their tokens match the query's tokens. A gold doc that
paraphrases the question ("Bewilligung" instead of "Genehmigung") may
rank low because the lexical overlap is low — even though semantically
it's the right answer.

If we instead pass the reranker a query *augmented* with synonyms
("Welche Genehmigung … (auch: Bewilligung, Erlaubnis, Konzession)"),
the reranker has more lexical hooks to match against. Paraphrased gold
should now rank higher.

This hypothesis is COMPLETELY ORTHOGONAL to the BM25 question — BM25
stays at v5 (unchanged). The expansions only land in the cross-encoder
prompt the reranker sees.

## Goal

Find a query-augmentation strategy that:

1. Lifts **post-reranker Recall@K** by ≥ 1 pp vs unaugmented baseline.
2. Keeps **per-query reranker latency** within +30 % of baseline
   (longer prompts cost some tokens; the rerank stage is ~2.5 s on
   smoke today, so the budget is +750 ms).
3. Adds NO new model — uses the same Qwen3-Reranker-8B production
   loads, just with a different query string.

If no strategy clears the recall gate, ship NOTHING and document
"query-to-reranker-only attempted, no lift" — the 2026-05-31 anti-
pattern (ship-then-measure) stays forbidden.

## Approach

### Step 1 — extend the harness to measure post-reranker Recall@K

The existing `LAI/scripts/eval/retrieval_recall.py` stops at parent
hydration. Add a `--rerank` flag that:

* Loads `Qwen3-Reranker-8B` via the same `Reranker` class
  serve_rag uses (`lai.search.eval.Reranker` — already in-tree).
* Scores each candidate parent's text against the query.
* Reports Recall@K computed AFTER reranker ordering.

GPU consideration: production has the reranker on `cuda:1` (~18.5 GB).
Loading a second instance for eval would conflict. Two safe paths:

* **Use cuda:0** (free of the reranker; Qwen3.6-27B vLLM lives there
  but has ~24 GB free per today's `nvidia-smi`).
* **Or use the `:8004 lai-test-reranker` container** — separate
  process, no GPU contention with serve_rag.

Pick whichever is simpler — likely cuda:0 in-process via `Reranker`.

### Step 2 — three augmentation strategies

Reuse `lai.search.query_rewriter.get_expansions(query, variant)` — the
LLM call + cache infrastructure is already there. New module
`lai.search.rerank_query.py` composes the augmented query:

| Tag | Augmented query passed to reranker |
|---|---|
| `none` *(control)* | the original query, unchanged |
| `q1` | `<original query>\nVerwandte Begriffe: <r2 synonyms joined by comma>` |
| `q2` | `<original query>\nVerwandte Formen: <r1 morphology joined by comma>` |
| `q3` | `<original query>\nVerwandte Begriffe: <r2 ...>; verwandte Formen: <r1 ...>` |

The "Verwandte Begriffe" prefix is German for "related terms" — the
reranker is multilingual but the prompt is German-dominated, so a
German hint reads naturally. Expansions are LLM-cached from yesterday's
sweep so re-runs cost ~zero.

### Step 3 — sweep

```
python -m scripts.eval.rerank_expansion_sweep \
  --mode hybrid --n 200 --variants none,q1,q2,q3
```

Output: one CSV row per variant with R@K, MRR, rerank_ms,
rerank_query_chars (for debug).

### Step 4 — decision rule (locked BEFORE running)

1. Drop any variant whose **post-reranker R@30** is below the
   control's R@30 minus 1 pp (i.e. require ≥ control − 0.01).
2. Drop any variant whose **rerank_ms** exceeds control × 1.30.
3. From survivors, pick the highest R@30. If multiple tie within
   0.5 pp, prefer lowest rerank_ms.
4. If no variant survives, ship nothing.

The recall direction here is "must not regress more than 1 pp" rather
than "must improve by ≥ 1 pp" — because we ALSO want to learn whether
augmenting the reranker prompt is *neutral* (i.e. the reranker is
already doing what augmentation would do via its embeddings). A
neutral result tells us the answer is "reranker is fine as-is" which
is genuinely useful.

## What this plan does NOT do

* Does NOT touch BM25 or dense — v5 stays.
* Does NOT touch the reranker model — same Qwen3-Reranker-8B, just
  different input string.
* Does NOT measure pilot-quality (the LLM still generates from the
  reranker's top-K; we're measuring upstream of generation).

## Cost estimate

* Step 1 (harness with reranker): ~1 hour code + 30 min lint/tests.
* Step 2 (query composition + sweep runner): ~45 min code.
* Step 3 (sweep): 4 variants × ~200 queries × (2.5 s base BM25/dense
  + ~3 s reranker on top-200 candidates) = ~67 min wall-clock.
* Step 4 (decision rule + commit): ~15 min.
* Total: ~3 hours of which ~70 min is unattended sweep.

## Lessons folded in from today's experiments

1. Locked decision rule before running. No threshold drift.
2. Cache expansions — yesterday's `_rewrite_cache/` already has the
   LLM output for the val queries via the r1/r2 calls.
3. Negative result is documented, not silently abandoned — same shape
   as today's retrieval-tuning-results.md.
