"""
Pilot A/B test — does adding multilegalpile hurt or help retrieval?

Tests three search pools against the same 500 val queries (gold
parent_ids known):
  A. Baseline — existing 217K embeddings (hf_cases / openlegaldata /
     gesetz / vdr / dd_report).
  B. Baseline + 50K multilegalpile SIGNAL children (caselaw + legislation
     + contracts — dropping legal-mc4).
  C. Baseline + 50K multilegalpile NOISE children (legal-mc4 only —
     the web-crawled subset we suspect is noise).

If R@5 holds on B but drops on C, we have a defensible "keep signal,
drop legal-mc4" rule backed by numbers.

New embeddings go into a separate `pilot_embeddings` table so this
never touches `child_embeddings` used by main retrieval.

Usage:
    python scripts/pilot_mlp_ab.py --n 500
    python scripts/pilot_mlp_ab.py --skip-embed   # eval only, after embed
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import struct
import time
from pathlib import Path

import httpx
import numpy as np

LAI_DIR  = Path(__file__).resolve().parents[2]
DB       = LAI_DIR / "processed" / "pipeline_local.db"
VAL      = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
OUT_JSON = LAI_DIR / "scripts" / "archive" / "pilot_mlp_ab_results.json"

EMBED_URL   = "http://localhost:8003"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DIM   = 4096
BATCH       = 32

POOL_SIZE = 50_000
VAL_N_DEFAULT = 500

QWEN3_INSTR = ("Given a user's question about German legal, wind-energy, "
               "or due-diligence matters, retrieve the most relevant passages.")


# -----------------------------------------------------------------------------
# Pilot table
# -----------------------------------------------------------------------------

def ensure_pilot_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pilot_embeddings (
            child_id   INTEGER,
            pool       TEXT,
            embedding  BLOB,
            PRIMARY KEY (child_id, pool)
        )
    """)
    conn.commit()


MLP_PARENT_ROWID_MIN = 2_845_310  # boundary where multilegalpile parents start


def sample_pool_child_ids(conn: sqlite3.Connection, raw_types: list[str],
                          n: int, seed: int) -> list[tuple[int, str]]:
    """Return up to n (child_rowid, content) pairs from mlp raw_types.

    Uses random rowid seeds + small windows to avoid full table scans.
    Takes roughly one child per parent for diversity.
    """
    random.seed(seed)
    # Get current upper bound (Step 2 may still be adding rows)
    max_rowid = conn.execute(
        "SELECT MAX(rowid) FROM parent_chunks WHERE rowid >= ?",
        (MLP_PARENT_ROWID_MIN,)
    ).fetchone()[0] or MLP_PARENT_ROWID_MIN

    placeholders = ",".join("?" * len(raw_types))
    q_parents = f"""
      SELECT pc.rowid
      FROM parent_chunks pc
      WHERE pc.rowid BETWEEN ? AND ?
        AND json_extract(pc.metadata, '$.raw_type') IN ({placeholders})
      LIMIT 1500
    """
    parent_ids: set[int] = set()
    attempts = 0
    # Over-sample by 1.4× to account for dedupe and content-length filter rejects
    need_parents = int(n * 1.4)
    while len(parent_ids) < need_parents and attempts < 500:
        s = random.randint(MLP_PARENT_ROWID_MIN, max_rowid - 2000)
        rows = conn.execute(q_parents, (s, s + 2000, *raw_types)).fetchall()
        for (rid,) in rows:
            parent_ids.add(rid)
        attempts += 1

    # For each parent, grab one child of reasonable length
    parent_list = list(parent_ids)[:need_parents]
    q_child = """
      SELECT cc.rowid, cc.content FROM child_chunks cc
      WHERE cc.parent_id = ? AND length(cc.content) >= 100
      LIMIT 1
    """
    out: list[tuple[int, str]] = []
    for pid in parent_list:
        row = conn.execute(q_child, (pid,)).fetchone()
        if row:
            out.append(row)
            if len(out) >= n:
                break
    return out


# -----------------------------------------------------------------------------
# Embedding
# -----------------------------------------------------------------------------

