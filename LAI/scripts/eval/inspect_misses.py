"""Spot-check val.jsonl misses against the retrieval pipeline's top-K.

For every row in a per-row CSV from ``scripts.eval.retrieval_recall``
whose ``rank`` is null or > a cutoff, fetch:

* the question (from val.jsonl by index — relies on the CSV being in
  val order, which it is by construction),
* the gold parent's text (from pgvector),
* the harness's top-K hybrid candidates' parent texts,

and emit a markdown report a human can skim in 60 seconds per row to
classify each miss as one of:

* ``gold_correct`` — model failed; gold is correct and relevant.
* ``gold_questionable`` — gold parent doesn't obviously answer the
  question; the model may be right to surface other rows.
* ``gold_unrelated`` — gold parent is on a totally different topic
  (likely a val.jsonl labelling error).

The point isn't to confirm the harness is wrong — it's to find out
what fraction of the both-miss tail is actually unfair grading
against bad labels. If 20 %+ of misses are ``gold_questionable`` or
``gold_unrelated``, the published 0.490 R@30 ceiling is a floor
artifact, not a model ceiling.

Usage
-----
::

    python -m scripts.eval.inspect_misses \\
        --per-row-csv scripts/eval/rag_eval_results/2026-06-02_baseline/recall_hybrid_n200_per_row.csv \\
        --val training/fine_tuning/data/val.jsonl \\
        --rank-cutoff 100 --n 20 \\
        --output scripts/eval/rag_eval_results/2026-06-02_miss_audit.md
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from lai.common.retrieval import RetrievalClient
from lai.search.eval import (
    embed_query,
    retrieve_bm25_ids,
    rrf_fuse,
)

LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_VAL = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"
DEFAULT_DB = LAI_DIR / "processed" / "pipeline_local.db"


def _load_val_questions(path: Path, n_rows: int) -> list[tuple[str, int, str]]:
    """Return ordered (question, gold_parent_id, domain) for the first
    ``n_rows`` scoreable rows. Order matches the harness's load_val_rows."""
    out: list[tuple[str, int, str]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if len(out) >= n_rows:
                break
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = d.get("parent_id")
            messages = d.get("messages") or []
            if pid is None or len(messages) < 2:
                continue
            user = messages[1]
            q = (user.get("content") or "").strip() if isinstance(user, dict) else ""
            if not q:
                continue
            out.append((q, int(pid), str(d.get("domain") or "")))
    return out


def _retrieve_topk_hybrid(
    client: RetrievalClient,
    conn: sqlite3.Connection,
    question: str,
    k: int = 5,
) -> list[tuple[int, str]]:
    """Return [(parent_id, text)] for top-K hybrid candidates, deduped to
    first-parent appearance, text trimmed to 600 chars."""
    qvec = embed_query(question, with_prefix=True)
    dense_hits = client.dense_search(qvec, top_k=200)
    dense_ranking = [h.child_id for h in dense_hits]
    bm25_pairs = retrieve_bm25_ids(question, conn, 200)
    bm25_ranking = [cid for cid, _ in bm25_pairs]
    fused = rrf_fuse([dense_ranking, bm25_ranking])[:200]
    cand_ids = [cid for cid, _ in fused]
    children = client.fetch_children_by_id(cand_ids)
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for cid in cand_ids:
        chunk = children.get(cid)
        if chunk is None or chunk.parent_id in seen:
            continue
        seen.add(chunk.parent_id)
        text = (chunk.content or "")[:600].replace("\n", " ")
        out.append((chunk.parent_id, text))
        if len(out) >= k:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-row-csv", type=Path, required=True)
    ap.add_argument("--val", type=Path, default=DEFAULT_VAL)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument(
        "--rank-cutoff",
        type=int,
        default=100,
        help="treat rank None or > cutoff as a 'miss' (default 100)",
    )
    ap.add_argument("--n", type=int, default=20, help="max misses to inspect")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"sqlite DB not found at {args.db}")
        return 2

    # Load CSV rows in order
    with args.per_row_csv.open("r", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))

    # Load val.jsonl questions in matching order
    val_rows = _load_val_questions(args.val, len(csv_rows))
    if len(val_rows) != len(csv_rows):
        print(
            f"WARN: csv rows ({len(csv_rows)}) != val rows ({len(val_rows)}) — "
            "row alignment may be off; spot-check the first row's question"
        )

    # Pick misses
    misses: list[tuple[int, dict, tuple[str, int, str]]] = []
    for i, (csv_row, val_row) in enumerate(zip(csv_rows, val_rows, strict=False)):
        rank_str = csv_row.get("rank") or ""
        rank = int(rank_str) if rank_str.isdigit() else None
        is_miss = rank is None or rank > args.rank_cutoff
        if is_miss:
            misses.append((i, csv_row, val_row))
        if len(misses) >= args.n:
            break
    if not misses:
        print("no misses found at this rank cutoff; try a smaller --rank-cutoff")
        return 0

    print(f"inspecting {len(misses)} misses (rank-cutoff={args.rank_cutoff})")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    client = RetrievalClient()

    out_lines: list[str] = [
        f"# Val.jsonl miss audit — {len(misses)} rows · rank-cutoff {args.rank_cutoff}",
        "",
        "For each miss: the question, the gold parent text, and the top-5",
        "hybrid candidates. Skim and tag each as:",
        "",
        "* `gold_correct` — gold is on-topic; the model genuinely missed it.",
        "* `gold_questionable` — gold sort of fits but the top-5 looks at least as relevant.",
        "* `gold_unrelated` — gold is on a totally different topic (likely a labelling error).",
        "",
        "If `gold_unrelated` + `gold_questionable` together exceed 20 % of",
        "the sample, the published Recall@K ceiling is a label-quality",
        "floor, not a model ceiling.",
        "",
        "---",
        "",
    ]

    try:
        # Batch-fetch all gold parent texts up front
        gold_pids = sorted({val_row[1] for _, _, val_row in misses})
        gold_texts = client.fetch_parent_texts(gold_pids)
        for n, (idx, csv_row, val_row) in enumerate(misses, start=1):
            question, gold_pid, domain = val_row
            rank = csv_row.get("rank") or "—"
            gold_text = (gold_texts.get(gold_pid) or "<NOT IN LIVE CORPUS>")[:600]
            gold_text = gold_text.replace("\n", " ")
            out_lines.append(f"## {n}. row {idx} · gold={gold_pid} · domain={domain} · rank={rank}")
            out_lines.append("")
            out_lines.append(f"**Question:** {question}")
            out_lines.append("")
            out_lines.append(f"**Gold parent {gold_pid}:** {gold_text}")
            out_lines.append("")
            out_lines.append("**Top-5 hybrid candidates:**")
            out_lines.append("")
            try:
                topk = _retrieve_topk_hybrid(client, conn, question, k=5)
            except Exception as exc:
                out_lines.append(f"_(retrieval failed: {exc})_")
                out_lines.append("")
                continue
            for rank_idx, (pid, text) in enumerate(topk, start=1):
                marker = " ⭐ (=gold)" if pid == gold_pid else ""
                out_lines.append(f"{rank_idx}. parent={pid}{marker}: {text}")
            out_lines.append("")
            out_lines.append("**Verdict:** TBD")
            out_lines.append("")
            out_lines.append("---")
            out_lines.append("")
    finally:
        conn.close()
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"→ wrote {args.output}")
    print(f"  ({sum(1 for line in out_lines if line.startswith('## '))} rows for review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
