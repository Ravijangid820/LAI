#!/usr/bin/env python3
"""Stage volume-tier stress matters from real VDR data.

Samples files from /data/projects/lai/LAI/data/lai-raw/VDRs/ into tier
directories under LAI/demo-seed/stress-volumes/. Uses symlinks so we
don't duplicate 6 GB; a separate bundle script resolves them to copies
when we hand the tarball to Kristian.

Tiers:
    T1  volume-10       ~10 docs    sanity (matches existing stress-vdr)
    T2  volume-100      ~100 docs   realistic small VDR
    T3  volume-500      ~500 docs   realistic mid VDR
    T4  volume-1000     ~1000 docs  realistic large VDR
    T5  volume-5000     ~5000 docs  breaking-point
    -   format-adversarial  ~30     hand-picked edges

Run:
    python LAI/scripts/stress/stage_volume_matters.py

Idempotent: clears + re-creates each tier dir on every run.
"""

from __future__ import annotations

import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

VDR_ROOT = Path("/data/projects/lai/LAI/data/lai-raw/VDRs")
OUT_ROOT = Path("/data/projects/lai/LAI/demo-seed/stress-volumes")
SEED = 42  # deterministic sampling — same script run = same tier composition

# Backend-supported extensions (mirrors SUPPORTED_DOC_EXTS in the UI).
ACCEPTED = {".pdf", ".doc", ".docx", ".xlsx", ".xls", ".txt", ".csv", ".md"}

SMALL = 1 * 1024 * 1024       # < 1 MB
MEDIUM = 10 * 1024 * 1024     # 1-10 MB
LARGE = 50 * 1024 * 1024      # 10-50 MB
HUGE = 100 * 1024 * 1024      # 50-100 MB
# anything > 100 MB hits the serve_rag cap → excluded from normal tiers,
# kept for format-adversarial only.


def bucket(size: int) -> str:
    if size < SMALL:
        return "xs"
    if size < MEDIUM:
        return "s"
    if size < LARGE:
        return "m"
    if size < HUGE:
        return "l"
    return "xl"


def scan_vdrs() -> list[dict]:
    """Walk VDRs, return list of file records."""
    out: list[dict] = []
    for vdr_dir in sorted(VDR_ROOT.iterdir()):
        if not vdr_dir.is_dir():
            continue
        for path in vdr_dir.rglob("*"):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext not in ACCEPTED:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            out.append({
                "path": str(path),
                "vdr": vdr_dir.name,
                "ext": ext,
                "size": size,
                "bucket": bucket(size),
            })
    return out


def stage_tier(name: str, files: list[dict], note: str) -> None:
    """Materialize tier as symlinks + manifest.json."""
    tier_dir = OUT_ROOT / name
    if tier_dir.exists():
        shutil.rmtree(tier_dir)
    tier_dir.mkdir(parents=True)

    # Symlink names: zero-padded index + sanitized original name, so the
    # tester sees stable ordering and no collisions across VDRs.
    width = max(4, len(str(len(files))))
    rels: list[dict] = []
    for i, f in enumerate(files):
        src = Path(f["path"])
        # Prefix index to dedupe filenames from different VDRs.
        link_name = f"{i:0{width}d}__{src.name}"
        link = tier_dir / link_name
        try:
            link.symlink_to(src)
        except OSError as e:
            print(f"  ! symlink failed for {src}: {e}")
            continue
        rels.append({
            "link": link_name,
            "source": str(src),
            "vdr": f["vdr"],
            "ext": f["ext"],
            "size": f["size"],
            "bucket": f["bucket"],
        })

    # Per-tier stats.
    by_ext = defaultdict(int)
    by_bucket = defaultdict(int)
    by_vdr = defaultdict(int)
    total_size = 0
    for r in rels:
        by_ext[r["ext"]] += 1
        by_bucket[r["bucket"]] += 1
        by_vdr[r["vdr"]] += 1
        total_size += r["size"]

    manifest = {
        "tier": name,
        "note": note,
        "seed": SEED,
        "file_count": len(rels),
        "total_bytes": total_size,
        "total_mb": round(total_size / (1024 * 1024), 1),
        "by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
        "by_size_bucket": {
            "xs (<1MB)": by_bucket["xs"],
            "s (1-10MB)": by_bucket["s"],
            "m (10-50MB)": by_bucket["m"],
            "l (50-100MB)": by_bucket["l"],
        },
        "by_vdr": dict(sorted(by_vdr.items(), key=lambda kv: -kv[1])),
        "files": rels,
    }
    (tier_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
    )
    print(
        f"  {name}: {len(rels)} files, {manifest['total_mb']} MB, "
        f"ext={dict(by_ext)}",
    )


