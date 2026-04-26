"""
Multi-model RAG comparison harness.

Pre-computes RAG context once for N val queries, then loads each model
sequentially (frees GPU between loads) and generates an answer per
query. Outputs a side-by-side markdown table for inspection.

Why pre-compute the RAG context: the embedding container holds ~44 GB
on GPU 1, leaving ~53 GB free. The 27B Qwen3.5/3.6 models need ~54 GB
in bf16 — they don't fit alongside the embedding container. So we
retrieve once, then stop the embedding service for the heavy models
and just do generation.

Usage:
    cd /data/projects/lai/LAI
    CUDA_VISIBLE_DEVICES=1 .venv/bin/python scripts/multi_model_compare.py \
        --n 5 --top-k 3 \
        --models qwen25-ft qwen25-base qwen35 qwen36 gemma4

Models registered (--models keys):
    qwen25-ft     → /data/projects/lai/models/qwen25-7b-legal-merged
    qwen25-base   → Qwen/Qwen2.5-7B-Instruct
    qwen35        → Qwen/Qwen3.5-27B
    qwen36        → Qwen/Qwen3.6-27B
    gemma4        → google/gemma-4-E4B-it
    llama3        → meta-llama/Meta-Llama-3-8B-Instruct
    leo7b         → /data/projects/lai/models/leo-hessianai-7b
    saul7b        → /data/projects/lai/models/Saul-7B-Instruct-v1
"""
from __future__ import annotations

import argparse
import gc
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

LAI_DIR = Path(__file__).resolve().parents[1]
DB      = LAI_DIR / "processed" / "pipeline_local.db"
VAL     = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
OUT     = LAI_DIR / "scripts" / "rag_eval_results" / "multi_model_compare.md"

sys.path.insert(0, str(LAI_DIR / "scripts"))
from rag_eval import (  # noqa: E402
    Corpus, load_embeddings, ensure_bm25, embed_query,
    retrieve_dense, retrieve_bm25, rrf_fuse, Reranker,
    load_parent_texts, dedupe_by_parent,
)


MODELS = {
    "qwen25-ft":   "/data/projects/lai/models/qwen25-7b-legal-merged",
    "qwen25-base": "Qwen/Qwen2.5-7B-Instruct",
    "qwen35":      "Qwen/Qwen3.5-27B",
    "qwen36":      "Qwen/Qwen3.6-27B",
    "gemma4":      "google/gemma-4-E4B-it",
    "llama3":      "meta-llama/Meta-Llama-3-8B-Instruct",
    "leo7b":       "/data/projects/lai/models/leo-hessianai-7b",
    "saul7b":      "/data/projects/lai/models/Saul-7B-Instruct-v1",
}

RAG_SYSTEM = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage ausschließlich auf "
    "Grundlage der unten bereitgestellten Rechtstexte. Zitiere bei jeder "
    "Aussage den entsprechenden Quellabschnitt (z.B. [Quelle 1])."
)


def build_messages(question: str, sources: list[str]) -> list[dict]:
    src_block = "\n\n".join(f"[Quelle {i+1}]\n{s}" for i, s in enumerate(sources))
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user",   "content": f"Rechtstexte:\n{src_block}\n\nFrage: {question}"},
    ]


def free_gpu():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def precompute_contexts(picks: list[dict], top_k: int, candidate_k: int) -> list[dict]:
    """Run dense+bm25+rerank once and stash the retrieved sources per query."""
    print("[retrieval] loading embeddings (this is the slow part)...", flush=True)
    conn = sqlite3.connect(str(DB))
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    corpus = load_embeddings(conn)
    ensure_bm25(corpus, conn)
    parent_text = load_parent_texts(conn)
    print(f"[retrieval]   {len(corpus.embs):,} embeddings ready", flush=True)

    print("[retrieval] loading reranker...", flush=True)
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")

    out = []
    for i, q in enumerate(picks):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        ref = next(m["content"] for m in q["messages"] if m["role"] == "assistant")
        gold = q["parent_id"]

        qvec = embed_query(question, with_prefix=True)
        d_idx, _ = retrieve_dense(qvec, corpus, candidate_k)
        b_idx, _ = retrieve_bm25(question, corpus, candidate_k)
        fused = rrf_fuse([d_idx, b_idx])[:candidate_k]
        cand = [c for c, _ in fused]
        pairs = [(question, parent_text.get(int(corpus.parent_ids[c]), "")[:2000])
                 for c in cand]
        scores = reranker.score(pairs)
        order = np.argsort(-np.asarray(scores))
        reranked = [cand[j] for j in order]
        top_parents = dedupe_by_parent(reranked, corpus, top_k)
        sources = [parent_text.get(int(p), "")[:1500] for p in top_parents]

        gold_in_top = int(gold) in top_parents
        out.append({
            "question": question,
            "ref": ref,
            "gold": gold,
            "gold_in_top": gold_in_top,
            "top_parents": [int(p) for p in top_parents],
            "sources": sources,
        })
        print(f"[retrieval] [{i+1}/{len(picks)}] gold_in_top={gold_in_top} parents={out[-1]['top_parents']}",
              flush=True)

    # Free the heavy retriever artifacts
    del reranker.model, reranker.tok, reranker
    del corpus, parent_text
    conn.close()
    free_gpu()
    return out


