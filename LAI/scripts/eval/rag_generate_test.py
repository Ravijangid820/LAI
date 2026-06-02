"""
FT+RAG end-to-end test.

For each val query:
    1. Retrieve top-K parent chunks using hybrid+prefix+rerank (our best retriever)
    2. Build a RAG prompt with the retrieved chunks as context
    3. Generate an answer with BOTH the base and fine-tuned models
    4. Print side-by-side with the reference answer

Output is manual-inspection style (like compare_base_vs_ft.py) — the fix
for "FT hallucinates without context" is checking that FT does NOT
hallucinate when the right context is present.

Usage:
    python scripts/rag_generate_test.py --n 5 --top-k 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

LAI_DIR = Path(__file__).resolve().parents[2]
DB      = LAI_DIR / "processed" / "pipeline_local.db"
VAL     = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
OUT_DIR = LAI_DIR / "scripts" / "eval" / "rag_eval_results"

EMBED_URL   = "http://localhost:8003"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DIM   = 4096

QWEN3_QUERY_INSTRUCTION = (
    "Given a user's question about German legal, wind-energy, or "
    "due-diligence matters, retrieve the most relevant passages."
)

# Reuse the proven-best retriever
from lai.search.eval import (
    load_embeddings, ensure_bm25, embed_query, retrieve_dense,
    retrieve_bm25, rrf_fuse, Reranker, load_parent_texts, dedupe_by_parent,
)


RAG_SYSTEM = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage ausschließlich auf "
    "Grundlage der unten bereitgestellten Rechtstexte. Zitiere bei jeder "
    "Aussage den entsprechenden Quellabschnitt (z.B. [Quelle 1]). "
    "Wenn die Frage mit den Quellen nicht eindeutig beantwortet werden "
    "kann, gib das ehrlich an."
)


def build_rag_prompt(question: str, sources: list[str]) -> list[dict]:
    src_block = "\n\n".join(
        f"[Quelle {i+1}]\n{s}" for i, s in enumerate(sources)
    )
    user = f"Rechtstexte:\n{src_block}\n\nFrage: {question}"
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user",   "content": user},
    ]


def load_lm(path: str, device: str = "cuda"):
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        path, dtype=torch.bfloat16, device_map=device, trust_remote_code=True,
    ).eval()
    return tok, model


@torch.no_grad()
def generate(model, tok, messages, max_new_tokens=400):
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt", truncation=True, max_length=8192).to(model.device)
    out = model.generate(
        **inp, max_new_tokens=max_new_tokens, do_sample=False,
        temperature=1.0, repetition_penalty=1.05,
        pad_token_id=tok.pad_token_id,
    )
    gen_ids = out[0][inp.input_ids.shape[1]:]
    return tok.decode(gen_ids, skip_special_tokens=True).strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--top-k", type=int, default=3,
                   help="How many retrieved parent chunks to include in the RAG prompt.")
    p.add_argument("--candidate-k", type=int, default=30)
    p.add_argument("--base",  default="Qwen/Qwen2.5-7B-Instruct")
    p.add_argument("--ft",    default="/data/projects/lai/models/qwen25-7b-legal-lora-v2-merged")
    args = p.parse_args()

    # ---- 1. Pick queries
    picks = []
    with open(VAL) as f:
        for line in f:
            r = json.loads(line)
            if r.get("task_type") == "rag_qa" and r.get("parent_id") is not None:
                picks.append(r)
            if len(picks) >= args.n:
                break
    print(f"Loaded {len(picks)} val queries")

    # ---- 2. Set up retriever (hybrid + prefix + rerank)
    conn = sqlite3.connect(str(DB))
    # Some rows from .recover'd corpus have stray non-UTF-8 bytes (mangled
    # umlauts from PDF extraction). Replace rather than crash.
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    corpus = load_embeddings(conn)
    ensure_bm25(corpus, conn)
    parent_text = load_parent_texts(conn)
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")

    # ---- 3. Retrieve for each query
    retrieved: list[list[int]] = []
    for i, q in enumerate(picks):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        print(f"\n[{i+1}/{len(picks)}] retrieving: {question[:80]}...")
        qvec = embed_query(question, with_prefix=True)
        d_idx, _ = retrieve_dense(qvec, corpus, args.candidate_k)
        b_idx, _ = retrieve_bm25(question, corpus, args.candidate_k)
        fused = rrf_fuse([d_idx, b_idx])[:args.candidate_k]
        cands = [p for p, _ in fused]
        pairs = [(question, parent_text.get(int(corpus.parent_ids[c]), "")[:2000])
                 for c in cands]
        scores = reranker.score(pairs)
        order  = np.argsort(-np.asarray(scores))
        idx    = [cands[j] for j in order]
        top_parents = dedupe_by_parent(idx, corpus, args.top_k)
        retrieved.append(top_parents)
        print(f"  top parents: {top_parents}  (gold: {q['parent_id']})")

    # Reranker done with GPU; free it up for LM loading
    del reranker.model, reranker.tok, reranker
    import gc; gc.collect(); torch.cuda.empty_cache()

    # ---- 4. Generate with BASE
    print(f"\n=== Loading BASE: {args.base} ===")
    tok_b, model_b = load_lm(args.base)
    base_out = []
    for i, q in enumerate(picks):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        sources = [parent_text.get(int(p), "")[:1500] for p in retrieved[i]]
        msgs = build_rag_prompt(question, sources)
        print(f"  base gen {i+1}/{len(picks)}")
        base_out.append(generate(model_b, tok_b, msgs))
    del model_b, tok_b; gc.collect(); torch.cuda.empty_cache()

    # ---- 5. Generate with FT
    print(f"\n=== Loading FT: {args.ft} ===")
    tok_f, model_f = load_lm(args.ft)
    ft_out = []
    for i, q in enumerate(picks):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        sources = [parent_text.get(int(p), "")[:1500] for p in retrieved[i]]
        msgs = build_rag_prompt(question, sources)
        print(f"  ft gen {i+1}/{len(picks)}")
        ft_out.append(generate(model_f, tok_f, msgs))
    del model_f, tok_f; gc.collect(); torch.cuda.empty_cache()

    # ---- 6. Report
    print("\n\n" + "=" * 80)
    print("FT + RAG  vs  BASE + RAG")
    print("=" * 80)
    for i, q in enumerate(picks):
        ref = next(m["content"] for m in q["messages"] if m["role"] == "assistant")
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        gold = q["parent_id"]
        gold_in_top = int(gold) in retrieved[i]
        print(f"\n\n--- [{i+1}/{len(picks)}] gold_parent={gold} ({'✓ in retrieved' if gold_in_top else '✗ NOT in retrieved'}) ---")
        print(f"Q: {question}\n")
        print(f"[REF]\n{ref}\n")
        print(f"[BASE+RAG]\n{base_out[i]}\n")
        print(f"[FT+RAG]\n{ft_out[i]}\n")
        print("-" * 80)

    OUT_DIR.mkdir(exist_ok=True)
    outp = OUT_DIR / "rag_generate_results.json"
    with open(outp, "w", encoding="utf-8") as f:
        json.dump([{
            "question":  next(m["content"] for m in q["messages"] if m["role"]=="user"),
            "ref":       next(m["content"] for m in q["messages"] if m["role"]=="assistant"),
            "gold_parent": q["parent_id"],
            "retrieved": retrieved[i],
            "base":      base_out[i],
            "ft":        ft_out[i],
        } for i, q in enumerate(picks)], f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {outp}")


if __name__ == "__main__":
    main()
