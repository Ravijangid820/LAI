"""Production-fidelity retrieval recall harness for val.jsonl.

Replaces the in-RAM ``lai.search.eval.Corpus`` harness which OOMs on the
35.7M-child pgvector corpus. Runs val.jsonl questions through the SAME
indexes serve_rag uses in production (pgvector HNSW dense + SQLite FTS5
BM25 + RRF fusion), so reported Recall@K mirrors what users see.

Modes
-----
``--mode dense``   pgvector HNSW cosine top-K (no BM25, no fusion)
``--mode bm25``    SQLite FTS5 ``MATCH … ORDER BY bm25()`` only
``--mode hybrid``  RRF fusion of dense + BM25 — production semantics

Metrics
-------
For each requested K (``--k 10,30,100``) the harness reports:

* ``recall_at_k`` — fraction of questions whose gold ``parent_id`` is in
  the top-K *parents* of the retrieved children (multi-child hits to the
  same parent are deduped to first occurrence).
* ``mrr`` — mean reciprocal rank of the gold parent in the de-duped
  parent ranking, capped at the largest requested K.
* ``n`` — count of questions actually scored after filtering rows whose
  gold ``parent_id`` is not present in the live corpus (a stale val row
  can't be the harness's fault).

Caching
-------
Query embeddings are cached to ``--cache-dir`` keyed on the question
sha256. Re-runs with the same val rows skip the embedding service
entirely. The cache is just a JSON sidecar of (sha → fp32 list); a stale
cache is detected by sha256 mismatch and silently recomputed.

Usage
-----
::

    python -m scripts.eval.retrieval_recall \\
        --mode hybrid --n 200 --k 10,30,100 \\
        --output scripts/eval/rag_eval_results/hybrid_200.json

    # HNSW recall knob — higher = better recall, slower:
    python -m scripts.eval.retrieval_recall --mode dense --n 200 --ef-search 200
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

# Project-local imports go through the same modules serve_rag uses so
# eval semantics match production exactly. Failures here usually mean
# the embedding service is down or the DB env vars are unset.
from lai.common.retrieval import RetrievalClient
from lai.search.eval import (
    embed_query,
    retrieve_bm25_ids,
    rrf_fuse,
)

# Project layout — scripts/eval/retrieval_recall.py → parents[2] is LAI/
LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_VAL = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
DEFAULT_DB = LAI_DIR / "processed" / "pipeline_local.db"
DEFAULT_OUT_DIR = LAI_DIR / "scripts" / "eval" / "rag_eval_results"
DEFAULT_CACHE_DIR = LAI_DIR / "scripts" / "eval" / "_embed_cache"

Mode = Literal["dense", "bm25", "hybrid"]


# ─────────────────────────────────────────────────────────────────────────────
# val.jsonl
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ValRow:
    """One scored val.jsonl row in the shape the harness uses."""

    question: str
    gold_parent_id: int
    domain: str
    task_type: str


def load_val_rows(path: Path, n: int) -> list[ValRow]:
    """Read the first ``n`` rag-style rows from val.jsonl.

    Filters rows that lack a ``parent_id`` (not all val rows are RAG)
    or whose ``messages`` schema is unexpected; their absence is a data
    quality issue, not a harness bug, so we skip silently and log a
    summary count.
    """
    rows: list[ValRow] = []
    skipped = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if len(rows) >= n:
                break
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            pid = d.get("parent_id")
            messages = d.get("messages") or []
            # The val rows we care about have a user turn at index 1
            # (messages[0] is the system prompt, [2] is the gold answer).
            if pid is None or len(messages) < 2:
                skipped += 1
                continue
            user = messages[1]
            content = (user.get("content") if isinstance(user, dict) else None) or ""
            content = content.strip()
            if not content:
                skipped += 1
                continue
            rows.append(
                ValRow(
                    question=content,
                    gold_parent_id=int(pid),
                    domain=str(d.get("domain") or ""),
                    task_type=str(d.get("task_type") or ""),
                )
            )
    if skipped:
        print(f"  [val] skipped {skipped} unscorable rows (no parent_id or messages)")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Embedding cache (sha256(question) → fp32 list)
# ─────────────────────────────────────────────────────────────────────────────


def _q_sha(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


class EmbedCache:
    """JSON-on-disk cache so re-runs skip the embedding service entirely.

    One file per cache dir; round-trips through numpy at the boundary so
    callers get np.ndarray and the disk format is plain JSON.
    """

    def __init__(self, cache_dir: Path, prefix: bool):
        cache_dir.mkdir(parents=True, exist_ok=True)
        # The query prefix changes the produced vector, so cache files
        # are separated by prefix-on/off to avoid contaminating one with
        # the other when the same question is asked under both modes.
        name = "query_embeddings__prefix.json" if prefix else "query_embeddings__raw.json"
        self.path = cache_dir / name
        self._cache: dict[str, list[float]] = {}
        self._dirty = False
        if self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                # Corrupt cache: throw it away rather than block the
                # whole eval. The harness can always re-derive.
                print(f"  [cache] {self.path.name} corrupt — discarding")
                self._cache = {}

    def get_or_embed(self, question: str, *, with_prefix: bool) -> np.ndarray:
        key = _q_sha(question)
        cached = self._cache.get(key)
        if cached is not None:
            return np.asarray(cached, dtype=np.float32)
        vec = embed_query(question, with_prefix=with_prefix)
        self._cache[key] = vec.astype(np.float32).tolist()
        self._dirty = True
        return vec

    def flush(self) -> None:
        if not self._dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache))
        os.replace(tmp, self.path)
        self._dirty = False


# ─────────────────────────────────────────────────────────────────────────────
# Per-mode retrieval — each returns an ordered list of child_ids
# ─────────────────────────────────────────────────────────────────────────────


def _dense_child_ids(
    client: RetrievalClient, qvec: np.ndarray, *, k: int, ef_search: int | None
) -> list[int]:
    hits = client.dense_search(qvec, top_k=k, ef_search=ef_search)
    return [h.child_id for h in hits]


def _bm25_child_ids(conn: sqlite3.Connection, question: str, *, k: int) -> list[int]:
    return [cid for cid, _score in retrieve_bm25_ids(question, conn, k)]


def _hybrid_child_ids(
    client: RetrievalClient,
    conn: sqlite3.Connection,
    qvec: np.ndarray,
    question: str,
    *,
    candidate_k: int,
    ef_search: int | None,
) -> list[int]:
    dense = _dense_child_ids(client, qvec, k=candidate_k, ef_search=ef_search)
    bm25 = _bm25_child_ids(conn, question, k=candidate_k)
    return [cid for cid, _ in rrf_fuse([dense, bm25])[:candidate_k]]


# ─────────────────────────────────────────────────────────────────────────────
# Child → parent resolution (single batched fetch per query)
# ─────────────────────────────────────────────────────────────────────────────


def _hydrate_parents(client: RetrievalClient, child_ids: list[int]) -> list[int]:
    """Resolve child_ids → ordered parent_ids, deduped to first occurrence.

    fetch_children_by_id returns a dict, so we re-key in the original
    child order. Children with no resolvable parent (stale FTS5 rowid)
    are dropped; a missing parent legitimately can't appear in the
    parent ranking.
    """
    if not child_ids:
        return []
    by_child = client.fetch_children_by_id(child_ids)
    seen: set[int] = set()
    parents: list[int] = []
    for cid in child_ids:
        chunk = by_child.get(cid)
        if chunk is None or chunk.parent_id in seen:
            continue
        seen.add(chunk.parent_id)
        parents.append(chunk.parent_id)
    return parents


# ─────────────────────────────────────────────────────────────────────────────
# Metric aggregation
# ─────────────────────────────────────────────────────────────────────────────


def _rank_of_gold(parents: list[int], gold: int) -> int | None:
    """1-indexed rank of ``gold`` in ``parents``; None if absent."""
    try:
        return parents.index(gold) + 1
    except ValueError:
        return None


def _summarise(rank_list: list[int | None], ks: list[int]) -> dict[str, Any]:
    """Recall@K (multi-K) + MRR over the scored ranks."""
    n = len(rank_list)
    if n == 0:
        return {"n": 0, "recall_at_k": {str(k): 0.0 for k in ks}, "mrr": 0.0}
    recall = {
        str(k): sum(1 for r in rank_list if r is not None and r <= k) / n for k in ks
    }
    mrr = sum((1.0 / r) for r in rank_list if r is not None) / n
    return {"n": n, "recall_at_k": recall, "mrr": mrr}


# ─────────────────────────────────────────────────────────────────────────────
# Gold-id filtering — drop val rows whose gold isn't in the live corpus
# ─────────────────────────────────────────────────────────────────────────────


def _filter_to_live_gold(client: RetrievalClient, rows: list[ValRow]) -> list[ValRow]:
    """Drop rows whose gold parent_id isn't in ``corpus_parent_chunks``.

    A stale val row (gold was deleted, re-migrated to a different id, or
    never made the cut) is a data issue and would unfairly hurt every
    mode's Recall@K. We do this as one batched ``fetch_parent_texts``
    query so it's a single round-trip instead of N.
    """
    unique = sorted({r.gold_parent_id for r in rows})
    found = client.fetch_parent_texts(unique)
    alive = set(found.keys())
    kept = [r for r in rows if r.gold_parent_id in alive]
    dropped = len(rows) - len(kept)
    if dropped:
        print(f"  [val] dropped {dropped} rows with stale gold parent_id not in live corpus")
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────


def _progress(it: Iterable[ValRow], total: int) -> Iterator[tuple[int, ValRow]]:
    """tty-friendly progress bar without tqdm (stdlib-only by design)."""
    t0 = time.monotonic()
    for i, row in enumerate(it, start=1):
        if i == 1 or i == total or i % 25 == 0:
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed else 0.0
            eta = (total - i) / rate if rate else 0.0
            sys.stderr.write(
                f"\r  [eval] {i}/{total}  ({rate:.1f} q/s, ETA {eta:.0f}s)"
            )
            sys.stderr.flush()
        yield i, row
    sys.stderr.write("\n")


def run_eval(
    rows: list[ValRow],
    mode: Mode,
    ks: list[int],
    candidate_k: int,
    ef_search: int | None,
    cache_dir: Path,
    db_path: Path,
) -> dict[str, Any]:
    """Drive the harness — embed → retrieve → score → summarise."""
    max_k = max(ks)
    if candidate_k < max_k:
        # Otherwise the gold can sit just outside the candidate pool and
        # Recall@K would be artificially capped at Recall@candidate_k.
        candidate_k = max_k

    cache = EmbedCache(cache_dir, prefix=True)

    print(f"  [setup] mode={mode} n={len(rows)} ks={ks} candidate_k={candidate_k}", flush=True)
    if ef_search is not None:
        print(f"  [setup] hnsw.ef_search={ef_search} (override)", flush=True)

    bm25_conn: sqlite3.Connection | None = None
    if mode in ("bm25", "hybrid"):
        # Read-only — the FTS5 index is small but the parent SQLite is
        # ~800 GB on this box, so we never write or pull large rows.
        bm25_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    client = RetrievalClient()
    rows = _filter_to_live_gold(client, rows)
    if not rows:
        return {"mode": mode, "summary": _summarise([], ks)}

    rank_list: list[int | None] = []
    per_row_records: list[dict[str, Any]] = []

    t_embed_total = 0.0
    t_retrieve_total = 0.0
    t_hydrate_total = 0.0

    try:
        for _i, row in _progress(rows, total=len(rows)):
            # ── Embed (cached) ──────────────────────────────────────────
            t0 = time.perf_counter()
            if mode in ("dense", "hybrid"):
                qvec = cache.get_or_embed(row.question, with_prefix=True)
            else:
                qvec = np.zeros(0, dtype=np.float32)
            t_embed_total += time.perf_counter() - t0

            # ── Retrieve (per mode) ─────────────────────────────────────
            t0 = time.perf_counter()
            if mode == "dense":
                child_ids = _dense_child_ids(
                    client, qvec, k=candidate_k, ef_search=ef_search
                )
            elif mode == "bm25":
                assert bm25_conn is not None
                child_ids = _bm25_child_ids(bm25_conn, row.question, k=candidate_k)
            else:  # hybrid
                assert bm25_conn is not None
                child_ids = _hybrid_child_ids(
                    client,
                    bm25_conn,
                    qvec,
                    row.question,
                    candidate_k=candidate_k,
                    ef_search=ef_search,
                )
            t_retrieve_total += time.perf_counter() - t0

            # ── Child → parent → rank gold ──────────────────────────────
            t0 = time.perf_counter()
            parents = _hydrate_parents(client, child_ids)
            t_hydrate_total += time.perf_counter() - t0

            rank = _rank_of_gold(parents, row.gold_parent_id)
            rank_list.append(rank)
            per_row_records.append(
                {
                    "gold_parent_id": row.gold_parent_id,
                    "domain": row.domain,
                    "task_type": row.task_type,
                    "rank": rank,
                    "n_parents_returned": len(parents),
                }
            )
    finally:
        cache.flush()
        if bm25_conn is not None:
            bm25_conn.close()
        client.close()

    summary = _summarise(rank_list, ks)
    n = max(summary["n"], 1)
    return {
        "mode": mode,
        "candidate_k": candidate_k,
        "ef_search": ef_search,
        "summary": summary,
        "timings_ms_per_query": {
            "embed": 1000 * t_embed_total / n,
            "retrieve": 1000 * t_retrieve_total / n,
            "hydrate": 1000 * t_hydrate_total / n,
        },
        "per_row": per_row_records,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _parse_ks(spec: str) -> list[int]:
    return sorted({int(s.strip()) for s in spec.split(",") if s.strip()})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Production-fidelity Recall@K + MRR over val.jsonl."
    )
    ap.add_argument("--mode", choices=("dense", "bm25", "hybrid"), default="hybrid")
    ap.add_argument("--n", type=int, default=200, help="val.jsonl rows to score")
    ap.add_argument("--k", default="10,30,100", help="comma-sep K values for Recall@K")
    ap.add_argument(
        "--candidate-k",
        type=int,
        default=200,
        help="retrieval pool size before scoring (bumped to max(--k) if smaller)",
    )
    ap.add_argument(
        "--ef-search",
        type=int,
        default=None,
        help="pgvector hnsw.ef_search override; higher = better recall, slower",
    )
    ap.add_argument("--val", type=Path, default=DEFAULT_VAL)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--output", type=Path, default=None, help="JSON output path")
    ap.add_argument(
        "--per-row-csv",
        type=Path,
        default=None,
        help="optional CSV with one row per query (gold, rank, domain, …)",
    )
    args = ap.parse_args(argv)

    ks = _parse_ks(args.k)
    if not ks:
        print("--k must list at least one integer", file=sys.stderr)
        return 2

    print(f"[load] {args.val}", flush=True)
    rows = load_val_rows(args.val, args.n)
    if not rows:
        print("no scorable val rows; aborting", file=sys.stderr)
        return 2
    print(f"  [val] loaded {len(rows)} rows", flush=True)

    if args.mode in ("bm25", "hybrid") and not args.db.exists():
        print(f"sqlite DB not found at {args.db}", file=sys.stderr)
        return 2

    result = run_eval(
        rows=rows,
        mode=args.mode,
        ks=ks,
        candidate_k=args.candidate_k,
        ef_search=args.ef_search,
        cache_dir=args.cache_dir,
        db_path=args.db,
    )

    # ── Console summary ─────────────────────────────────────────────────
    s = result["summary"]
    print()
    print(f"=== {args.mode} (n={s['n']}, candidate_k={result['candidate_k']}) ===")
    for k_str in sorted(s["recall_at_k"], key=int):
        print(f"  Recall@{k_str:<4} {s['recall_at_k'][k_str]:.3f}")
    print(f"  MRR        {s['mrr']:.3f}")
    t = result["timings_ms_per_query"]
    print(
        f"  per-query  embed={t['embed']:.0f}ms retrieve={t['retrieve']:.0f}ms hydrate={t['hydrate']:.0f}ms"
    )

    # ── Optional JSON + per-row CSV ─────────────────────────────────────
    out = args.output
    if out is None:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = DEFAULT_OUT_DIR / f"recall_{args.mode}_n{len(rows)}.json"
    payload = {k: v for k, v in result.items() if k != "per_row"}
    payload["meta"] = {
        "n_requested": args.n,
        "n_scored": s["n"],
        "ks": ks,
        "val_path": str(args.val),
        "db_path": str(args.db),
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"  → wrote {out}")

    if args.per_row_csv is not None:
        with args.per_row_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["gold_parent_id", "domain", "task_type", "rank", "n_parents_returned"],
            )
            w.writeheader()
            w.writerows(result["per_row"])
        print(f"  → wrote {args.per_row_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