@torch.no_grad()
def generate_with_model(model_key: str, prompts: list[list[dict]],
                       max_new_tokens: int = 400) -> tuple[list[str], float]:
    path = MODELS[model_key]
    print(f"\n=== {model_key}: loading {path} ===", flush=True)
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        path,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        trust_remote_code=True,
    ).eval()
    load_s = time.time() - t0
    print(f"[{model_key}]   loaded in {load_s:.1f}s", flush=True)

    answers: list[str] = []
    t_gen = time.time()
    for i, msgs in enumerate(prompts):
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inp = tok(text, return_tensors="pt", truncation=True, max_length=8192).to(model.device)
        out = model.generate(
            **inp, max_new_tokens=max_new_tokens, do_sample=False,
            temperature=1.0, repetition_penalty=1.05,
            pad_token_id=tok.pad_token_id,
        )
        gen_ids = out[0][inp.input_ids.shape[1]:]
        answers.append(tok.decode(gen_ids, skip_special_tokens=True).strip())
        print(f"[{model_key}]   gen {i+1}/{len(prompts)}", flush=True)
    gen_s = time.time() - t_gen

    # Free GPU before loading the next model
    del model, tok
    free_gpu()
    print(f"[{model_key}] DONE  load={load_s:.1f}s  gen_total={gen_s:.1f}s  "
          f"per_query={gen_s/max(len(prompts),1):.1f}s", flush=True)
    return answers, gen_s


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--candidate-k", type=int, default=30)
    p.add_argument("--max-new-tokens", type=int, default=400)
    p.add_argument("--models", nargs="+",
                   default=["qwen25-ft", "qwen25-base", "qwen35", "qwen36", "gemma4"],
                   help="Comma-separated model keys from MODELS dict")
    p.add_argument("--out", type=str, default=str(OUT))
    args = p.parse_args()

    # 1. Pick val queries (rag_qa task type)
    picks = []
    with open(VAL) as f:
        for line in f:
            r = json.loads(line)
            if r.get("task_type") == "rag_qa" and r.get("parent_id") is not None:
                picks.append(r)
            if len(picks) >= args.n:
                break
    print(f"[setup] {len(picks)} val queries", flush=True)

    # 2. Pre-compute retrieval contexts (one pass, ~25 min for embedding load)
    contexts = precompute_contexts(picks, args.top_k, args.candidate_k)
    prompts = [build_messages(c["question"], c["sources"]) for c in contexts]

    # 3. Run each model sequentially
    results: dict[str, dict] = {}
    for mkey in args.models:
        if mkey not in MODELS:
            print(f"[skip] unknown model key: {mkey}", flush=True)
            continue
        try:
            answers, gen_s = generate_with_model(mkey, prompts, args.max_new_tokens)
            results[mkey] = {
                "path": MODELS[mkey],
                "answers": answers,
                "gen_s": gen_s,
            }
        except Exception as e:
            print(f"[{mkey}] FAILED: {e}", flush=True)
            results[mkey] = {"path": MODELS[mkey], "error": str(e)}

    # 4. Write side-by-side markdown
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# Multi-Model RAG Comparison (n={len(picks)}, top_k={args.top_k})\n\n")
        f.write("Pre-computed retrieval contexts (hybrid+rerank); each model generates from the same prompts.\n\n")
        f.write("## Models tested\n\n")
        for mk, r in results.items():
            if "error" in r:
                f.write(f"- ❌ **{mk}** (`{r['path']}`) — {r['error']}\n")
            else:
                f.write(f"- ✅ **{mk}** (`{r['path']}`) — total gen time {r['gen_s']:.1f}s, "
                        f"avg {r['gen_s']/max(len(picks),1):.1f}s/query\n")
        f.write("\n")

        for i, ctx in enumerate(contexts):
            hit = "✓" if ctx["gold_in_top"] else "✗"
            f.write(f"## [{i+1}/{len(contexts)}] {hit} gold parent {ctx['gold']}\n\n")
            f.write(f"**Q:** {ctx['question']}\n\n")
            f.write(f"**REF (gold answer):** {ctx['ref'][:600]}{'…' if len(ctx['ref'])>600 else ''}\n\n")
            for mk, r in results.items():
                if "error" in r:
                    continue
                f.write(f"**{mk}:**\n\n{r['answers'][i]}\n\n---\n\n")

    print(f"\nWrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
