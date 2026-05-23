# rj — serve_rag GPU placement (unblocks the legal-corpus retrieval)

**Date:** 2026-05-21 · **Priority:** high — this is what makes LAI a *legal
agent* (corpus `[C-n]` answers) rather than a document-only Q&A.

> **UPDATE 2 — just restart serve_rag once more; nothing else needed.**
> Your first restart didn't move the reranker (it stayed on the saturated
> GPU 0 and kept OOMing). Rather than have you debug `CUDA_VISIBLE_DEVICES`,
> I fixed it **in code**: the reranker now auto-selects the CUDA device
> with the most free memory (so it lands on GPU 1, ~52 GB free, on its
> own — override with `LAI_RERANK_DEVICE` if ever needed). The same restart
> also picks up a bugfix to the corpus-retrieval fallback (it was throwing
> an UnboundLocalError after the OOM). serve_rag runs from source, so:
>
> ```
> # stop the current serve_rag, then relaunch (start-host.sh is fine)
> bash /data/projects/lai/LAI/scripts/ops/start-host.sh
> ```
>
> No git pull (changes are in the working tree), no env edits. After it's
> up, a corpus question should return `[C-n]` citations (verify below).

## The problem (measured)

Chat corpus retrieval is **dead** — every statutory / jurisdiction /
corpus-only question returns *"not in the uploaded documents"* with zero
`[C-n]` citations. Root cause is a **CUDA OOM on the in-process reranker**:

```
nvidia-smi:
  GPU 0  (97 GB):   127 MiB free   ← analyzer vLLM 77 GB + serve_rag reranker 20 GB
  GPU 1  (97 GB):  52 GB free      ← embedding vLLM 45 GB, lots of room
```

serve_rag's reranker (Qwen3-Reranker-8B) loaded onto **GPU 0**, which the
analyzer vLLM already nearly fills. At query time the rerank step can't
allocate working memory → `torch.OutOfMemoryError` → corpus retrieval
throws → chat silently degrades to document-only.

`scripts/ops/start-host.sh` already launches serve_rag with
`CUDA_VISIBLE_DEVICES=1` — which would put the reranker on **GPU 1 (52 GB
free)**. But the running serve_rag (PID 1150746) is on GPU 0, i.e. it was
relaunched **without** that env. That's the whole bug.

## The ask (one of these — A preferred)

**A. Relaunch serve_rag via start-host.sh so it gets `CUDA_VISIBLE_DEVICES=1`.**
   Put the reranker on GPU 1 where there's 52 GB free. Cleanest — no
   analyzer throughput hit. Concretely:
   ```
   cd /data/projects/lai/LAI
   # stop the current serve_rag (PID 1150746), then:
   bash scripts/ops/start-host.sh        # relaunches with CUDA_VISIBLE_DEVICES=1
   ```
   Verify after: `nvidia-smi` shows serve_rag's reranker on GPU 1, and a
   corpus question returns `[C-n]` (see the test below).

**B. If serve_rag must stay on GPU 0:** lower the analyzer vLLM's
   `--gpu-memory-utilization` (e.g. 0.85 → 0.65, frees ~14 GB) and set
   `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` on serve_rag. Costs
   analyzer KV-cache/throughput — less clean than A.

## This restart ALSO lands two staged serve_rag code fixes

Both are uncommitted in `src/lai/api/serve_rag.py` (alongside Sahid's
in-progress changes), and go live on the next serve_rag restart:

1. **Thinking-mode off on the non-streaming `/query` path** — the chat
   500 bug (already verified fixed after your last restart, but re-confirm).
2. **Graceful reranker degradation in `_do_rag`** — if the reranker
   still OOMs, corpus retrieval now falls back to the hybrid RRF order
   (dense + BM25) instead of dying. So even under GPU pressure the lawyer
   gets cited `[C-n]` corpus passages (just without the final rerank).
   Belt-and-suspenders with the GPU fix above.

## Verify it worked (any logged-in session)

```
POST /query  {"question":"Ab welcher Gesamthöhe ist eine WEA nach BImSchG
genehmigungspflichtig?","force_mode":"rag"}
→ should answer with [C-n] corpus citations (the >50 m / 4. BImSchV chain),
  NOT "nicht in den hochgeladenen Unterlagen".
```

## Note on Step 6 / GPU 1 contention

GPU 1's 45 GB vLLM is the embedding server (Step-6 corpus embedding runs
through it, ~100% util). The reranker needs ~16-20 GB of GPU 1's 52 GB
free — fits on memory, shares compute. If rerank latency is poor under
that contention, revisit once Step 6 finishes (~days out).
