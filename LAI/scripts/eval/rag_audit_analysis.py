"""
Analyze retrieval eval results to understand WHERE retrieval fails.

Categorizes each query by features (specificity, named-entity content,
numeric/clause references, question length) and reports recall by category.
This tells us whether the remaining failures are "generic questions that
could match many docs" vs "specific questions where retrieval got it wrong."

Usage:
    python scripts/eval/rag_audit_analysis.py scripts/eval/rag_eval_results/hybrid_rerank_n500.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

LAI_DIR = Path(__file__).resolve().parents[2]
DB = LAI_DIR / "processed" / "pipeline_local.db"


# ---------------------------------------------------------------------------
# Query feature extraction
# ---------------------------------------------------------------------------

# Named-entity indicators in German legal / wind-park text
WIND_PARK_RX = re.compile(
    r"\b(?:Windpark|WP|WEA|Windenergieanlage)\s+[A-ZÄÖÜ][\w\s-]{2,40}\b"
)
COURT_RX = re.compile(r"\b(?:BGH|BVerfG|BVerwG|OVG|VG|OLG|LG|AG|BFH|BAG|BSG|EuGH|EGMR)\b")
COMPANY_RX = re.compile(r"\b[A-ZÄÖÜ][\w-]{2,}\s*(?:GmbH|AG|KG|SE|OHG|e\.V\.|Co\.?\s*KG)\b")
PARAGRAPH_RX = re.compile(r"§{1,2}\s*\d+", re.I)
ARTICLE_RX = re.compile(r"\bArt(?:ikel|\.)\s*\d+", re.I)
CLAUSE_RX = re.compile(r"(?:Klausel|Nr\.|Nummer|Ziffer)\s+\w+", re.I)
DATE_RX = re.compile(r"\b(?:19|20)\d{2}\b")
CASE_ID_RX = re.compile(r"\b[A-Z]{1,4}\s+\d+/\d{2,4}\b")


def query_features(question: str) -> dict:
    feats = {
        "length":          len(question),
        "word_count":      len(question.split()),
        "has_wind_park":   bool(WIND_PARK_RX.search(question)),
        "has_court":       bool(COURT_RX.search(question)),
        "has_company":     bool(COMPANY_RX.search(question)),
        "has_paragraph":   bool(PARAGRAPH_RX.search(question)),
        "has_article":     bool(ARTICLE_RX.search(question)),
        "has_clause":      bool(CLAUSE_RX.search(question)),
        "has_date":        bool(DATE_RX.search(question)),
        "has_case_id":     bool(CASE_ID_RX.search(question)),
    }
    feats["is_specific"] = any(
        feats[k] for k in
        ("has_wind_park", "has_court", "has_company", "has_paragraph",
         "has_article", "has_case_id", "has_clause")
    )
    feats["is_generic"] = not feats["is_specific"]
    return feats


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def bucket_stats(rows: list[dict], key_fn) -> dict[str, dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    out = {}
    for g, rs in groups.items():
        n = len(rs)
        if n == 0:
            continue
        found = [r for r in rs if r.get("rank") is not None]
        r1 = sum(1 for r in rs if r.get("rank") == 1) / n
        r5 = sum(1 for r in rs if r.get("rank") and r["rank"] <= 5) / n
        r10 = sum(1 for r in rs if r.get("rank") and r["rank"] <= 10) / n
        mrr = sum((1/r["rank"]) if r.get("rank") else 0 for r in rs) / n
        out[g] = {
            "n":        n,
            "recall@1": round(r1, 3),
            "recall@5": round(r5, 3),
            "recall@10": round(r10, 3),
            "mrr":      round(mrr, 3),
        }
    return out


def print_table(title: str, stats: dict):
    print(f"\n=== {title} ===")
    print(f"{'group':<25s} {'n':>6s} {'R@1':>6s} {'R@5':>6s} {'R@10':>6s} {'MRR':>6s}")
    # Sort by sample size (largest first)
    for g, s in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
        label = str(g)[:24]
        print(f"{label:<25s} {s['n']:>6d} {s['recall@1']:>6.1%} "
              f"{s['recall@5']:>6.1%} {s['recall@10']:>6.1%} {s['mrr']:>6.3f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("results", help="Path to rag_eval output JSON (from lai.search.eval)")
    p.add_argument("--val-file",
                   default=str(LAI_DIR / "training/fine_tuning/data/val.jsonl"),
                   help="Needed to recover task_type per query")
    p.add_argument("--dump-failures", type=int, default=0,
                   help="Print N failure examples at the end")
    args = p.parse_args()

    result = json.loads(Path(args.results).read_text())
    per_q = result.get("per_query", [])
    print(f"Loaded {len(per_q):,} queries from {args.results}")
    print(f"Mode: {result.get('mode')}  |  overall metrics: {result.get('metrics')}")

    # Load val.jsonl to recover task_type (per_query only stores question/rank)
    val_by_q: dict[str, dict] = {}
    with open(args.val_file) as f:
        for line in f:
            r = json.loads(line)
            question = next((m["content"] for m in r["messages"] if m["role"] == "user"), None)
            if question:
                val_by_q[question] = r

    # Pull parent metadata so we can segment by source doc_type
    conn = sqlite3.connect(str(DB))
    parent_meta = {pid: {"doc_type": dt, "len": ln, "section": sec}
                   for pid, dt, ln, sec in conn.execute(
                       "SELECT id, doc_type, char_count, section FROM parent_chunks"
                   )}

    # Enrich rows with features
    rows = []
    for q in per_q:
        feats = query_features(q["question"])
        orig = val_by_q.get(q["question"], {})
        pmeta = parent_meta.get(q["gold_parent"], {})
        rows.append({
            **q,
            **feats,
            "task_type":      orig.get("task_type", "unknown"),
            "gold_doc_type":  pmeta.get("doc_type"),
            "gold_section":   pmeta.get("section"),
            "gold_chunk_len": pmeta.get("len"),
        })

    # ---- Slices ----
    print_table("By task_type",   bucket_stats(rows, lambda r: r["task_type"]))
    print_table("By specificity", bucket_stats(rows, lambda r: "specific" if r["is_specific"] else "generic"))
    print_table("By gold doc_type", bucket_stats(rows, lambda r: r["gold_doc_type"] or "?"))

    def q_len_bucket(r):
        L = r["word_count"]
        return "short (≤15)" if L <= 15 else "medium (16-25)" if L <= 25 else "long (>25)"
    print_table("By question length", bucket_stats(rows, q_len_bucket))

    def chunk_len_bucket(r):
        L = r.get("gold_chunk_len") or 0
        return "small (<1k)" if L < 1000 else "medium (1k-3k)" if L < 3000 else "large (>=3k)"
    print_table("By gold chunk size", bucket_stats(rows, chunk_len_bucket))

    # ---- Failure mode ----
    failures = [r for r in rows if r.get("rank") is None or r["rank"] > 5]
    print(f"\n=== Failure categorization (n={len(failures)}, R@5 miss) ===")
    f_cnt = Counter()
    for r in failures:
        if r["is_generic"]:         f_cnt["generic_query"] += 1
        if r["gold_chunk_len"] and r["gold_chunk_len"] < 1000: f_cnt["small_gold_chunk"] += 1
        if r["gold_chunk_len"] and r["gold_chunk_len"] > 4000: f_cnt["large_gold_chunk"] += 1
        if r["has_paragraph"]:      f_cnt["has_paragraph_ref"] += 1
        if r["has_case_id"]:        f_cnt["has_case_id"] += 1
        if r["word_count"] <= 10:   f_cnt["very_short_query"] += 1
    for cat, c in f_cnt.most_common():
        print(f"  {cat:<25s} {c:>5d} ({c/len(failures):>5.1%})")

    # ---- Example failures ----
    if args.dump_failures:
        print(f"\n=== {args.dump_failures} sample failures ===")
        for r in failures[:args.dump_failures]:
            print(f"\ntask={r['task_type']} rank={r.get('rank')} specific={r['is_specific']}")
            print(f"  Q: {r['question'][:150]}")
            print(f"  gold_parent={r['gold_parent']} doc_type={r.get('gold_doc_type')}")
            print(f"  got top-5: {r.get('top_parents', [])[:5]}")

    # Save enriched rows for further drill-down
    enriched_path = Path(args.results).with_suffix(".enriched.json")
    with open(enriched_path, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\nSaved enriched per-query data to {enriched_path}")


if __name__ == "__main__":
    main()
