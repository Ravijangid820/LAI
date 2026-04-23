"""
Quality audit of the synthetic training data in training_samples.

For every sample we already know which parent_chunk it was generated from
(Step 5 always gave the 72B teacher exactly one chunk as context). So the
ground truth is: **everything factual in the answer must be reconstructible
from that chunk**.

Two checks:

1. **Citation grounding** — for every § reference, clause, court-decision
   citation, or explicit Klausel/Nummer reference in the answer, confirm
   the same identifier exists in the parent chunk text.

2. **Answer-in-chunk grounding** — what fraction of the answer's content
   words / named entities appear in the parent chunk? Very low overlap
   means the model free-associated beyond the source.

We aggregate by task_type so we can see where the teacher is strong vs
weak. For example `summarize` and `explain` legitimately paraphrase, so
their per-token overlap is naturally lower than `rag_qa` or `extract`.

Usage:
    python scripts/audit_training_data.py
    python scripts/audit_training_data.py --sample 5000    # faster
    python scripts/audit_training_data.py --dump-worst 30  # show worst samples
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

LAI_DIR = Path(__file__).resolve().parents[1]
DB = LAI_DIR / "processed" / "pipeline_local.db"


# --------------------------------------------------------------------------
# Regex patterns for legal references
# --------------------------------------------------------------------------

# German legal references — deliberately narrow so we catch real citations,
# not every integer in the text. False positives on match, not on miss,
# is a bigger quality-audit sin.
PATTERNS = {
    # § 14, §§ 14–18, § 14 Abs. 2, § 14 Absatz 2 Satz 1
    "paragraph": re.compile(r"§{1,2}\s*(\d+[a-z]?)(\s*Abs[a-z.]*\s*\d+)?(\s*Satz\s*\d+)?", re.I),
    # Article references (EU law, international): Art. 5 Abs. 2 | Artikel 12
    "article":   re.compile(r"\bArt(?:ikel|\.)\s*(\d+[a-z]?)", re.I),
    # Court decisions: BGH, OVG, VG + case number (2 BvR 12/34, I ZR 123/45, 7 K 1234/20)
    "case_nr":   re.compile(r"\b(?:\d+\s+)?(?:[A-Z]{1,4}\s*)?(?:[A-Za-z]+)\s+\d+/\d{2,4}\b"),
    # Clauses / numbered items: Klausel dd, Nr. 5, Ziffer 3.2
    "clause":    re.compile(r"(?:Klausel|Nr\.|Nummer|Ziffer|Ziff\.)\s+([A-Za-z]{1,3}|\d+(?:\.\d+)?)", re.I),
}


def extract_refs(text: str) -> dict[str, list[str]]:
    """Return a dict of reference kind -> normalized reference strings."""
    out: dict[str, list[str]] = defaultdict(list)
    if not text:
        return out
    # paragraph
    for m in PATTERNS["paragraph"].finditer(text):
        out["paragraph"].append(f"§ {m.group(1).strip()}")
    # article
    for m in PATTERNS["article"].finditer(text):
        out["article"].append(f"Art {m.group(1).strip()}")
    # clause / numbered
    for m in PATTERNS["clause"].finditer(text):
        out["clause"].append(m.group(1).strip())
    # NOTE: case_nr is too noisy in practice (triggers on dates and weights).
    # We leave it out of the audit to avoid false fabrication flags.
    return out


def _words(text: str) -> set[str]:
    """Normalize to word tokens of length >= 4 (skip stopwords, junk)."""
    # lowercase, split on non-letters, keep tokens with >= 4 chars and at least one letter
    return {
        w for w in re.split(r"[^A-Za-zäöüÄÖÜß\d]+", text.lower())
        if len(w) >= 4 and not w.isdigit()
    }


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def audit_sample(answer: str, chunk: str, task_type: str) -> dict:
    """Return an audit record for one training sample."""
    ans_refs  = extract_refs(answer)
    chunk_txt = chunk or ""
    chunk_norm = chunk_txt.lower()

    # 1. Citation grounding
    ref_total = 0
    ref_found = 0
    missing_refs: list[str] = []
    for kind, refs in ans_refs.items():
        for r in refs:
            ref_total += 1
            # Match a § 14 or § 14 against '§ 14' in the chunk (with a bit of slack)
            key = r.lower().replace(" ", "")
            chunk_key = chunk_norm.replace(" ", "")
            if key in chunk_key:
                ref_found += 1
            else:
                missing_refs.append(r)

    # 2. Word-overlap grounding — fraction of answer content words that
    # appear in the chunk. For paraphrase-heavy tasks (summarize, explain)
    # we still expect key terms to be reused.
    ans_words   = _words(answer or "")
    chunk_words = _words(chunk_txt)
    if ans_words:
        overlap = len(ans_words & chunk_words) / len(ans_words)
    else:
        overlap = 0.0

    return {
        "task_type":     task_type,
        "ref_total":     ref_total,
        "ref_found":     ref_found,
        "missing_refs":  missing_refs,
        "word_overlap":  round(overlap, 3),
        "ans_len":       len(answer or ""),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DB))
    p.add_argument("--sample", type=int, default=0,
                   help="Audit only this many random rows (0 = all 200K).")
    p.add_argument("--dump-worst", type=int, default=0,
                   help="Print N worst-grounded samples at the end.")
    p.add_argument("--out", default=str(LAI_DIR / "scripts" / "audit_results.json"))
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    # Pull all parent_chunks upfront — 134K rows, small
    parents = {pid: content for pid, content in conn.execute(
        "SELECT id, content FROM parent_chunks"
    )}
    print(f"Loaded {len(parents):,} parent chunks", file=sys.stderr)

    # Training samples — either all or a random subset
    limit_clause = "ORDER BY RANDOM() LIMIT ?" if args.sample > 0 else "ORDER BY rowid"
    params = (args.sample,) if args.sample > 0 else ()
    cur = conn.execute(
        f"SELECT parent_id, task_type, messages FROM training_samples {limit_clause}",
        params,
    )

    agg: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "ref_total": 0, "ref_found": 0,
        "overlap_sum": 0.0, "missing_ref_count": 0, "word_coverage_lt_20pct": 0,
    })
    worst = []  # (score, record) where lower score = worse
    missing_examples: list[dict] = []
    n = 0

    for parent_id, task_type, msgs_json in cur:
        n += 1
        if parent_id is None or parent_id not in parents:
            continue
        try:
            msgs = json.loads(msgs_json)
        except Exception:
            continue
        answer = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
        if not answer:
            continue

        rec = audit_sample(answer, parents[parent_id], task_type)

        a = agg[task_type]
        a["n"] += 1
        a["ref_total"] += rec["ref_total"]
        a["ref_found"] += rec["ref_found"]
        a["overlap_sum"] += rec["word_overlap"]
        a["missing_ref_count"] += len(rec["missing_refs"])
        if rec["word_overlap"] < 0.2 and task_type not in ("summarize", "explain"):
            a["word_coverage_lt_20pct"] += 1

        # Track worst-grounded (by smaller ref recall and smaller overlap)
        if rec["ref_total"] > 0 and rec["ref_found"] < rec["ref_total"]:
            missing_examples.append({
                "task_type":    task_type,
                "parent_id":    parent_id,
                "missing_refs": rec["missing_refs"],
                "answer":       answer[:400],
                "chunk":        parents[parent_id][:600],
            })

        if args.dump_worst:
            score = (rec["word_overlap"]
                     + (rec["ref_found"] / rec["ref_total"] if rec["ref_total"] else 1.0))
            worst.append((score, {
                "task_type": task_type, "parent_id": parent_id,
                "word_overlap": rec["word_overlap"],
                "ref_total": rec["ref_total"], "ref_found": rec["ref_found"],
                "answer": answer[:400],
                "chunk":  parents[parent_id][:400],
            }))

        if n % 20_000 == 0:
            print(f"  {n:,} processed...", file=sys.stderr)

    print(f"Done. {n:,} samples audited.\n", file=sys.stderr)

    # -------- Report --------
    print("=" * 78)
    print(f"QUALITY AUDIT — {n:,} samples")
    print("=" * 78)
    print(f"{'task_type':<15} {'n':>7} {'cites':>6} {'verif':>6} "
          f"{'cite_verif_pct':>14} {'avg_overlap':>12} {'low_overlap':>11}")
    totals = {"n": 0, "ref_total": 0, "ref_found": 0, "overlap_sum": 0.0, "low": 0}
    for task, a in sorted(agg.items()):
        cite_pct = (a["ref_found"] / a["ref_total"] * 100) if a["ref_total"] else 100.0
        avg_overlap = a["overlap_sum"] / a["n"] if a["n"] else 0
        low_pct = (a["word_coverage_lt_20pct"] / a["n"] * 100) if a["n"] else 0
        print(f"{task:<15} {a['n']:>7,} {a['ref_total']:>6,} {a['ref_found']:>6,} "
              f"{cite_pct:>13.1f}% {avg_overlap:>12.1%} {low_pct:>10.1f}%")
        for k in ("n", "ref_total", "ref_found", "overlap_sum"):
            totals[k] += a[k]
        totals["low"] += a["word_coverage_lt_20pct"]

    total_cite_pct = (totals["ref_found"] / totals["ref_total"] * 100) if totals["ref_total"] else 100
    total_overlap  = totals["overlap_sum"] / totals["n"] if totals["n"] else 0
    total_low      = (totals["low"] / totals["n"] * 100) if totals["n"] else 0
    print("-" * 78)
    print(f"{'TOTAL':<15} {totals['n']:>7,} {totals['ref_total']:>6,} {totals['ref_found']:>6,} "
          f"{total_cite_pct:>13.1f}% {total_overlap:>12.1%} {total_low:>10.1f}%")

    print()
    print("Columns:")
    print("  cites          = total legal references (§, Art, Klausel/Nr) found in answers")
    print("  verif          = those that also appear in the parent chunk")
    print("  cite_verif_pct = verif / cites — ideally > 95%")
    print("  avg_overlap    = fraction of answer content-words present in chunk")
    print("  low_overlap    = % of non-paraphrase samples with overlap < 20%")

    # Save structured results
    out = {
        "n": totals["n"],
        "totals": {
            "citations": totals["ref_total"],
            "verifiable": totals["ref_found"],
            "cite_verif_pct": round(total_cite_pct, 2),
            "avg_word_overlap": round(total_overlap, 3),
            "low_overlap_pct": round(total_low, 2),
        },
        "per_task": {t: {
            "n": a["n"],
            "citations": a["ref_total"],
            "verifiable": a["ref_found"],
            "cite_verif_pct": round((a["ref_found"] / a["ref_total"] * 100) if a["ref_total"] else 100, 2),
            "avg_word_overlap": round(a["overlap_sum"] / a["n"] if a["n"] else 0, 3),
            "low_overlap_pct": round((a["word_coverage_lt_20pct"] / a["n"] * 100) if a["n"] else 0, 2),
        } for t, a in agg.items()},
        "missing_ref_examples": missing_examples[:50],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nSaved detailed results to {args.out}")

    # Optional: dump the N worst-grounded samples
    if args.dump_worst:
        worst.sort(key=lambda x: x[0])
        print()
        print(f"=== {args.dump_worst} worst-grounded samples ===")
        for score, rec in worst[:args.dump_worst]:
            print(f"\n[{rec['task_type']}  overlap={rec['word_overlap']:.2f}  "
                  f"refs={rec['ref_found']}/{rec['ref_total']}  parent={rec['parent_id']}]")
            print(f"  answer: {rec['answer'][:200]}...")
            print(f"  chunk:  {rec['chunk'][:200]}...")


if __name__ == "__main__":
    main()
