"""
Audit corpus quality before committing to full embedding (Step 6).

Questions we want answered:
  1. Per-corpus: how long are docs? how many? how much overlap with other
     sources? what fraction is actually wind-energy / public-law / construction
     related vs irrelevant (criminal / family / tax law)?
  2. Within multilegalpile specifically: which raw_type buckets (caselaw,
     legislation, contracts, legal-mc4, other) are signal vs noise?
  3. Cross-corpus deduplication: does multilegalpile duplicate content we
     already have in hf_cases / openlegaldata / gerdalir?

Output: JSON + printed table. No GPU needed. Pure disk scan.

Usage:
    python scripts/audit_corpus_quality.py                  # sample-based
    python scripts/audit_corpus_quality.py --full-mlp       # every mlp file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

LAI_DIR   = Path(__file__).resolve().parents[1]
SEG_ROOT  = LAI_DIR / "data" / "lai-segments" / "legal_data"
OUT_JSON  = LAI_DIR / "scripts" / "corpus_audit_results.json"

SAMPLE_FILES = 120   # per corpus, stratified sample

# Wind-energy & public-law legal lexicon
DOMAIN_TERMS = {
    "wind_energy": [
        r"\bWindenergie\b", r"\bWindkraft", r"\bWindpark\b", r"\bWEA\b",
        r"\bEEG\b", r"\bErneuerbare[ -]?Energien", r"\bBürgerenergie",
    ],
    "construction_permit": [
        r"\bBauGB\b", r"\bBImSchG\b", r"\bBNatSchG\b", r"\bFlächennutzungsplan",
        r"\bBebauungsplan", r"\bBaugenehmigung", r"\bImmissionsschutz",
        r"\bAußenbereich", r"\bPrivilegier",
    ],
    "land_use": [
        r"\bGrundstück", r"\bPachtvertrag", r"\bDienstbarkeit", r"\bFlurstück",
        r"\bNutzungsvertrag", r"\bLandpacht",
    ],
    # Tell us how much of the corpus is off-topic for wind-DD
    "off_topic_criminal": [r"\bStGB\b", r"\bStrafrecht", r"\bStPO\b"],
    "off_topic_family":   [r"\bFamilienrecht", r"\bScheidung", r"\bUnterhalt"],
    "off_topic_tax":      [r"\bSteuerrecht", r"\bUStG\b", r"\bEStG\b", r"\bKStG\b"],
}
DOMAIN_PATTERNS = {k: re.compile("|".join(v), re.IGNORECASE) for k, v in DOMAIN_TERMS.items()}


def text_prefix_hash(text: str, n: int = 500) -> str:
    return hashlib.sha256(text[:n].encode("utf-8")).hexdigest()[:16]


def analyze_record(rec: dict) -> dict:
    """Extract per-doc audit features."""
    text = "\n\n".join(s["text"] for s in rec.get("segments", []))
    flags = {}
    for name, pat in DOMAIN_PATTERNS.items():
        flags[name] = bool(pat.search(text))
    return {
        "doc_id": rec.get("doc_id"),
        "raw_type": rec.get("metadata", {}).get("raw_type"),
        "source_corpus": rec.get("metadata", {}).get("source_corpus"),
        "jurisdiction": rec.get("metadata", {}).get("jurisdiction"),
        "doc_type": rec.get("doc_type"),
        "char_len": len(text),
        "prefix_hash": text_prefix_hash(text),
        "flags": flags,
    }


def scan_corpus(name: str, path: Path, sample_files: int,
                full: bool = False) -> tuple[list[dict], int]:
    if not path.exists():
        return [], 0
    files = sorted(path.glob("*.segments.jsonl"))
    total_files = len(files)
    if not full and len(files) > sample_files:
        random.seed(42)
        files = random.sample(files, sample_files)
    recs = []
    for fp in files:
        with open(fp) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    recs.append(analyze_record(r))
                except Exception:
                    pass
    return recs, total_files


def aggregate(name: str, recs: list[dict]) -> dict:
    if not recs:
        return {"corpus": name, "n": 0}
    lens = [r["char_len"] for r in recs]
    by_type = defaultdict(list)
    for r in recs:
        by_type[r["raw_type"] or "(none)"].append(r)

    flag_rates = {}
    for k in DOMAIN_PATTERNS:
        flag_rates[k] = sum(1 for r in recs if r["flags"][k]) / len(recs)

    # Dedup by text prefix hash within this corpus
    hashes = [r["prefix_hash"] for r in recs]
    unique_hashes = len(set(hashes))

    per_raw_type = {}
    for t, rs in by_type.items():
        lens_t = [r["char_len"] for r in rs]
        per_raw_type[t] = {
            "n": len(rs),
            "char_mean": int(statistics.mean(lens_t)),
            "char_median": int(statistics.median(lens_t)),
            "char_p90": int(statistics.quantiles(lens_t, n=10)[-1]) if len(lens_t) > 10 else max(lens_t),
            "flag_wind": sum(1 for r in rs if r["flags"]["wind_energy"]) / len(rs),
            "flag_construction": sum(1 for r in rs if r["flags"]["construction_permit"]) / len(rs),
            "flag_land": sum(1 for r in rs if r["flags"]["land_use"]) / len(rs),
            "flag_off_criminal": sum(1 for r in rs if r["flags"]["off_topic_criminal"]) / len(rs),
            "flag_off_family":   sum(1 for r in rs if r["flags"]["off_topic_family"]) / len(rs),
            "flag_off_tax":      sum(1 for r in rs if r["flags"]["off_topic_tax"]) / len(rs),
        }

    return {
        "corpus": name,
        "n_sampled": len(recs),
        "char_mean": int(statistics.mean(lens)),
        "char_median": int(statistics.median(lens)),
        "char_p90": int(statistics.quantiles(lens, n=10)[-1]) if len(lens) > 10 else max(lens),
        "char_max": max(lens),
        "internal_dedup_rate": 1 - unique_hashes / len(recs),
        "domain_flags": flag_rates,
        "per_raw_type": per_raw_type,
    }


def cross_dedup(corpus_recs: dict[str, list[dict]]) -> dict:
    """For each pair, what fraction of A's prefix hashes appear in B?"""
    hash_sets = {n: {r["prefix_hash"] for r in recs} for n, recs in corpus_recs.items()}
    out = {}
    for a in hash_sets:
        for b in hash_sets:
            if a == b:
                continue
            inter = len(hash_sets[a] & hash_sets[b])
            out[f"{a}_in_{b}"] = {
                "overlap": inter,
                "pct_of_{a}": round(100 * inter / max(len(hash_sets[a]), 1), 2),
            }
    return out


