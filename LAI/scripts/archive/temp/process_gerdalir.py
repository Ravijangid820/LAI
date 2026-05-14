"""
Processor for **GERDaLIR** (German Dataset for Legal Information Retrieval).

Schema is trivial — each record is ``{"id": "cNNN", "text": "...", "title": "..."}``
— but we still route it through the same segments-JSONL format so downstream
Step 2 + Step 6 consume it identically to everything else.

Besides the corpus we ALSO copy `queries.jsonl` and `qrels.jsonl` to a
known path so our eval harness can use them as a gold retrieval benchmark.

Usage:
    python scripts/temp/process_gerdalir.py
    python scripts/temp/process_gerdalir.py --n 1000   # smoke test
    python scripts/temp/process_gerdalir.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

LAI_DIR  = Path(__file__).resolve().parents[3]
RAW_DIR  = LAI_DIR / "data" / "lai-raw" / "legal_data" / "gerdalir_full"
SEG_DIR  = LAI_DIR / "data" / "lai-segments" / "legal_data" / "gerdalir"
BENCH    = LAI_DIR / "scripts" / "eval" / "rag_eval_results" / "gerdalir_benchmark"

MAX_SECTION_CHARS = 4000


def char_chunk(text: str, max_chars: int = MAX_SECTION_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    # Paragraph-first, then mid-paragraph hard split for pathological docs
    chunks = []
    buf = ""
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


def process_record(rec: dict) -> dict | None:
    rid = (rec.get("id") or "").strip()
    text = (rec.get("text") or "").strip()
    if not rid or not text or len(text) < 50:
        return None

    title = (rec.get("title") or "").strip()
    doc_id = hashlib.sha256(f"gerdalir:{rid}".encode()).hexdigest()[:16]

    # GERDaLIR is already well-sized — corpus avg is ~2-5k chars.
    # Most records fit in one segment.
    chunks = char_chunk(text)
    segments = [
        {
            "text": c,
            "section": title if title else (f"Corpus doc {rid}" + (f" (part {i+1})" if len(chunks) > 1 else "")),
            "page_start": None, "page_end": None, "type": "text",
        }
        for i, c in enumerate(chunks)
    ]

    return {
        "doc_id":      doc_id,
        "source_file": f"legal_data/gerdalir_full/corpus/{rid}.json",
        "language":    "de",
        "doc_type":    "urteil",  # GERDaLIR corpus is German court decisions
        "segments":    segments,
        "metadata": {
            "gerdalir_id":   rid,
            "source_corpus": "gerdalir",
        },
    }


def copy_benchmark_files() -> None:
    BENCH.mkdir(parents=True, exist_ok=True)
    for name in ("queries.jsonl", "qrels.jsonl"):
        src = RAW_DIR / name
        dst = BENCH / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            print(f"  copied {src.name} → {dst}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n",       type=int, default=0,
                   help="0 = all (~131K docs); smaller for smoke tests")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    corpus = RAW_DIR / "corpus.jsonl"
    if not corpus.exists():
        raise SystemExit(f"Missing {corpus}")

    if not args.dry_run:
        SEG_DIR.mkdir(parents=True, exist_ok=True)

    batch: list[dict] = []
    batch_idx = 0
    batch_size = 500
    seen_ids: set[str] = set()
    stats = {"seen": 0, "emitted": 0, "skipped": 0, "duplicates": 0}
    t0 = time.time()

    def flush():
        nonlocal batch_idx, batch
        if not batch:
            return
        if not args.dry_run:
            out = SEG_DIR / f"gerdalir_batch_{batch_idx:05d}.segments.jsonl"
            with open(out, "w", encoding="utf-8") as f:
                for r in batch:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        batch_idx += 1
        batch = []

    with open(corpus) as f:
        for line in f:
            stats["seen"] += 1
            if args.n and stats["seen"] > args.n:
                break
            try:
                rec = json.loads(line)
            except Exception:
                stats["skipped"] += 1
                continue
            out = process_record(rec)
            if out is None:
                stats["skipped"] += 1
                continue
            rid = rec["id"]
            if rid in seen_ids:
                stats["duplicates"] += 1
                continue
            seen_ids.add(rid)
            stats["emitted"] += 1
            batch.append(out)
            if len(batch) >= batch_size:
                flush()
            if stats["seen"] % 20_000 == 0:
                print(f"  {stats['seen']:,} processed, {stats['emitted']:,} emitted", file=sys.stderr)
    flush()

    if not args.dry_run:
        copy_benchmark_files()

    print("\n" + "=" * 60)
    print("GERDaLIR processing done")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k:<12s}: {v:>8,}")
    print(f"  elapsed    : {time.time()-t0:.1f}s")
    if not args.dry_run:
        print(f"  segments   : {SEG_DIR}/ ({batch_idx} batch files)")
        print(f"  benchmark  : {BENCH}/ (queries.jsonl + qrels.jsonl)")


if __name__ == "__main__":
    main()