def sample(rng: random.Random, pool: list[dict], n: int,
           ext_mix: dict[str, float] | None = None,
           fallback_pool: list[dict] | None = None) -> list[dict]:
    """Sample n files from pool, optionally enforcing ext distribution.

    If the primary pool can't satisfy a given ext quota, draws the
    deficit from ``fallback_pool`` (typically the full VDR universe).
    Office formats only exist in WP Altmark + WP Tostedt, so realistic
    tiers must cross-draw to show those code paths at all.
    """
    if ext_mix is None:
        return rng.sample(pool, min(n, len(pool)))
    by_ext: dict[str, list[dict]] = defaultdict(list)
    for f in pool:
        by_ext[f["ext"]].append(f)
    by_ext_fb: dict[str, list[dict]] = defaultdict(list)
    if fallback_pool:
        for f in fallback_pool:
            by_ext_fb[f["ext"]].append(f)
    picked: list[dict] = []
    picked_ids: set[str] = set()
    for ext, frac in ext_mix.items():
        count = round(n * frac)
        bucket_pool = by_ext.get(ext, [])
        take = rng.sample(bucket_pool, min(count, len(bucket_pool)))
        picked.extend(take)
        picked_ids.update(f["path"] for f in take)
        deficit = count - len(take)
        if deficit > 0 and by_ext_fb.get(ext):
            extra_pool = [f for f in by_ext_fb[ext]
                          if f["path"] not in picked_ids]
            extra = rng.sample(extra_pool, min(deficit, len(extra_pool)))
            picked.extend(extra)
            picked_ids.update(f["path"] for f in extra)
            if len(extra) < deficit:
                print(f"    ! {ext}: wanted {count}, got "
                      f"{len(take)}+{len(extra)} (global pool exhausted)")
        elif deficit > 0:
            print(f"    ! {ext}: wanted {count}, got {len(take)} "
                  f"(no fallback available)")
    # Pad with random PDFs from primary pool if we under-sampled.
    if len(picked) < n:
        remaining = [f for f in pool
                     if f["path"] not in picked_ids and f["ext"] == ".pdf"]
        deficit = n - len(picked)
        extra = rng.sample(remaining, min(deficit, len(remaining)))
        picked.extend(extra)
    return picked


