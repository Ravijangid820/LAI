"""
RAG retrieval eval harness.

Measures Recall@K and MRR of the dense/hybrid/reranked pipeline against
val.jsonl questions with known source parent_ids.

Supports multiple retrieval modes — same val set, same gold labels, so
results are directly comparable:

    --mode dense            pure Qwen3 cosine (current baseline)
    --mode dense_prefix     Qwen3 with "Instruct: ... Query: ..." prefix
    --mode bm25             pure SQLite FTS5 BM25
    --mode hybrid           dense + BM25 with RRF fusion
    --mode hybrid_prefix    hybrid with Qwen3 query prefix
    --mode hybrid_rerank    hybrid candidates -> bge-reranker-v2-m3 top-K
                            (reranker loaded only if used)

Usage:
    python scripts/rag_eval.py --mode dense --n 200
    python scripts/rag_eval.py --mode dense_prefix --n 200
    python scripts/rag_eval.py --mode hybrid --n 200
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

LAI_DIR = Path(__file__).resolve().parents[1]
DB      = LAI_DIR / "processed" / "pipeline_local.db"
VAL     = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
OUT_DIR = LAI_DIR / "scripts" / "rag_eval_results"

EMBED_URL   = "http://localhost:8003"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DIM   = 4096

QWEN3_QUERY_INSTRUCTION = (
    "Given a user's question about German legal, wind-energy, or "
    "due-diligence matters, retrieve the most relevant passages."
)


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------

@dataclass
class Corpus:
    child_ids:  np.ndarray  # (N,) int64
    parent_ids: np.ndarray  # (N,) int64
    embs:       np.ndarray  # (N, 4096) fp32

    # For BM25 (optional, loaded on demand)
    bm25_conn: Optional[sqlite3.Connection] = None


def load_embeddings(conn: sqlite3.Connection) -> Corpus:
    print("Loading child embeddings into RAM...")
    t0 = time.time()
    rows = conn.execute("""
        SELECT e.child_id, c.parent_id, e.embedding
        FROM child_embeddings e
        JOIN child_chunks c ON c.id = e.child_id
    """).fetchall()
    n = len(rows)

    child_ids  = np.empty(n, dtype=np.int64)
    parent_ids = np.empty(n, dtype=np.int64)
    embs       = np.empty((n, EMBED_DIM), dtype=np.float32)

    for i, (cid, pid, blob) in enumerate(rows):
        child_ids[i]  = cid
        parent_ids[i] = pid if pid is not None else -1
        embs[i]       = np.frombuffer(blob, dtype=np.float32)

    print(f"  {n:,} vectors loaded in {time.time()-t0:.1f}s "
          f"({embs.nbytes/1024**3:.2f} GB)")
    return Corpus(child_ids=child_ids, parent_ids=parent_ids, embs=embs)


def ensure_bm25(corpus: Corpus, conn: sqlite3.Connection) -> None:
    """Build (once) a SQLite FTS5 virtual table over child_chunks.content.
    Stored in the same DB so subsequent runs are instant."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='child_chunks_fts'"
    )
    if cur.fetchone():
        corpus.bm25_conn = conn
        return

    print("Building FTS5 index (one-time; may take a couple minutes)...")
    t0 = time.time()
    conn.execute("""
        CREATE VIRTUAL TABLE child_chunks_fts USING fts5(
            content,
            content='child_chunks',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    conn.execute("INSERT INTO child_chunks_fts(child_chunks_fts) VALUES('rebuild')")
    conn.commit()
    print(f"  FTS5 built in {time.time()-t0:.1f}s")
    corpus.bm25_conn = conn


# -----------------------------------------------------------------------------
# Retrievers
# -----------------------------------------------------------------------------

def embed_query(text: str, with_prefix: bool = False) -> np.ndarray:
    if with_prefix:
        text = f"Instruct: {QWEN3_QUERY_INSTRUCTION}\nQuery: {text}"
    resp = httpx.post(
        f"{EMBED_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": [text],
              "truncate_prompt_tokens": 32000},
        timeout=60,
    )
    resp.raise_for_status()
    v = np.asarray(resp.json()["data"][0]["embedding"], dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def retrieve_dense(q_vec: np.ndarray, corpus: Corpus, k: int) -> list[int]:
    """Returns child row indices (positions in corpus.embs), ordered by sim."""
    sims = corpus.embs @ q_vec
    top  = np.argpartition(-sims, k)[:k]
    top  = top[np.argsort(-sims[top])]
    return top.tolist(), sims[top].tolist()


def retrieve_bm25(query: str, corpus: Corpus, k: int) -> tuple[list[int], list[float]]:
    """Use FTS5 bm25(). Returns child row indices + scores."""
    conn = corpus.bm25_conn
    # FTS5 MATCH needs special char handling; wrap in quotes
    # Also treat the query as a "simple" query (OR over tokens), not full MATCH syntax
    safe = query.replace('"', ' ').strip()
    # Split into up-to 15 most informative-looking tokens; longer tokens first
    tokens = sorted(set(t for t in safe.split() if len(t) > 2), key=len, reverse=True)[:15]
    if not tokens:
        return [], []
    match_expr = " OR ".join(f'"{t}"' for t in tokens)
    rows = conn.execute("""
        SELECT rowid, bm25(child_chunks_fts) AS score
        FROM child_chunks_fts
        WHERE child_chunks_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """, (match_expr, k)).fetchall()
    if not rows:
        return [], []

    # Map rowid -> position in corpus arrays (which are ordered by row in load)
    rowid_to_idx = {int(c): i for i, c in enumerate(corpus.child_ids)}
    indices, scores = [], []
    for rowid, score in rows:
        if rowid in rowid_to_idx:
            indices.append(rowid_to_idx[rowid])
            scores.append(-score)   # bm25() returns negative scores; flip for "higher is better"
    return indices, scores


def rrf_fuse(rankings: list[list[int]], k_rrf: int = 60) -> list[tuple[int, float]]:
    """Reciprocal rank fusion — stable for mixing dense + BM25 ranks."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k_rrf + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


# -----------------------------------------------------------------------------
# Optional: reranker
# -----------------------------------------------------------------------------

class Reranker:
    """Lazy-loaded reranker. Supports both:
    - cross-encoder / sequence-classification models (bge-reranker-*)
    - Qwen3-Reranker (causal LM that scores by predicting "yes"/"no")
    """
    def __init__(self, model_id: str = "Qwen/Qwen3-Reranker-8B"):
        import torch
        self.torch = torch
        self.model_id = model_id
        self.is_qwen3 = "Qwen3-Reranker" in model_id

        print(f"Loading reranker {model_id}...")
        if self.is_qwen3:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(
                model_id, padding_side="left", trust_remote_code=True,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                model_id, torch_dtype=torch.float16, device_map="cuda",
                trust_remote_code=True,
            ).eval()
            self.token_yes = self.tok.convert_tokens_to_ids("yes")
            self.token_no  = self.tok.convert_tokens_to_ids("no")
            # The standard Qwen3-Reranker scoring prompt
            self.prefix = (
                "<|im_start|>system\nJudge whether the Document meets the "
                "requirements based on the Query and the Instruct provided. "
                "Note that the answer can only be \"yes\" or \"no\"."
                "<|im_end|>\n<|im_start|>user\n"
            )
            self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
            self.instruction = (
                "Given a user's question about German legal, wind-energy, or "
                "due-diligence matters, retrieve the most relevant passages."
            )
        else:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(model_id)
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_id, torch_dtype=torch.float16, device_map="cuda",
            ).eval()

    def _score_qwen3(self, pairs: list[tuple[str, str]]) -> list[float]:
        """For Qwen3-Reranker, score = P(yes) - P(no) over the next-token logits."""
        import torch
        batch = 8
        all_scores: list[float] = []
        for i in range(0, len(pairs), batch):
            b = pairs[i:i+batch]
            texts = [
                f"{self.prefix}<Instruct>: {self.instruction}\n"
                f"<Query>: {q}\n<Document>: {d}{self.suffix}"
                for q, d in b
            ]
            enc = self.tok(
                texts, padding=True, truncation=True, max_length=8192,
                return_tensors="pt",
            ).to("cuda")
            with torch.no_grad():
                logits = self.model(**enc).logits[:, -1, :]  # next-token dist
            yes = logits[:, self.token_yes]
            no  = logits[:, self.token_no]
            # log-odds; higher = more relevant
            scores = (yes - no).float().cpu().numpy().tolist()
            all_scores.extend(scores)
        return all_scores

    def _score_seqcls(self, pairs: list[tuple[str, str]]) -> list[float]:
        import torch
        all_scores = []
        batch = 16
        for i in range(0, len(pairs), batch):
            b = pairs[i:i+batch]
            enc = self.tok([p[0] for p in b], [p[1] for p in b],
                            padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to("cuda")
            with torch.no_grad():
                logits = self.model(**enc).logits.view(-1).float().cpu().numpy()
            all_scores.extend(logits.tolist())
        return all_scores

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self.is_qwen3:
            return self._score_qwen3(pairs)
        return self._score_seqcls(pairs)


# -----------------------------------------------------------------------------
# Eval loop
# -----------------------------------------------------------------------------

def load_val_queries(path: str, n: int, task_filter: str = "rag_qa") -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if task_filter and r.get("task_type") != task_filter:
                continue
            if r.get("parent_id") is None:
                continue
            out.append(r)
            if len(out) >= n:
                break
    return out


def dedupe_by_parent(idx_list: list[int], corpus: Corpus, k: int) -> list[int]:
    """Compress child-level results to unique parent_ids (in order)."""
    seen = set()
    parents = []
    for ci in idx_list:
        pid = int(corpus.parent_ids[ci])
        if pid not in seen:
            seen.add(pid)
            parents.append(pid)
        if len(parents) >= k:
            break
    return parents


def eval_run(mode: str, queries: list[dict], corpus: Corpus,
             top_k: int = 10, candidate_k: int = 50,
             reranker: Reranker = None,
             parent_text: dict = None) -> dict:
    ranks = []
    per_q = []
    t0 = time.time()

    for i, q in enumerate(queries):
        question = next(m["content"] for m in q["messages"] if m["role"] == "user")
        gold = int(q["parent_id"])

        # Retrieve candidates
        if mode == "dense":
            idx, sims = retrieve_dense(embed_query(question), corpus, candidate_k)
        elif mode == "dense_prefix":
            idx, sims = retrieve_dense(embed_query(question, with_prefix=True), corpus, candidate_k)
        elif mode == "bm25":
            idx, sims = retrieve_bm25(question, corpus, candidate_k)
        elif mode in ("hybrid", "hybrid_prefix", "hybrid_rerank"):
            qvec = embed_query(question, with_prefix=(mode != "hybrid"))
            d_idx, _ = retrieve_dense(qvec, corpus, candidate_k)
            b_idx, _ = retrieve_bm25(question, corpus, candidate_k)
            fused = rrf_fuse([d_idx, b_idx])[:candidate_k]
            idx = [p for p, _ in fused]
            sims = [s for _, s in fused]
        else:
            raise ValueError(f"unknown mode: {mode}")

        if mode == "hybrid_rerank" and reranker is not None and parent_text is not None:
            pairs = []
            for ci in idx:
                pid = int(corpus.parent_ids[ci])
                pairs.append((question, parent_text.get(pid, "")[:2000]))
            scores = reranker.score(pairs)
            order = np.argsort(-np.asarray(scores))
            idx = [idx[j] for j in order]
            sims = [scores[j] for j in order]

        top_parents = dedupe_by_parent(idx, corpus, top_k)

        rank = None
        for r_i, p in enumerate(top_parents):
            if p == gold:
                rank = r_i + 1
                break
        ranks.append(rank)

        per_q.append({
            "question":     question,
            "gold_parent":  gold,
            "top_parents":  top_parents,
            "rank":         rank,
        })

        if (i+1) % 25 == 0:
            print(f"  [{i+1}/{len(queries)}] mode={mode}")

    dt = time.time() - t0

    n = len(ranks)
    recall_at = lambda k: sum(1 for r in ranks if r is not None and r <= k) / n
    mrr = sum((1/r) if r is not None else 0 for r in ranks) / n

    metrics = {
        "n":        n,
        "elapsed":  round(dt, 1),
        "q_per_s":  round(n/dt, 2),
        "recall@1":  round(recall_at(1),  3),
        "recall@3":  round(recall_at(3),  3),
        "recall@5":  round(recall_at(5),  3),
        "recall@10": round(recall_at(10), 3),
        "mrr":       round(mrr, 3),
    }
    return {"mode": mode, "metrics": metrics, "per_query": per_q}


def load_parent_texts(conn: sqlite3.Connection) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT id, content FROM parent_chunks"
    )}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True,
                   choices=["dense", "dense_prefix", "bm25",
                            "hybrid", "hybrid_prefix", "hybrid_rerank"])
    p.add_argument("--n", type=int, default=100,
                   help="Number of val queries to evaluate.")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--candidate-k", type=int, default=50,
                   help="How many candidates to pull before dedup / rerank.")
    p.add_argument("--rerank-model", default="Qwen/Qwen3-Reranker-8B",
                   help="Qwen3-Reranker-* uses causal scoring (yes/no probs); "
                        "bge-reranker-* uses sequence-classification. Both work.")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else \
               OUT_DIR / f"{args.mode}_n{args.n}.json"

    queries = load_val_queries(str(VAL), args.n)
    print(f"Loaded {len(queries)} val queries")

    conn = sqlite3.connect(str(DB))
    corpus = load_embeddings(conn)

    if args.mode in ("bm25", "hybrid", "hybrid_prefix", "hybrid_rerank"):
        ensure_bm25(corpus, conn)

    reranker = None
    parent_text = None
    if args.mode == "hybrid_rerank":
        reranker = Reranker(args.rerank_model)
        parent_text = load_parent_texts(conn)

    result = eval_run(args.mode, queries, corpus,
                       top_k=args.top_k, candidate_k=args.candidate_k,
                       reranker=reranker, parent_text=parent_text)

    print()
    print("=" * 60)
    print(f"RESULTS — mode={args.mode}, n={args.n}")
    print("=" * 60)
    for k, v in result["metrics"].items():
        print(f"  {k:>10s}: {v}")

    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
