"""
vLLM-API multi-model comparison.

Cycles through requested models by spawning a vllm/vllm-openai container
for each, querying via the OpenAI-compatible endpoint, then stopping
the container and moving to the next.

Why this beats `multi_model_compare.py`:
  - Doesn't depend on `transformers` knowing the architecture
    (vLLM has its own model registry). Qwen3.5/Qwen3.6/Gemma-4 fail
    in transformers but load in vLLM 0.19.1.
  - No max_tokens cap by default — reasoning models (Qwen3.x) need
    several thousand tokens for thinking + answer. We pass max_tokens
    very high and rely on the model finishing naturally.
  - Long context (32k) so uploaded contracts fit alongside RAG sources.

Reuses pre-computed RAG contexts from disk if available
(`rag_eval_results/contexts.json`), else computes them once.

Usage:
    cd /data/projects/lai/LAI
    .venv/bin/python scripts/vllm_compare.py \
        --n 5 --top-k 3 \
        --models qwen35 qwen36 gemma4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx

LAI_DIR = Path(__file__).resolve().parents[2]
DB      = LAI_DIR / "processed" / "pipeline_local.db"
VAL     = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
OUT_DIR = LAI_DIR / "scripts" / "eval" / "rag_eval_results"
CONTEXTS_CACHE = OUT_DIR / "contexts.json"
OUT_MD  = OUT_DIR / "vllm_compare.md"
HF_CACHE = LAI_DIR / ".runtime-cache" / "hf"


# Model registry: id used in --models flag → vLLM model arg
MODELS: dict[str, dict] = {
    "qwen35":   {"path": "Qwen/Qwen3.5-27B",        "gpu_mem": 0.75, "max_model_len": 32768},
    "qwen36":   {"path": "Qwen/Qwen3.6-27B",        "gpu_mem": 0.75, "max_model_len": 32768},
    "gemma4":   {"path": "google/gemma-4-E4B-it",   "gpu_mem": 0.45, "max_model_len": 32768},
    "qwen25-base":{"path": "Qwen/Qwen2.5-7B-Instruct", "gpu_mem": 0.40, "max_model_len": 8192},
}


CONTAINER = "lai_vllm_eval"
PORT = 18001


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------

def docker_run(spec: dict) -> None:
    """Launch vLLM container with the given model. Blocks until ready."""
    print(f"[docker] launching {spec['path']} ...", flush=True)
    args = [
        "docker", "run", "-d",
        "--name", CONTAINER,
        "--restart", "no",
        "--ipc", "host",
        "--gpus", "device=0",
        "--network", "lai_network",
        "-v", f"{HF_CACHE}:/root/.cache/huggingface:rw",
        "-p", f"{PORT}:8000",
        "vllm/vllm-openai:latest",
        spec["path"],
        "--dtype", "auto",
        "--trust-remote-code",
        "--max-model-len", str(spec["max_model_len"]),
        "--gpu-memory-utilization", str(spec["gpu_mem"]),
    ]
    subprocess.run(args, check=True, capture_output=True)
    # Wait for /v1/models to respond
    deadline = time.time() + 1200  # 20 min max
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://localhost:{PORT}/v1/models", timeout=2)
            if r.status_code == 200 and spec["path"] in r.text:
                print(f"[docker] ready after {time.time() - (deadline - 1200):.0f}s", flush=True)
                return
        except httpx.HTTPError:
            pass
        # Surface last log line for visibility
        try:
            log = subprocess.run(
                ["docker", "logs", CONTAINER, "--tail", "1"],
                capture_output=True, text=True, timeout=5,
            ).stderr.strip()
            if log:
                print(f"[docker] ... {log[:160]}", flush=True)
        except Exception:
            pass
        time.sleep(20)
    raise RuntimeError(f"vLLM container did not become ready in 20 min")


def docker_stop() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER],
                   capture_output=True, check=False)
    time.sleep(5)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate(model_path: str, messages: list[dict],
             max_tokens: int = 8000, temperature: float = 0.0,
             timeout: float = 600.0) -> tuple[str, dict]:
    body = {
        "model": model_path,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    r = httpx.post(f"http://localhost:{PORT}/v1/chat/completions",
                   json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    return text, data.get("usage", {})


def strip_reasoning(text: str) -> tuple[str, str]:
    """Reasoning models emit `<think>...</think>` (or sometimes other tags)
    before the final answer. Return (final_answer, reasoning_trace)."""
    # Common reasoning markers across Qwen3.x / DeepSeek-R1
    # Format 1: <think>...</think>final_answer
    m = re.search(r"</think>\s*", text)
    if m:
        return text[m.end():].strip(), text[:m.end()].strip()
    # Format 2: "Thinking Process:" header → answer at the end after blank
    if text.startswith("Thinking Process:") or "Thinking Process:" in text[:100]:
        # Heuristic: take the last paragraph as the answer
        parts = re.split(r"\n\s*\n", text.rstrip())
        return parts[-1].strip(), "\n\n".join(parts[:-1]).strip()
    return text.strip(), ""


# ---------------------------------------------------------------------------
# Pre-computed retrieval contexts
# ---------------------------------------------------------------------------

def get_or_compute_contexts(n: int, top_k: int, candidate_k: int) -> list[dict]:
    if CONTEXTS_CACHE.exists():
        ctxs = json.loads(CONTEXTS_CACHE.read_text())
        if len(ctxs) >= n:
            print(f"[rag] reusing cached contexts ({len(ctxs)} queries)", flush=True)
            return ctxs[:n]

    print(f"[rag] computing retrieval contexts for {n} queries (slow first run)...", flush=True)
    from lai.search.eval import (
        load_embeddings, ensure_bm25, embed_query,
        retrieve_dense, retrieve_bm25, rrf_fuse, Reranker,
        load_parent_texts, dedupe_by_parent,
    )
    import numpy as np

    # Pick val queries
    picks: list[dict] = []
    with open(VAL) as f:
        for line in f:
            r = json.loads(line)
            if r.get("task_type") == "rag_qa" and r.get("parent_id") is not None:
                picks.append(r)
            if len(picks) >= n:
                break

    conn = sqlite3.connect(str(DB))
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    corpus = load_embeddings(conn)
    ensure_bm25(corpus, conn)
    parent_text = load_parent_texts(conn)
    print(f"[rag]   {len(corpus.embs):,} embeddings ready", flush=True)
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")

    out = []
    for i, q in enumerate(picks):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        ref      = next(m["content"] for m in q["messages"] if m["role"] == "assistant")
        gold     = q["parent_id"]

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
        out.append({
            "question": question,
            "ref": ref,
            "gold": gold,
            "gold_in_top": int(gold) in top_parents,
            "top_parents": [int(p) for p in top_parents],
            "sources": sources,
        })
        print(f"[rag] [{i+1}/{len(picks)}] gold_in_top={out[-1]['gold_in_top']}", flush=True)

    # Cache so next run skips the load
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CONTEXTS_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[rag] cached → {CONTEXTS_CACHE}", flush=True)

    # Free memory
    del reranker.model, reranker.tok, reranker
    del corpus, parent_text
    conn.close()
    import gc, torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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


def messages_for_model(messages: list[dict], model_path: str) -> list[dict]:
    """Some models reject the `system` role (Gemma family). Merge system
    instruction into the first user message for those."""
    if "gemma" in model_path.lower():
        sys_msgs = [m["content"] for m in messages if m["role"] == "system"]
        rest = [m for m in messages if m["role"] != "system"]
        if sys_msgs and rest and rest[0]["role"] == "user":
            rest[0] = {
                "role": "user",
                "content": "\n\n".join(sys_msgs) + "\n\n" + rest[0]["content"],
            }
            return rest
    return messages


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--candidate-k", type=int, default=30)
    p.add_argument("--max-tokens", type=int, default=12000,
                   help="Generous budget so reasoning models finish.")
    p.add_argument("--models", nargs="+",
                   default=["qwen35", "qwen36", "gemma4"],
                   help="Model keys from MODELS dict")
    args = p.parse_args()

    contexts = get_or_compute_contexts(args.n, args.top_k, args.candidate_k)
    prompts = [build_messages(c["question"], c["sources"]) for c in contexts]

    results: dict[str, dict] = {}
    for mkey in args.models:
        if mkey not in MODELS:
            print(f"[skip] unknown: {mkey}", flush=True)
            continue
        spec = MODELS[mkey]
        try:
            docker_stop()  # paranoid cleanup
            docker_run(spec)
            t0 = time.time()
            answers, reasonings = [], []
            for i, msgs in enumerate(prompts):
                t_q = time.time()
                text, usage = generate(
                    spec["path"],
                    messages_for_model(msgs, spec["path"]),
                    args.max_tokens,
                )
                ans, reasoning = strip_reasoning(text)
                answers.append(ans)
                reasonings.append(reasoning)
                print(f"[{mkey}] q{i+1}/{len(prompts)} "
                      f"gen_tok={usage.get('completion_tokens', 0)} "
                      f"({time.time()-t_q:.1f}s)", flush=True)
            results[mkey] = {
                "path":   spec["path"],
                "answers": answers,
                "reasonings": reasonings,
                "total_s": time.time() - t0,
            }
        except Exception as e:
            print(f"[{mkey}] FAILED: {e}", flush=True)
            results[mkey] = {"path": spec["path"], "error": str(e)}
        finally:
            docker_stop()

    # Markdown output
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(f"# vLLM-Container Multi-Model Comparison (n={len(contexts)})\n\n")
        f.write("Each model is loaded into a fresh vllm/vllm-openai container, queried, then torn down.\n")
        f.write("Reasoning models (Qwen3.x) emit `<think>...</think>` traces; we strip those for the answer column but preserve them in the report.\n\n")
        f.write("## Models\n\n")
        for mk, r in results.items():
            if "error" in r:
                f.write(f"- ❌ **{mk}** (`{r['path']}`) — {r['error']}\n")
            else:
                f.write(f"- ✅ **{mk}** (`{r['path']}`) — total {r['total_s']:.1f}s, "
                        f"avg {r['total_s']/max(len(contexts),1):.1f}s/query\n")
        f.write("\n")

        for i, ctx in enumerate(contexts):
            hit = "✓" if ctx["gold_in_top"] else "✗"
            f.write(f"## [{i+1}/{len(contexts)}] {hit} gold {ctx['gold']}\n\n")
            f.write(f"**Q:** {ctx['question']}\n\n")
            f.write(f"**REF:** {ctx['ref'][:600]}{'…' if len(ctx['ref'])>600 else ''}\n\n")
            for mk, r in results.items():
                if "error" in r:
                    continue
                f.write(f"### {mk}\n\n{r['answers'][i]}\n\n")
                if r['reasonings'][i]:
                    f.write("<details><summary>Reasoning trace</summary>\n\n")
                    f.write(f"{r['reasonings'][i][:2000]}{'…' if len(r['reasonings'][i])>2000 else ''}\n\n")
                    f.write("</details>\n\n")

    print(f"\nWrote {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