def main() -> None:
    rng = random.Random(SEED)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {VDR_ROOT}…")
    files = scan_vdrs()
    print(f"  found {len(files)} ingestible files")

    by_vdr = defaultdict(list)
    for f in files:
        by_vdr[f["vdr"]].append(f)

    # ── T1 ──────────────────────────────────────────────────────────────
    # 10 PDFs from Butterberg — sanity baseline.
    t1_pool = [f for f in by_vdr["WP Butterberg"] if f["ext"] == ".pdf"]
    t1 = sample(rng, t1_pool, 10)
    stage_tier(
        "volume-10",
        t1,
        "Sanity baseline. 10 PDFs from WP Butterberg. Matches the "
        "complexity of the existing stress-vdr demo set.",
    )

    # ── T2 ──────────────────────────────────────────────────────────────
    # ~100 docs from Butterberg + 33:34 with realistic office-format mix.
    t2_pool = by_vdr["WP Butterberg"] + by_vdr["WP 33&#x3a;34"]
    t2 = sample(rng, t2_pool, 100, ext_mix={
        ".pdf": 0.90,
        ".xlsx": 0.08,
        ".docx": 0.02,
    }, fallback_pool=files)
    stage_tier(
        "volume-100",
        t2,
        "Realistic small VDR. WP Butterberg + WP 33:34. "
        "90% PDF / 8% XLSX / 2% DOCX.",
    )

    # ── T3 ──────────────────────────────────────────────────────────────
    # ~500 docs from Lamstedt + Hudehatten, broader mix.
    t3_pool = by_vdr["WP Lamstedt"] + by_vdr["WP Hudehatten"]
    t3 = sample(rng, t3_pool, 500, ext_mix={
        ".pdf": 0.92,
        ".xlsx": 0.06,
        ".docx": 0.015,
        ".doc": 0.005,
    }, fallback_pool=files)
    stage_tier(
        "volume-500",
        t3,
        "Realistic mid VDR. WP Lamstedt + WP Hudehatten. "
        "Native + scanned PDFs, office formats. Demo park = Lamstedt.",
    )

    # ── T4 ──────────────────────────────────────────────────────────────
    # ~1000 docs anchored on Tostedt (944) + topped up from Sebbenhausen.
    t4_pool = by_vdr["WP Tostedt"] + by_vdr["WP Sebbenhausen"]
    t4 = sample(rng, t4_pool, 1000, ext_mix={
        ".pdf": 0.90,
        ".xlsx": 0.075,
        ".xls": 0.015,
        ".doc": 0.007,
        ".docx": 0.003,
    }, fallback_pool=files)
    stage_tier(
        "volume-1000",
        t4,
        "Realistic large VDR. WP Tostedt + supplement from WP Sebbenhausen. "
        "Full office-format mix.",
    )

    # ── T5 ──────────────────────────────────────────────────────────────
    # ~5000 docs spread across every VDR — breaking-point.
    t5 = sample(rng, files, 5000)
    stage_tier(
        "volume-5000",
        t5,
        "Breaking-point. ~5000 docs spread across all 9 VDRs. "
        "Real-world distribution.",
    )

    # ── adversarial ─────────────────────────────────────────────────────
    # Hand-pick edges: biggest, smallest, scanned-only candidates, legacy
    # formats. We surface the SQL-style hints by filename patterns so
    # this stays maintainable as the source VDRs change.
    adv: list[dict] = []

    # Top 5 largest PDFs (oversized contracts / drawings).
    pdfs_by_size = sorted([f for f in files if f["ext"] == ".pdf"],
                          key=lambda f: -f["size"])
    adv.extend(pdfs_by_size[:5])

    # Top 5 smallest non-empty PDFs (often image-only single-page scans).
    small_pdfs = sorted(
        [f for f in files if f["ext"] == ".pdf" and f["size"] > 1024],
        key=lambda f: f["size"],
    )
    adv.extend(small_pdfs[:5])

    # All legacy .doc (Docling has had issues with these).
    adv.extend([f for f in files if f["ext"] == ".doc"])

    # Top 5 largest XLSX (multi-sheet stress).
    xlsx_by_size = sorted([f for f in files if f["ext"] == ".xlsx"],
                          key=lambda f: -f["size"])
    adv.extend(xlsx_by_size[:5])

    # All legacy .xls.
    adv.extend([f for f in files if f["ext"] == ".xls"][:5])

    # Dedupe while preserving order.
    seen = set()
    adv_unique = []
    for f in adv:
        if f["path"] not in seen:
            adv_unique.append(f)
            seen.add(f["path"])

    stage_tier(
        "format-adversarial",
        adv_unique,
        "Edge-case set. Largest PDFs, near-empty PDFs (likely scans), "
        "legacy .doc/.xls, largest XLSX. Pipeline should ingest these "
        "OR refuse them gracefully — no silent corruption, no hangs.",
    )

    # ── Top-level index ─────────────────────────────────────────────────
    summary = []
    for tier_dir in sorted(OUT_ROOT.iterdir()):
        m = tier_dir / "manifest.json"
        if not m.exists():
            continue
        data = json.loads(m.read_text())
        summary.append({
            "tier": data["tier"],
            "file_count": data["file_count"],
            "total_mb": data["total_mb"],
            "by_extension": data["by_extension"],
        })
    (OUT_ROOT / "INDEX.json").write_text(json.dumps(summary, indent=2))
    print("\nSummary written to", OUT_ROOT / "INDEX.json")


if __name__ == "__main__":
    main()
