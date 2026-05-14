"""
Structural audit of the hf_cases corpus before writing the processor.

Questions to answer:
  1. What fields are always present, which are optional?
  2. Distribution of `court.level_of_appeal` — which courts dominate?
  3. Distribution of `type` — Urteile vs Beschlüsse vs other?
  4. Are the markdown section headings consistent? (## Tenor, ## Tatbestand, ...)
  5. How often is each section present?
  6. How long are the documents — do we need to worry about embedding limits?
  7. Is there meaningful overlap with openlegaldata (same slug/ecli)?
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

LAI_DIR = Path(__file__).resolve().parents[3]
HF_CASES_DIR = LAI_DIR / "data" / "lai-raw" / "legal_data" / "hf_cases"

SECTION_HEADING_RX = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=1000,
                   help="How many cases to sample (0 = all 251K; slow).")
    p.add_argument("--dir", default=str(HF_CASES_DIR))
    args = p.parse_args()

    case_dir = Path(args.dir)
    files = sorted(case_dir.glob("case_*.json"))
    if args.n and args.n < len(files):
        # Stratified sample — take every Nth file
        step = len(files) // args.n
        files = files[::step][:args.n]
    print(f"Inspecting {len(files):,} cases from {case_dir}")

    field_presence        = Counter()
    court_levels          = Counter()
    jurisdictions         = Counter()
    decision_types        = Counter()
    section_headings      = Counter()
    sections_per_case     = Counter()
    content_size_hist     = Counter()   # bucketed character count
    markdown_size_hist    = Counter()
    year_dist             = Counter()
    has_markdown          = 0
    has_content           = 0
    missing_fields: dict[str, int] = defaultdict(int)
    slug_sample = set()

    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            missing_fields["_parse_error"] += 1
            continue

        for k in ("id", "slug", "court", "file_number", "date", "type",
                  "ecli", "content", "markdown_content"):
            if d.get(k) is not None and d.get(k) != "":
                field_presence[k] += 1
            else:
                missing_fields[k] += 1

        court = d.get("court") or {}
        court_levels[court.get("level_of_appeal") or "UNKNOWN"] += 1
        jurisdictions[court.get("jurisdiction") or "UNKNOWN"] += 1
        decision_types[d.get("type") or "UNKNOWN"] += 1

        date = d.get("date") or ""
        if len(date) >= 4 and date[:4].isdigit():
            year_dist[date[:4]] += 1

        md = d.get("markdown_content") or ""
        ct = d.get("content") or ""
        if md: has_markdown += 1
        if ct: has_content += 1

        # Bucket content sizes
        for label, val in (("markdown", len(md)), ("html", len(ct))):
            if val == 0:
                bucket = "0"
            elif val < 2_000:
                bucket = "<2k"
            elif val < 10_000:
                bucket = "2k–10k"
            elif val < 50_000:
                bucket = "10k–50k"
            else:
                bucket = ">50k"
            (markdown_size_hist if label == "markdown" else content_size_hist)[bucket] += 1

        if md:
            headings = SECTION_HEADING_RX.findall(md)
            sections_per_case[len(headings)] += 1
            for h in headings:
                # Normalize punctuation variations
                norm = h.strip().rstrip(":").strip()
                section_headings[norm] += 1

        if len(slug_sample) < 20 and d.get("slug"):
            slug_sample.add(d["slug"])

    n = len(files)

    def _pct_table(title: str, c: Counter, top: int = 15):
        print(f"\n=== {title} (top {top}) ===")
        for k, v in c.most_common(top):
            print(f"  {str(k):<40s} {v:>6} ({v/n:>6.1%})")
        if len(c) > top:
            rest = sum(v for _, v in c.most_common()[top:])
            print(f"  … {len(c)-top} more categories, total {rest:,} ({rest/n:.1%})")

    print("\n" + "=" * 68)
    print(f"HF_CASES inspection — n={n:,}")
    print("=" * 68)

    print("\nField presence:")
    for k, v in field_presence.most_common():
        print(f"  {k:<25s} {v:>6} ({v/n:>6.1%})")
    if any(missing_fields.values()):
        print("\nMissing:")
        for k, v in sorted(missing_fields.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {k:<25s} {v:>6} ({v/n:>6.1%})")

    _pct_table("Decision types", decision_types)
    _pct_table("Court levels", court_levels)
    _pct_table("Jurisdictions", jurisdictions)
    _pct_table("Year distribution", year_dist, top=20)

    print(f"\nmarkdown_content present: {has_markdown:,} ({has_markdown/n:.1%})")
    print(f"html content present:     {has_content:,} ({has_content/n:.1%})")

    print("\nMarkdown size distribution:")
    for b in ("0", "<2k", "2k–10k", "10k–50k", ">50k"):
        v = markdown_size_hist.get(b, 0)
        print(f"  {b:<12s} {v:>6} ({v/n:>6.1%})")

    _pct_table("Section headings in markdown_content", section_headings, top=25)
    _pct_table("Section count per case", sections_per_case, top=10)

    print("\nSample slugs:")
    for s in list(slug_sample)[:10]:
        print(f"  {s}")


if __name__ == "__main__":
    main()
