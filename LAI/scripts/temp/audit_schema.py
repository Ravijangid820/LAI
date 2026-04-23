"""
Thorough structural audit to catch format drift within a corpus.

For every JSON file in a directory:
  - Records the set of top-level keys and their value types
  - Records the set of keys inside nested dicts (e.g. court.*)
  - Buckets string lengths per field
  - Flags empty / null / wrong-type values
  - Reports any file whose schema differs from the majority

Run before writing a processor so you know which cases the processor
needs to handle (and which you can legitimately treat as a fallback path).

Usage:
    python scripts/temp/audit_schema.py --dir data/lai-raw/legal_data/hf_cases --n 10000
    python scripts/temp/audit_schema.py --dir data/lai-raw/legal_data/openlegaldata_api_dump --n 500
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path


def type_str(v) -> str:
    if v is None: return "null"
    if isinstance(v, bool): return "bool"
    if isinstance(v, int):  return "int"
    if isinstance(v, float): return "float"
    if isinstance(v, str):  return "str"
    if isinstance(v, list): return f"list[{type_str(v[0]) if v else '?'}]"
    if isinstance(v, dict): return "dict"
    return type(v).__name__


def schema_hash(d, depth=0) -> str:
    """Deterministic hash of the structure — keys + types, not values."""
    if d is None: return "null"
    if isinstance(d, dict):
        parts = [f"{k}:{schema_hash(v, depth+1)}" for k, v in sorted(d.items())]
        return "{" + ",".join(parts) + "}"
    if isinstance(d, list):
        return f"[{schema_hash(d[0], depth+1) if d else ''}]"
    return type_str(d).split("[")[0]


def flatten_keys(obj, prefix=""):
    """Yield (dotted.path, type_str, value) tuples."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            yield p, type_str(v), v
            if isinstance(v, (dict, list)):
                yield from flatten_keys(v, p)
    elif isinstance(obj, list):
        # Use [*] to indicate list element — all items should share schema
        if obj:
            yield from flatten_keys(obj[0], f"{prefix}[*]")