def print_table(results: list[dict]) -> None:
    print()
    print(f"{'corpus':<18s}  {'n':>8s}  {'char_med':>8s}  {'char_p90':>8s}  "
          f"{'wind%':>6s}  {'bau%':>6s}  {'land%':>6s}  {'crim%':>6s}  "
          f"{'tax%':>6s}  {'dedup%':>7s}")
    print("-" * 110)
    for r in results:
        if r.get("n_sampled", 0) == 0:
            print(f"{r['corpus']:<18s}  (empty)")
            continue
        f = r["domain_flags"]
        print(f"{r['corpus']:<18s}  {r['n_sampled']:>8,}  {r['char_median']:>8,}  "
              f"{r['char_p90']:>8,}  {f['wind_energy']*100:>6.2f}  "
              f"{f['construction_permit']*100:>6.2f}  "
              f"{f['land_use']*100:>6.2f}  "
              f"{f['off_topic_criminal']*100:>6.2f}  "
              f"{f['off_topic_tax']*100:>6.2f}  "
              f"{r['internal_dedup_rate']*100:>6.2f}%")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--full-mlp", action="store_true",
                   help="Scan every multilegalpile file (slow, ~30 min)")
    p.add_argument("--sample-files", type=int, default=SAMPLE_FILES)
    args = p.parse_args()

    corpora = [
        ("multilegalpile", SEG_ROOT / "multilegalpile"),
        ("hf_cases",       SEG_ROOT / "hf_cases"),
        ("openlegaldata",  SEG_ROOT / "openlegaldata"),
        ("gerdalir",       SEG_ROOT / "gerdalir"),
    ]

    all_recs = {}
    totals = {}
    for name, path in corpora:
        full = args.full_mlp and name == "multilegalpile"
        recs, total_files = scan_corpus(name, path, args.sample_files, full)
        all_recs[name] = recs
        totals[name] = total_files
        print(f"  {name}: sampled {len(recs):,} docs from "
              f"{min(args.sample_files, total_files) if not full else total_files}/{total_files} files")

    results = [aggregate(name, recs) for name, recs in all_recs.items()]
    cross = cross_dedup(all_recs)

    for r, (name, _) in zip(results, corpora):
        r["total_files"] = totals[name]

    print_table(results)

    print("\nPer-multilegalpile-raw_type breakdown:")
    mlp = next((r for r in results if r["corpus"] == "multilegalpile"), None)
    if mlp and "per_raw_type" in mlp:
        print(f"  {'raw_type':<15s}  {'n':>8s}  {'char_med':>8s}  "
              f"{'wind%':>6s}  {'bau%':>6s}  {'crim%':>6s}  {'tax%':>6s}")
        print("  " + "-" * 85)
        for t, st in sorted(mlp["per_raw_type"].items(), key=lambda x: -x[1]["n"]):
            print(f"  {t:<15s}  {st['n']:>8,}  {st['char_median']:>8,}  "
                  f"{st['flag_wind']*100:>6.2f}  {st['flag_construction']*100:>6.2f}  "
                  f"{st['flag_off_criminal']*100:>6.2f}  {st['flag_off_tax']*100:>6.2f}")

    print("\nCross-corpus duplicate rate (first-500-char prefix hash):")
    for k, v in sorted(cross.items()):
        if v["overlap"] > 0:
            print(f"  {k:<45s}  {v['overlap']:>6,}  ({v['pct_of_{a}']}%)")

    out = {
        "per_corpus": results,
        "cross_dedup": cross,
        "sample_size_per_corpus": args.sample_files,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2))
    print(f"\nFull results -> {OUT_JSON}")


if __name__ == "__main__":
    main()