def embed_texts(texts: list[str], with_prefix: bool = False) -> np.ndarray:
    if with_prefix:
        texts = [f"Instruct: {QWEN3_INSTR}\nQuery: {t}" for t in texts]
    resp = httpx.post(
        f"{EMBED_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": texts,
              "truncate_prompt_tokens": 32000},
        timeout=120,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda x: x["index"])
    arr = np.asarray([d["embedding"] for d in data], dtype=np.float32)
    # L2 normalize so dot = cosine
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def embed_pool(conn: sqlite3.Connection, pool: str, rows: list[tuple[int, str]]) -> int:
    """Embed a list of (child_rowid, content) pairs and insert into pilot_embeddings."""
    print(f"\n[{pool}] Embedding {len(rows):,} children in batches of {BATCH}...")
    t0 = time.time()
    inserted = 0
    cur = conn.cursor()
    # Skip already-embedded
    existing = {r[0] for r in cur.execute(
        "SELECT child_id FROM pilot_embeddings WHERE pool = ?", (pool,)
    )}
    pending = [(cid, c) for cid, c in rows if cid not in existing]
    if not pending:
        print(f"  [{pool}] all {len(rows):,} already embedded, skipping")
        return 0

    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        texts = [c[1] for c in chunk]
        try:
            embs = embed_texts(texts)
        except Exception as e:
            print(f"  [{pool}] batch {i}: {e} — skipping")
            continue
        for (cid, _), vec in zip(chunk, embs):
            blob = vec.astype(np.float32).tobytes()
            cur.execute(
                "INSERT OR REPLACE INTO pilot_embeddings(child_id, pool, embedding) VALUES (?,?,?)",
                (int(cid), pool, blob),
            )
        inserted += len(chunk)
        if (i // BATCH + 1) % 20 == 0:
            conn.commit()
            rate = inserted / (time.time() - t0)
            eta = (len(pending) - inserted) / rate / 60 if rate > 0 else 0
            print(f"  [{pool}] {inserted:,}/{len(pending):,}  "
                  f"({rate:.0f}/s, ETA {eta:.1f} min)")
    conn.commit()
    print(f"[{pool}] done: {inserted:,} new embeddings in {(time.time()-t0)/60:.1f} min")
    return inserted


# -----------------------------------------------------------------------------
# Eval
# -----------------------------------------------------------------------------

def load_baseline(conn: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load all child_embeddings + map child_id to parent_id.

    Historical quirk: child_embeddings.child_id refers to child_chunks.id
    (the integer column), not rowid. For older embeddings id == rowid, but
    rag_eval.py and training_samples both use the id-based join.
    """
    print("\nLoading baseline child_embeddings...")
    t0 = time.time()
    rows = conn.execute("""
        SELECT ce.child_id, ce.embedding, cc.parent_id
        FROM child_embeddings ce
        JOIN child_chunks cc ON cc.id = ce.child_id
    """).fetchall()
    n = len(rows)
    child_ids  = np.empty(n, dtype=np.int64)
    parent_ids = np.empty(n, dtype=np.int64)
    embs       = np.empty((n, EMBED_DIM), dtype=np.float32)
    for i, (cid, blob, pid) in enumerate(rows):
        child_ids[i]  = cid
        parent_ids[i] = pid if pid is not None else -1
        embs[i]       = np.frombuffer(blob, dtype=np.float32)
    # L2 normalize
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs /= norms
    print(f"  baseline: {n:,} embs  ({embs.nbytes/1024**3:.2f} GB)  "
          f"loaded in {time.time()-t0:.1f}s")
    return child_ids, parent_ids, embs


def load_pilot_pool(conn: sqlite3.Connection, pool: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = conn.execute("""
        SELECT pe.child_id, pe.embedding, cc.parent_id
        FROM pilot_embeddings pe
        JOIN child_chunks cc ON cc.rowid = pe.child_id
        WHERE pe.pool = ?
    """, (pool,)).fetchall()
    n = len(rows)
    if n == 0:
        return (np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.int64),
                np.empty((0, EMBED_DIM), dtype=np.float32))
    cids = np.empty(n, dtype=np.int64)
    pids = np.empty(n, dtype=np.int64)
    embs = np.empty((n, EMBED_DIM), dtype=np.float32)
    for i, (cid, blob, pid) in enumerate(rows):
        cids[i] = cid
        pids[i] = pid if pid is not None else -1
        embs[i] = np.frombuffer(blob, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs /= norms
    print(f"  pool [{pool}]: {n:,} embs")
    return cids, pids, embs


def eval_pool(val_queries: list[tuple[str, int]], pool_embs: np.ndarray,
              pool_pids: np.ndarray, label: str, k_list=(1, 5, 10)) -> dict:
    print(f"\n== {label} ({len(pool_embs):,} children in pool) ==")
    ks = list(k_list)
    max_k = max(ks)

    r_at_k = {k: 0 for k in ks}
    mrr = 0.0
    t0 = time.time()

    # Batch-embed queries
    print(f"  embedding {len(val_queries)} queries...")
    q_texts = [q for q, _ in val_queries]
    q_embs = []
    for i in range(0, len(q_texts), BATCH):
        q_embs.append(embed_texts(q_texts[i:i + BATCH], with_prefix=True))
    q_embs = np.concatenate(q_embs, axis=0)

    print(f"  scoring vs {len(pool_embs):,} passages...")
    # Matrix multiply: (nq, D) @ (D, N) = (nq, N)  — but N can be huge, so chunk
    CHUNK = 50_000
    for qi, (_, gold_pid) in enumerate(val_queries):
        q = q_embs[qi]
        best_idx = np.empty(max_k, dtype=np.int64)
        best_sim = np.full(max_k, -np.inf, dtype=np.float32)
        for s in range(0, len(pool_embs), CHUNK):
            e = pool_embs[s:s + CHUNK]
            sims = e @ q
            if len(sims) >= max_k:
                top = np.argpartition(-sims, max_k)[:max_k]
            else:
                top = np.arange(len(sims))
            # Merge with running top-k
            cand_idx = np.concatenate([best_idx, top + s])
            cand_sim = np.concatenate([best_sim, sims[top] if len(sims) >= max_k else sims])
            order = np.argsort(-cand_sim)[:max_k]
            best_idx = cand_idx[order]
            best_sim = cand_sim[order]
        # Deduplicate by parent_id, keeping first occurrence
        seen = set(); unique_pids = []
        for idx in best_idx:
            p = int(pool_pids[idx])
            if p not in seen:
                seen.add(p); unique_pids.append(p)
        # Metrics
        hit_rank = None
        for r, pid in enumerate(unique_pids):
            if pid == gold_pid:
                hit_rank = r + 1
                break
        if hit_rank is not None:
            for k in ks:
                if hit_rank <= k:
                    r_at_k[k] += 1
            mrr += 1.0 / hit_rank

    n = len(val_queries)
    metrics = {f"R@{k}": r_at_k[k] / n for k in ks}
    metrics["MRR"] = mrr / n
    metrics["n_queries"] = n
    metrics["pool_size"] = int(len(pool_embs))
    metrics["elapsed_s"] = round(time.time() - t0, 1)
    for m, v in metrics.items():
        print(f"  {m:>12s}  {v if isinstance(v, int) else f'{v:.4f}' if isinstance(v, float) else v}")
    return metrics


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def load_val(n: int, seed: int = 42) -> list[tuple[str, int]]:
    rows = []
    with open(VAL) as f:
        for line in f:
            r = json.loads(line)
            user = next((m["content"] for m in r["messages"] if m["role"] == "user"), None)
            if user and r.get("parent_id"):
                rows.append((user, r["parent_id"]))
    random.seed(seed); random.shuffle(rows)
    return rows[:n]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=VAL_N_DEFAULT)
    p.add_argument("--pool-size", type=int, default=POOL_SIZE)
    p.add_argument("--skip-embed", action="store_true",
                   help="Skip embedding step; eval only on what's already in pilot_embeddings")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    ensure_pilot_table(conn)

    if not args.skip_embed:
        signal_types = ["caselaw", "legislation", "contracts"]
        noise_types  = ["legal-mc4"]
        sig = sample_pool_child_ids(conn, signal_types, args.pool_size, seed=42)
        nos = sample_pool_child_ids(conn, noise_types,  args.pool_size, seed=43)
        print(f"Sampled signal={len(sig):,}  noise={len(nos):,}")
        embed_pool(conn, "signal", sig)
        embed_pool(conn, "noise",  nos)

    val_queries = load_val(args.n)
    print(f"\nLoaded {len(val_queries)} val queries")

    b_cids, b_pids, b_embs = load_baseline(conn)
    s_cids, s_pids, s_embs = load_pilot_pool(conn, "signal")
    n_cids, n_pids, n_embs = load_pilot_pool(conn, "noise")

    results = {}

    # Pool A: baseline
    results["A_baseline"] = eval_pool(
        val_queries, b_embs, b_pids, "A: baseline only")

    # Pool B: baseline + signal
    if len(s_embs):
        results["B_plus_signal"] = eval_pool(
            val_queries,
            np.concatenate([b_embs, s_embs]),
            np.concatenate([b_pids, s_pids]),
            "B: baseline + mlp signal (caselaw+legislation+contracts)")

    # Pool C: baseline + noise
    if len(n_embs):
        results["C_plus_noise"] = eval_pool(
            val_queries,
            np.concatenate([b_embs, n_embs]),
            np.concatenate([b_pids, n_pids]),
            "C: baseline + mlp noise (legal-mc4)")

    # Summary
    print("\n" + "=" * 70)
    print(f"{'pool':<28s}  {'n':>6s}  {'R@1':>6s}  {'R@5':>6s}  {'R@10':>6s}  {'MRR':>6s}  pool_size")
    print("-" * 80)
    for label, m in results.items():
        print(f"{label:<28s}  {m['n_queries']:>6}  "
              f"{m['R@1']:>6.3f}  {m['R@5']:>6.3f}  {m['R@10']:>6.3f}  "
              f"{m['MRR']:>6.3f}  {m['pool_size']:,}")

    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"\nResults -> {OUT_JSON}")


if __name__ == "__main__":
    main()
