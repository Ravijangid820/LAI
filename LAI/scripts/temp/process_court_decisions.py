"""
Unified processor for German court decisions — handles both hf_cases
(one case per file) and openlegaldata (10 cases per page file).

Reads raw JSON, normalizes heterogeneous/partial metadata, chunks
respecting markdown section boundaries when present, falls back to
char-based chunking for unstructured cases.

Writes Step-1-compatible segments JSONL under
``data/lai-segments/legal_data/<source>/<case-id>.segments.jsonl``,
which Step 2 of the existing pipeline consumes unchanged.

Usage:
    # Dry run on 200 cases to inspect output
    python scripts/temp/process_court_decisions.py \\
        --source hf_cases --n 200 --dry-run

    # Full run (writes segments files)
    python scripts/temp/process_court_decisions.py --source hf_cases
    python scripts/temp/process_court_decisions.py --source openlegaldata
    python scripts/temp/process_court_decisions.py --source all
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import html
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterator

LAI_DIR   = Path(__file__).resolve().parents[2]
RAW_DIR   = LAI_DIR / "data" / "lai-raw" / "legal_data"
SEG_DIR   = LAI_DIR / "data" / "lai-segments" / "legal_data"

# -----------------------------------------------------------------------------
# Metadata normalization
# -----------------------------------------------------------------------------

COURT_LEVEL_FROM_NAME = [
    # Order matters: check more specific prefixes first
    ("Bundesverfassungsgericht",  "Bundesverfassungsgericht"),
    ("Bundesverwaltungsgericht",  "Bundesverwaltungsgericht"),
    ("Bundesfinanzhof",           "Bundesfinanzhof"),
    ("Bundesgerichtshof",         "Bundesgerichtshof"),
    ("Bundesarbeitsgericht",      "Bundesarbeitsgericht"),
    ("Bundessozialgericht",       "Bundessozialgericht"),
    ("Bundespatentgericht",       "Bundespatentgericht"),
    ("Landesarbeitsgericht",      "Landesarbeitsgericht"),
    ("Landessozialgericht",       "Landessozialgericht"),
    ("Finanzgericht",             "Finanzgericht"),
    ("Oberverwaltungsgericht",    "Oberverwaltungsgericht"),
    ("Verwaltungsgerichtshof",    "Verwaltungsgerichtshof"),
    ("Oberlandesgericht",         "Oberlandesgericht"),
    ("Landgericht",               "Landgericht"),
    ("Sozialgericht",             "Sozialgericht"),
    ("Arbeitsgericht",            "Arbeitsgericht"),
    ("Verwaltungsgericht",        "Verwaltungsgericht"),
    ("Amtsgericht",               "Amtsgericht"),
    ("Bayerischer Verwaltungsgerichtshof", "Verwaltungsgerichtshof"),
]

JURISDICTION_FROM_LEVEL = {
    "Bundesverfassungsgericht":   "Verfassungsgerichtsbarkeit",
    "Bundesverwaltungsgericht":   "Verwaltungsgerichtsbarkeit",
    "Oberverwaltungsgericht":     "Verwaltungsgerichtsbarkeit",
    "Verwaltungsgericht":         "Verwaltungsgerichtsbarkeit",
    "Verwaltungsgerichtshof":     "Verwaltungsgerichtsbarkeit",
    "Bundesfinanzhof":            "Finanzgerichtsbarkeit",
    "Finanzgericht":              "Finanzgerichtsbarkeit",
    "Bundesarbeitsgericht":       "Arbeitsgerichtsbarkeit",
    "Landesarbeitsgericht":       "Arbeitsgerichtsbarkeit",
    "Arbeitsgericht":             "Arbeitsgerichtsbarkeit",
    "Bundessozialgericht":        "Sozialgerichtsbarkeit",
    "Landessozialgericht":        "Sozialgerichtsbarkeit",
    "Sozialgericht":              "Sozialgerichtsbarkeit",
    "Bundesgerichtshof":          "Ordentliche Gerichtsbarkeit",
    "Oberlandesgericht":          "Ordentliche Gerichtsbarkeit",
    "Landgericht":                "Ordentliche Gerichtsbarkeit",
    "Amtsgericht":                "Ordentliche Gerichtsbarkeit",
    "Bundespatentgericht":        "Patentgerichtsbarkeit",
}

TYPE_NORMALIZATION = {
    # Urteile
    "urteil":               "urteil",
    "endurteil":            "urteil",
    "teilurteil":           "urteil",
    "schlussurteil":        "urteil",
    "grundurteil":          "urteil",
    "versäumnisurteil":     "urteil",
    "anerkenntnisurteil":   "urteil",
    "zwischenurteil":       "urteil",
    # Beschlüsse
    "beschluss":                       "beschluss",
    "kammerbeschluss":                 "beschluss",
    "nichtannahmebeschluss":           "beschluss",
    "stattgebender kammerbeschluss":   "beschluss",
    "kammerbeschluss ohne begründung": "beschluss",
    "geb":                             "beschluss",
    "vorlagebeschluss":                "beschluss",
    "aussetzungsbeschluss":            "beschluss",
    "ablehnung einstweilige anordnung":"beschluss",
    # Other
    "gerichtsbescheid":     "gerichtsbescheid",
    "eugh-vorlage":         "sonstige",
    "entscheidung":         "sonstige",
    "einstweilige anordnung":"sonstige",
    "abhilfebescheid":      "sonstige",
    "auslagenentscheidung": "sonstige",
    "vorbescheid":          "sonstige",
    "abw. meinung":         "sonstige",
}


def normalize_court_level(court: dict) -> str:
    """Try the structured field; fall back to parsing court.name."""
    raw = (court or {}).get("level_of_appeal")
    if raw:
        return raw
    name = ((court or {}).get("name") or "").strip()
    for prefix, level in COURT_LEVEL_FROM_NAME:
        if prefix in name:
            return level
    return "Unknown"


def normalize_jurisdiction(court: dict, court_level: str) -> str:
    raw = (court or {}).get("jurisdiction")
    if raw:
        return raw
    return JURISDICTION_FROM_LEVEL.get(court_level, "Unknown")


def normalize_type(raw_type: str) -> str:
    if not raw_type:
        return "sonstige"
    return TYPE_NORMALIZATION.get(raw_type.strip().lower(), "sonstige")


def normalize_unique_id(case: dict) -> str:
    """ECLI when populated, else slug. Either is globally unique."""
    ecli = (case.get("ecli") or "").strip()
    if ecli:
        return ecli
    return (case.get("slug") or "").strip()


# -----------------------------------------------------------------------------
# Content handling
# -----------------------------------------------------------------------------

SECTION_HEADING_RX = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
HTML_TAG_RX = re.compile(r"<[^>]+>")
MULTISPACE_RX = re.compile(r"[ \t]{2,}")
MULTILINE_RX = re.compile(r"\n{3,}")


def html_to_text(s: str) -> str:
    """Minimal HTML → plain text conversion for openlegaldata.

    We don't need anything clever — just strip tags, unescape entities,
    and normalize whitespace. If we ever need richer structure (list
    formatting, tables) we can plug in a real converter later.
    """
    if not s:
        return ""
    # Replace block-level tags with newlines to preserve paragraph structure
    s = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    # Drop all remaining tags
    s = HTML_TAG_RX.sub("", s)
    s = html.unescape(s)
    s = MULTISPACE_RX.sub(" ", s)
    s = MULTILINE_RX.sub("\n\n", s)
    return s.strip()


def split_on_headings(md: str) -> list[tuple[str, str]]:
    """Return [(section_title, body_text), ...]. Empty if no headings."""
    positions = [(m.start(), m.group(1).strip()) for m in SECTION_HEADING_RX.finditer(md)]
    if not positions:
        return []
    sections = []
    for i, (start, title) in enumerate(positions):
        line_end = md.find("\n", start)
        body_start = line_end + 1 if line_end != -1 else start + len(title) + 3
        body_end = positions[i + 1][0] if i + 1 < len(positions) else len(md)
        body = md[body_start:body_end].strip()
        if body:
            sections.append((title, body))
    return sections


def char_chunk(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    """Fallback chunker when no headings. Splits on paragraph boundaries.
    Keeps each chunk <= max_chars. Small overlap keeps semantics continuous.
    """
    if len(text) <= max_chars:
        return [text]
    chunks = []
    paras = text.split("\n\n")
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) > max_chars:
                # Mid-paragraph hard split — rare but possible for massive docs
                for i in range(0, len(p), max_chars - overlap):
                    chunks.append(p[i:i + max_chars])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


# -----------------------------------------------------------------------------
# Core processor
# -----------------------------------------------------------------------------

MAX_SECTION_CHARS = 4000  # oversized section → char-chunk within


def process_case(case: dict, source: str) -> dict | None:
    """Return a segments dict (Step-1-compatible) for one case, or None to skip."""
    # Pick the best available text
    md = case.get("markdown_content") or ""
    if not md:
        # openlegaldata path: only HTML content
        md = html_to_text(case.get("content") or "")
    if not md or len(md) < 50:
        return None  # empty or trivial — skip

    # Cap pathologically large docs (very rare — p99.5 is ~200k; seen up to 723k)
    if len(md) > 600_000:
        md = md[:600_000]

    # Normalized metadata
    court = case.get("court") or {}
    court_level = normalize_court_level(court)
    jurisdiction = normalize_jurisdiction(court, court_level)
    doc_type = normalize_type(case.get("type") or "")
    unique_id = normalize_unique_id(case)
    if not unique_id:
        return None  # can't dedupe without an id

    # Build segments — section-aware first, char-based fallback.
    # We distinguish three cases:
    #   2+ headings → one segment per section (split large sections internally)
    #   1  heading  → first chunk is the named section; later chunks are
    #                 "(Fortsetzung)" so we keep at least the opening-section
    #                 attribution
    #   0  headings → generic "Volltext" segments
    sections = split_on_headings(md)
    segments = []

    def emit_named(title: str, body: str) -> None:
        if len(body) <= MAX_SECTION_CHARS:
            segments.append({"text": body, "section": title,
                             "page_start": None, "page_end": None, "type": "text"})
        else:
            for i, chunk in enumerate(char_chunk(body, MAX_SECTION_CHARS)):
                segments.append({
                    "text": chunk,
                    "section": title if i == 0 else f"{title} (Fortsetzung {i})",
                    "page_start": None, "page_end": None, "type": "text",
                })

    if len(sections) >= 2:
        for title, body in sections:
            emit_named(title, body)
    elif len(sections) == 1:
        # Single heading covers the whole document. Keep its name on the
        # opening chunk so retrieval can still locate e.g. a Tenor.
        emit_named(sections[0][0], sections[0][1])
    else:
        # Fully unstructured — char-chunk the whole document
        chunks = char_chunk(md, MAX_SECTION_CHARS)
        for i, chunk in enumerate(chunks):
            segments.append({
                "text": chunk,
                "section": f"Volltext (part {i+1})" if len(chunks) > 1 else "Volltext",
                "page_start": None, "page_end": None, "type": "text",
            })

    if not segments:
        return None

    # doc_id = hash of unique_id (stable across runs)
    doc_id = hashlib.sha256(unique_id.encode()).hexdigest()[:16]

    return {
        "doc_id":      doc_id,
        "source_file": f"legal_data/{source}/{case.get('slug') or unique_id}.json",
        "language":    "de",
        "doc_type":    doc_type,
        "segments":    segments,
        "metadata": {
            "court_name":     court.get("name") or "Unknown",
            "court_level":    court_level,
            "jurisdiction":   jurisdiction,
            "court_id":       court.get("id"),
            "court_state":    court.get("state"),
            "file_number":    case.get("file_number") or None,
            "decision_date":  case.get("date") or None,
            "ecli":           case.get("ecli") or None,
            "slug":           case.get("slug") or None,
            "raw_type":       case.get("type") or None,
            "source_corpus":  source,
        },
    }


# -----------------------------------------------------------------------------
# Source adapters
# -----------------------------------------------------------------------------

def iter_hf_cases(limit: int = 0) -> Iterator[tuple[dict, Path]]:
    root = RAW_DIR / "hf_cases"
    files = sorted(root.glob("case_*.json"))
    if limit:
        files = files[:limit]
    for f in files:
        try:
            yield json.loads(f.read_text()), f
        except Exception as e:
            print(f"⚠  {f.name}: {e}", file=sys.stderr)


def iter_openlegaldata(limit: int = 0) -> Iterator[tuple[dict, Path]]:
    root = RAW_DIR / "openlegaldata_api_dump"
    files = sorted(root.glob("cases_page_*.json"))
    seen = 0
    for f in files:
        try:
            page = json.loads(f.read_text())
        except Exception as e:
            print(f"⚠  {f.name}: {e}", file=sys.stderr)
            continue
        for case in page.get("results", []):
            yield case, f
            seen += 1
            if limit and seen >= limit:
                return


# -----------------------------------------------------------------------------
# Main driver
# -----------------------------------------------------------------------------

def run(source: str, limit: int, dry_run: bool) -> None:
    out_dir = SEG_DIR / source
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    iter_fn = {"hf_cases": iter_hf_cases, "openlegaldata": iter_openlegaldata}[source]
    stats = Counter()
    seen_ids: set[str] = set()
    t0 = time.time()

    # Group segments by output file: keep one .segments.jsonl per ~500 cases
    # to avoid creating 290K tiny files. Batch by source-specific chunking.
    batch_size = 500
    batch: list[dict] = []
    batch_idx = 0

    def flush():
        nonlocal batch_idx, batch
        if not batch:
            return
        if not dry_run:
            out_path = out_dir / f"{source}_batch_{batch_idx:05d}.segments.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                for rec in batch:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        batch_idx += 1
        batch = []

    for case, src_path in iter_fn(limit=limit):
        stats["seen"] += 1
        rec = process_case(case, source)
        if rec is None:
            stats["skipped"] += 1
            continue

        # Dedupe by ECLI or slug
        uid = normalize_unique_id(case)
        if uid in seen_ids:
            stats["duplicates"] += 1
            continue
        seen_ids.add(uid)

        stats["emitted"] += 1
        stats[f"type.{rec['doc_type']}"] += 1
        stats[f"level.{rec['metadata']['court_level']}"] += 1
        batch.append(rec)
        if len(batch) >= batch_size:
            flush()

        if stats["seen"] % 20_000 == 0:
            elapsed = time.time() - t0
            rate = stats["seen"] / elapsed
            print(f"  {stats['seen']:,} processed, {stats['emitted']:,} emitted "
                  f"({rate:.0f}/s)", file=sys.stderr)

    flush()

    # ---- summary ----
    print("\n" + "=" * 72)
    print(f"SOURCE: {source}")
    print("=" * 72)
    for k in ("seen", "emitted", "skipped", "duplicates"):
        print(f"  {k:<12s}: {stats[k]:>8,}")
    print("\n  doc_type breakdown:")
    for k, v in sorted(stats.items()):
        if k.startswith("type."):
            print(f"    {k[5:]:<20s} {v:>8,}")
    print("\n  court_level breakdown:")
    for k, v in sorted([(k, v) for k, v in stats.items() if k.startswith("level.")],
                       key=lambda kv: -kv[1])[:15]:
        print(f"    {k[6:]:<30s} {v:>8,}")
    dt = time.time() - t0
    print(f"\n  elapsed: {dt:.0f}s  ({stats['seen']/max(dt,1):.0f} cases/s)")
    if not dry_run:
        print(f"  output:  {out_dir}/  ({batch_idx} batch files)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["hf_cases", "openlegaldata", "all"],
                   required=True)
    p.add_argument("--n", type=int, default=0,
                   help="Cap per source (0 = all)")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse+chunk but do not write output files.")
    args = p.parse_args()

    if args.source == "all":
        for s in ("hf_cases", "openlegaldata"):
            run(s, args.n, args.dry_run)
    else:
        run(args.source, args.n, args.dry_run)


if __name__ == "__main__":
    main()
