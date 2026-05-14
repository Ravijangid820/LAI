"""
Processor for **multilegalpile_full** — 998 sharded JSONL files, ~50M records.

Designed to be **resumable**: every finished shard drops a ``.done`` marker
under ``state/`` next to its output. On each run we skip shards that are
already marked done, so you can start the script, Ctrl-C whenever (GPUs
needed elsewhere), and start it again later — it picks up where it left off.

Records have schema: ``{language, type, jurisdiction, text}``.
We filter to German only and emit V5-compatible segments JSONL that
downstream Step 2 + Step 6 consume identically to all other sources.

Scale note — full German subset:
  ~998 shards × 50k/shard × ~20% de ≈ 10M German docs
  → ~60-120M children after chunking
  → Step 6 embedding = 28+ days on one GPU
So realistically we'd take a subset or stream-process over weeks. This
script makes incremental progress tractable.

Usage:
    # Process all shards (resumable; Ctrl-C safe)
    python scripts/temp/process_multilegalpile.py

    # Only a handful of shards (for smoke test)
    python scripts/temp/process_multilegalpile.py --max-shards 3

    # Restrict to specific jurisdictions / types
    python scripts/temp/process_multilegalpile.py \\
        --jurisdictions Germany,Austria,Switzerland \\
        --types legislation,caselaw

    # Re-process (clear .done markers first)
    python scripts/temp/process_multilegalpile.py --restart

    # Show current progress
    python scripts/temp/process_multilegalpile.py --status
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

LAI_DIR   = Path(__file__).resolve().parents[3]
RAW_DIR   = LAI_DIR / "data" / "lai-raw" / "legal_data" / "multilegalpile_full"
SHARDS_DIR = RAW_DIR / "all_all"
SEG_DIR   = LAI_DIR / "data" / "lai-segments" / "legal_data" / "multilegalpile"
STATE_DIR = SEG_DIR / "_state"

MAX_CHARS        = 4000
DEFAULT_LANG     = {"de"}

# Map multilegalpile type → our doc_type taxonomy
TYPE_MAP = {
    "caselaw":       "urteil",
    "legislation":   "gesetz",
    "contracts":     "vertrag",
    "legal-mc4":     "legal_text",
    "other":         "sonstige",
}


def char_chunk(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            if buf:
                chunks.append(buf)
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i:i + max_chars])
                buf = ""
            else:
                buf = para
    if buf:
        chunks.append(buf)
    return chunks


def process_record(rec: dict, shard: str, idx: int) -> dict | None:
    text = (rec.get("text") or "").strip()
    if not text or len(text) < 200:
        return None
    lang = rec.get("language") or ""
    typ  = rec.get("type")     or ""
    jur  = rec.get("jurisdiction") or ""

    # Cap absurd outliers
    if len(text) > 600_000:
        text = text[:600_000]

    doc_type = TYPE_MAP.get(typ, "sonstige")
    # Use shard+idx as stable unique id (multilegalpile has no doc id field)
    uid = f"mlp:{shard}:{idx}"
    doc_id = hashlib.sha256(uid.encode()).hexdigest()[:16]

    chunks = char_chunk(text)
    segments = [
        {
            "text": c,
            "section": "Volltext" if len(chunks) == 1 else f"Volltext (part {i+1})",
            "page_start": None, "page_end": None, "type": "text",
        }
        for i, c in enumerate(chunks)
    ]

    return {
        "doc_id":      doc_id,
        "source_file": f"legal_data/multilegalpile_full/{shard}#{idx}",
        "language":    lang,
        "doc_type":    doc_type,
        "segments":    segments,
        "metadata": {
            "source_corpus": "multilegalpile",
            "jurisdiction":  jur,
            "raw_type":      typ,
        },
    }


# -----------------------------------------------------------------------------
# Resume state
# -----------------------------------------------------------------------------

def done_marker(shard_name: str) -> Path:
    return STATE_DIR / f"{shard_name}.done"


def already_done(shard_name: str) -> bool:
    return done_marker(shard_name).exists()


def mark_done(shard_name: str, stats: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    done_marker(shard_name).write_text(json.dumps(stats))


def clear_all_done() -> int:
    if not STATE_DIR.exists():
        return 0
    n = 0
    for f in STATE_DIR.glob("*.done"):
        f.unlink(); n += 1
    return n


def status() -> None:
    shards = sorted(SHARDS_DIR.glob("all_all_shard_*.jsonl"))
    total = len(shards)
    done  = sum(1 for s in shards if already_done(s.name))
    print(f"multilegalpile status: {done}/{total} shards processed ({done*100/max(total,1):.1f}%)")

    # Aggregate marker stats
    agg = {"emitted": 0, "skipped": 0, "filtered_lang": 0, "filtered_type": 0,
           "filtered_jur": 0, "seen": 0}
    for s in shards:
        dm = done_marker(s.name)
        if dm.exists():
            try:
                d = json.loads(dm.read_text())
                for k in agg:
                    agg[k] += d.get(k, 0)
            except Exception:
                pass
    print()
    print("Totals across completed shards:")
    for k, v in agg.items():
        print(f"  {k:<20s} {v:>12,}")

    # Output segments on disk
    if SEG_DIR.exists():
        segs = list(SEG_DIR.glob("*.segments.jsonl"))
        size = sum(s.stat().st_size for s in segs)
        print(f"\nSegments written: {len(segs):,} batch files, {size/1024**3:.2f} GB")


# -----------------------------------------------------------------------------
# Core loop
# -----------------------------------------------------------------------------

def sniff_shard_language(shard: Path, sample: int = 5) -> str | None:
    """Return the language of the first N records (shards are single-language).

    multilegalpile shards are sorted by language, so the first record is
    authoritative. Sample a few in case of leading garbage."""
    langs = set()
    with open(shard) as f:
        for i, line in enumerate(f):
            if i >= sample:
                break
            try:
                langs.add(json.loads(line).get("language"))
            except Exception:
                pass
    if len(langs) == 1:
        return next(iter(langs))
    return None  # mixed or empty — fall through to full scan


def process_shard(shard: Path,
                  allowed_langs: set[str],
                  allowed_types: set[str] | None,
                  allowed_juris: set[str] | None,
                  batch_size: int = 500) -> dict:
    """Process one shard end-to-end. Writes its own segments files + done marker."""
    out_prefix = shard.stem  # e.g. all_all_shard_00042
    stats = dict(seen=0, emitted=0, skipped=0,
                 filtered_lang=0, filtered_type=0, filtered_jur=0)

    # Fast path: shards are single-language. If head sample shows a non-allowed
    # language, mark done without reading 50K lines.
    sniffed = sniff_shard_language(shard)
    if sniffed is not None and sniffed not in allowed_langs:
        stats["filtered_lang"] = -1  # sentinel: skipped by head-sniff
        mark_done(shard.name, stats)
        return stats

    batch: list[dict] = []
    batch_idx = 0

    def flush():
        nonlocal batch_idx, batch
        if not batch:
            return
        SEG_DIR.mkdir(parents=True, exist_ok=True)
        out = SEG_DIR / f"{out_prefix}_batch_{batch_idx:05d}.segments.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for rec in batch:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        batch_idx += 1
        batch = []

    with open(shard) as f:
        for i, line in enumerate(f):
            stats["seen"] += 1
            try:
                rec = json.loads(line)
            except Exception:
                stats["skipped"] += 1
                continue

            if rec.get("language") not in allowed_langs:
                stats["filtered_lang"] += 1
                continue
            if allowed_types and rec.get("type") not in allowed_types:
                stats["filtered_type"] += 1
                continue
            if allowed_juris and rec.get("jurisdiction") not in allowed_juris:
                stats["filtered_jur"] += 1
                continue

            out = process_record(rec, shard.name, i)
            if out is None:
                stats["skipped"] += 1
                continue

            stats["emitted"] += 1
            batch.append(out)
            if len(batch) >= batch_size:
                flush()

    flush()
    mark_done(shard.name, stats)
    return stats


def iter_shards_todo(max_shards: int = 0) -> Iterable[Path]:
    shards = sorted(SHARDS_DIR.glob("all_all_shard_*.jsonl"))
    pending = [s for s in shards if not already_done(s.name)]
    if max_shards:
        pending = pending[:max_shards]
    return pending


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--languages", default="de",
                   help="Comma-separated ISO-639-1 codes to keep (default: de)")
    p.add_argument("--types", default="",
                   help="Comma-separated record types to keep; empty = all. "
                        "Options: caselaw, legislation, contracts, legal-mc4, other")
    p.add_argument("--jurisdictions", default="",
                   help="Comma-separated jurisdictions to keep; empty = all")
    p.add_argument("--max-shards", type=int, default=0,
                   help="Stop after processing N NEW shards this run (0 = unlimited)")
    p.add_argument("--restart", action="store_true",
                   help="Clear all .done markers and start over. Does NOT delete segments.")
    p.add_argument("--status", action="store_true",
                   help="Print progress and exit without processing")
    args = p.parse_args()

    if args.status:
        status()
        return

    if args.restart:
        n = clear_all_done()
        print(f"Cleared {n} .done markers; will re-scan all shards.")

    allowed_langs = set(args.languages.split(","))
    allowed_types = set(args.types.split(",")) if args.types else None
    allowed_juris = set(args.jurisdictions.split(",")) if args.jurisdictions else None

    pending = list(iter_shards_todo(args.max_shards))
    total_shards = len(list(SHARDS_DIR.glob("all_all_shard_*.jsonl")))
    already = total_shards - len([s for s in SHARDS_DIR.glob("all_all_shard_*.jsonl") if not already_done(s.name)])

    print(f"multilegalpile: {total_shards} total shards, "
          f"{already} already done, {len(pending)} to process this run")
    print(f"Filters: lang={allowed_langs}  types={allowed_types or 'ALL'}  "
          f"jurisdictions={allowed_juris or 'ALL'}")
    print()

    if not pending:
        print("Nothing to do (all shards marked done). Use --restart to re-scan.")
        return

    t0_all = time.time()
    run_stats = dict(seen=0, emitted=0, skipped=0,
                     filtered_lang=0, filtered_type=0, filtered_jur=0)

    for i, shard in enumerate(pending):
        t0 = time.time()
        stats = process_shard(shard, allowed_langs, allowed_types, allowed_juris)
        dt = time.time() - t0
        for k in run_stats:
            run_stats[k] += stats[k]
        print(f"  [{i+1}/{len(pending)}] {shard.name}  "
              f"seen={stats['seen']:>6,}  emitted={stats['emitted']:>5,}  "
              f"filtered_lang={stats['filtered_lang']:>5,}  "
              f"({dt:.1f}s)", flush=True)
        # Free memory before next shard (50K records × potentially 17k chars = ~1 GB in worst case)
        gc.collect()

    elapsed = time.time() - t0_all
    print("\n" + "=" * 60)
    print(f"Run complete: {len(pending)} shards in {elapsed/60:.1f} min")
    print("=" * 60)
    for k, v in run_stats.items():
        print(f"  {k:<20s} {v:>12,}")
    print()
    status()


if __name__ == "__main__":
    main()