def collect_files(root: Path, n: int) -> list[Path]:
    files = []
    for p in root.rglob("*.json"):
        files.append(p)
    files.sort()
    if n and n < len(files):
        # Stratified sample: take every Nth
        step = max(1, len(files) // n)
        files = files[::step][:n]
    return files


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--n",   type=int, default=5000,
                   help="0 = all (slow for 251K files)")
    p.add_argument("--root-key", default=None,
                   help="If JSON is {count, results:[...]} (openlegaldata), "
                        "use --root-key results to descend into the array.")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    root = Path(args.dir)
    files = collect_files(root, args.n)
    print(f"Auditing {len(files):,} files from {root}", file=sys.stderr)

    # Aggregates
    n_docs = 0
    schemas = Counter()               # schema_hash -> count
    schema_examples: dict[str, str] = {}   # schema_hash -> example filepath

    key_types: dict[str, Counter] = defaultdict(Counter)
    key_presence: Counter = Counter()
    key_empty: Counter = Counter()
    str_lens: dict[str, list[int]] = defaultdict(list)
    enum_values: dict[str, Counter] = defaultdict(Counter)

    parse_errors: list[str] = []
    non_docs: list[str] = []           # files that didn't yield doc dicts

    for fp in files:
        try:
            root_obj = json.loads(fp.read_text())
        except Exception as e:
            parse_errors.append(f"{fp}: {e!r}"[:200])
            continue

        # Unwrap wrapper format (e.g. openlegaldata {count, results: [...]})
        if args.root_key and isinstance(root_obj, dict) and args.root_key in root_obj:
            docs = root_obj[args.root_key]
        elif isinstance(root_obj, list):
            docs = root_obj
        else:
            docs = [root_obj]

        if not isinstance(docs, list):
            non_docs.append(str(fp))
            continue

        for d in docs:
            if not isinstance(d, dict):
                non_docs.append(f"{fp}: not-dict {type(d).__name__}")
                continue
            n_docs += 1

            h = schema_hash(d)
            schemas[h] += 1
            if h not in schema_examples:
                schema_examples[h] = str(fp)

            for path, tp, val in flatten_keys(d):
                key_presence[path] += 1
                key_types[path][tp] += 1
                if val is None or val == "" or val == [] or val == {}:
                    key_empty[path] += 1
                if isinstance(val, str):
                    str_lens[path].append(len(val))
                    # Enum-detect: if path looks like a category (short, < 50 chars
                    # and we haven't seen too many distinct values)
                    if len(val) < 50 and len(enum_values[path]) < 30:
                        enum_values[path][val] += 1

    print(f"\nParsed {n_docs:,} docs from {len(files):,} files")
    if parse_errors:
        print(f"\n⚠  {len(parse_errors)} parse errors:")
        for e in parse_errors[:5]:
            print(f"    {e}")
    if non_docs:
        print(f"\n⚠  {len(non_docs)} non-doc entries")

    # ---------- Schema variants ----------
    print(f"\n=== Schema variants: {len(schemas)} distinct shapes ===")
    total = sum(schemas.values())
    for h, c in schemas.most_common(10):
        pct = c / total * 100
        print(f"  {c:>8,} docs ({pct:>5.1f}%) — hash={h[:16]}…")
        print(f"      example: {schema_examples[h]}")
    if len(schemas) > 10:
        tail = sum(c for _, c in schemas.most_common()[10:])
        print(f"  + {len(schemas)-10} rare variants totaling {tail:,} docs ({tail/total*100:.1f}%)")

    # ---------- Field-level table ----------
    print(f"\n=== Field-level summary (n_docs={n_docs:,}) ===")
    print(f"{'field':<45s} {'presence':>10s} {'empty%':>8s} {'types':<30s} {'str_len (p50/p95/max)':<24s}")
    for path in sorted(key_presence, key=lambda k: (-key_presence[k], k)):
        p_count = key_presence[path]
        e_count = key_empty[path]
        e_pct = (e_count / p_count * 100) if p_count else 0
        types_str = ",".join(f"{t}:{c}" for t, c in key_types[path].most_common(3))
        lens = str_lens.get(path, [])
        if lens:
            lens_sorted = sorted(lens)
            p50 = lens_sorted[len(lens_sorted)//2]
            p95 = lens_sorted[int(len(lens_sorted)*0.95)]
            mx  = lens_sorted[-1]
            ll = f"{p50}/{p95}/{mx}"
        else:
            ll = "-"
        pres = f"{p_count}/{n_docs}"
        print(f"{path:<45s} {pres:>10s} {e_pct:>7.1f}% {types_str:<30s} {ll:<24s}")

    # ---------- Enum-like fields ----------
    print("\n=== Enum-like fields (low-cardinality string fields) ===")
    for path in sorted(enum_values, key=lambda k: len(enum_values[k])):
        vals = enum_values[path]
        if len(vals) < 3 or len(vals) > 30:
            continue  # skip non-enums
        # Only show string fields that look enum-like (top values dominate)
        covered = sum(c for _, c in vals.most_common(10))
        if covered < key_presence[path] * 0.9:
            continue  # not truly enum
        print(f"\n  {path}  ({len(vals)} distinct values, top 10 cover {covered}/{key_presence[path]})")
        for v, c in vals.most_common(10):
            print(f"    {v!r:<45s} {c:>7d}")

    # ---------- Save full JSON report ----------
    if args.out:
        def _lens(path):
            L = sorted(str_lens.get(path, []))
            if not L: return None
            return {"p50": L[len(L)//2], "p95": L[int(len(L)*0.95)], "max": L[-1], "n": len(L)}

        out = {
            "dir": str(root),
            "n_files": len(files),
            "n_docs": n_docs,
            "schemas": {h: {"count": c, "example": schema_examples[h]}
                        for h, c in schemas.most_common()},
            "fields": {
                path: {
                    "presence": key_presence[path],
                    "empty":    key_empty[path],
                    "types":    dict(key_types[path]),
                    "str_len":  _lens(path),
                    "sample_enum_values": dict(enum_values[path].most_common(20))
                            if len(enum_values[path]) <= 30 else None,
                }
                for path in key_presence
            },
            "parse_errors": parse_errors[:50],
            "non_docs":     non_docs[:50],
        }
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nSaved full audit to {args.out}")


if __name__ == "__main__":
    main()
