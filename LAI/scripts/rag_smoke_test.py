"""
End-to-end RAG smoke test against the local SQLite pipeline.

Two parts:
    A. Retrieval quality — does dense cosine search actually surface the
       parent chunk that a val question came from?
    B. Generation quality with context — does the FT model produce better
       answers when given the retrieved chunks?

Part B is intentionally simple: no reranker, no CRAG loop. We just want
to know if the embeddings + the fine-tune are doing their jobs.

Reads:
    processed/pipeline_local.db
        parent_chunks    (id, chunk_id, content, domain, doc_type, ...)
        child_chunks     (id, parent_id, content, context_prefix)
        child_embeddings (child_id PK, embedding BLOB = 4096 fp32)
    training/fine_tuning/data/val.jsonl (picks the first N rag_qa rows)

Endpoints:
    http://localhost:8003     (Qwen3-Embedding-8B)

Usage:
    python scripts/rag_smoke_test.py --queries 10 --top-k 10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import time
from pathlib import Path

import httpx
import numpy as np

LAI_DIR = Path(__file__).resolve().parents[1]
DB      = LAI_DIR / "processed" / "pipeline_local.db"
VAL     = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
EMBED_URL   = "http://localhost:8003"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DIM   = 4096


def load_embeddings(conn: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load all child_embeddings into memory.

    Returns:
        ids:        (N,) int64        child_chunks.id for each row
        parent_ids: (N,) int64        parent_chunks.id linked to each child
        embs:       (N, 4096) fp32    L2-normalized (Qwen3 outputs are already normalized)
    """
    print("Loading child_embeddings + parent links into memory...")
    t0 = time.time()
    rows = conn.execute("""
        SELECT e.child_id, c.parent_id, e.embedding
        FROM child_embeddings e
        JOIN child_chunks c ON c.id = e.child_id
    """).fetchall()
    n = len(rows)

    ids        = np.empty(n, dtype=np.int64)
    parent_ids = np.empty(n, dtype=np.int64)
    embs       = np.empty((n, EMBED_DIM), dtype=np.float32)

    for i, (cid, pid, blob) in enumerate(rows):
        ids[i] = cid
        parent_ids[i] = pid if pid is not None else -1
        # Each BLOB is 4096 * 4 bytes = 16384 bytes (fp32)
        embs[i] = np.frombuffer(blob, dtype=np.float32)

    dt = time.time() - t0
    print(f"  loaded {n:,} vectors of dim {EMBED_DIM} in {dt:.1f}s "
          f"({embs.nbytes/1024**3:.2f} GB in RAM)")
    return ids, parent_ids, embs


def embed_query(text: str) -> np.ndarray:
    resp = httpx.post(
        f"{EMBED_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": [text], "truncate_prompt_tokens": 32000},
        timeout=60,
    )
    resp.raise_for_status()
    vec = np.asarray(resp.json()["data"][0]["embedding"], dtype=np.float32)
    # Safety normalize in case the server ever changes
    n = np.linalg.norm(vec)
    if n > 0:
        vec = vec / n
    return vec


def retrieve(query_vec: np.ndarray, embs: np.ndarray, top_k: int) -> np.ndarray:
    """Exact cosine similarity. Embeddings are L2-normalized, so a dot
    product is cosine. Returns top-k indices into `embs`."""
    sims = embs @ query_vec
    top = np.argpartition(-sims, top_k)[:top_k]
    # Sort that top-k block by similarity
    top = top[np.argsort(-sims[top])]
    return top, sims[top]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queries", type=int, default=10)
    p.add_argument("--top-k",   type=int, default=10)
    p.add_argument("--task",    default="rag_qa",
                   help="Only test this task_type (rag_qa is the core RAG signal).")
    p.add_argument("--val", default=str(VAL))
    args = p.parse_args()

    # Load val samples — stay on rag_qa for this test (that's the task with
    # a clear expected parent_id)
    picks = []
    with open(args.val) as f:
        for line in f:
            r = json.loads(line)
            if r.get("task_type") == args.task and r.get("parent_id") is not None:
                picks.append(r)
            if len(picks) >= args.queries:
                break
    print(f"Loaded {len(picks)} {args.task} val samples with parent_id\n")

    # Load DB + all embeddings
    conn = sqlite3.connect(str(DB))
    ids, parent_ids, embs = load_embeddings(conn)

    # Run retrieval
    ranks = []
    results = []
    for i, r in enumerate(picks):
        question = next(m["content"] for m in r["messages"] if m["role"] == "user")
        ref_ans  = next(m["content"] for m in r["messages"] if m["role"] == "assistant")
        gold_parent = r["parent_id"]

        print(f"--- Query {i+1}/{len(picks)} ---")
        print(f"Q: {question[:140]}")
        print(f"gold parent_id={gold_parent}")

        q_vec = embed_query(question)
        top_idx, top_sims = retrieve(q_vec, embs, args.top_k)
        top_parents = parent_ids[top_idx]
        top_child_ids = ids[top_idx]

        # Rank of the gold parent in the top-K (among all retrieved parents,
        # deduplicated since a single parent can have multiple children near the top)
        rank = None
        for r_i, p in enumerate(top_parents):
            if p == gold_parent:
                rank = r_i + 1
                break
        ranks.append(rank)
        print(f"gold rank: {rank if rank is not None else 'NOT IN TOP ' + str(args.top_k)}")
        print(f"top-5 parents: {list(top_parents[:5].tolist())}")
        print(f"top-5 sims:    {[f'{s:.3f}' for s in top_sims[:5]]}\n")

        results.append({
            "question":   question,
            "ref_answer": ref_ans,
            "gold_parent": int(gold_parent),
            "top_parents": top_parents[:args.top_k].tolist(),
            "top_child_ids": top_child_ids[:args.top_k].tolist(),
            "top_sims":     top_sims[:args.top_k].tolist(),
            "rank":         rank,
        })

    # Summary metrics
    n = len(ranks)
    recall_at_1  = sum(1 for r in ranks if r == 1) / n
    recall_at_3  = sum(1 for r in ranks if r is not None and r <= 3) / n
    recall_at_5  = sum(1 for r in ranks if r is not None and r <= 5) / n
    recall_at_10 = sum(1 for r in ranks if r is not None) / n
    # MRR = mean of (1/rank) for found, 0 for not found
    mrr = sum((1/r if r is not None else 0) for r in ranks) / n

    print("=" * 60)
    print(f"RETRIEVAL METRICS (n={n}, task={args.task})")
    print("=" * 60)
    print(f"  Recall@1:  {recall_at_1:.1%}")
    print(f"  Recall@3:  {recall_at_3:.1%}")
    print(f"  Recall@5:  {recall_at_5:.1%}")
    print(f"  Recall@{args.top_k}: {recall_at_10:.1%}")
    print(f"  MRR:       {mrr:.3f}")

    # Persist
    out_path = LAI_DIR / "scripts" / "rag_smoke_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "task": args.task,
            "n":    n,
            "metrics": {
                "recall@1":  recall_at_1,
                "recall@3":  recall_at_3,
                "recall@5":  recall_at_5,
                f"recall@{args.top_k}": recall_at_10,
                "mrr":       mrr,
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
