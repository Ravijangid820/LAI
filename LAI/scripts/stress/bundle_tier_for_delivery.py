#!/usr/bin/env python3
"""Resolve a tier's symlinks into real file copies + tar.gz it.

Use when handing a stress tier to Kristian (or any external tester) —
tarballs don't preserve symlinks across machines, and the source VDRs
are private. This script materializes one tier into a self-contained
directory + tarball under ``LAI/demo-seed/stress-volumes/_bundles/``.

Run:
    python LAI/scripts/stress/bundle_tier_for_delivery.py volume-100
    python LAI/scripts/stress/bundle_tier_for_delivery.py volume-5000

Notes:
    - volume-5000 is ~5.4 GB resolved; expect several minutes to copy
      and tar. Plenty of free space required on /data.
    - Filenames in the bundle keep the indexed prefix (0000__, 0001__…)
      so ordering survives. Original VDR-relative paths are recorded in
      manifest.json inside the bundle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

STRESS_ROOT = Path("/data/projects/lai/LAI/demo-seed/stress-volumes")
BUNDLES = STRESS_ROOT / "_bundles"


def bundle(tier_name: str) -> None:
    tier = STRESS_ROOT / tier_name
    if not tier.is_dir():
        sys.exit(f"tier not found: {tier}")
    manifest_path = tier / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"manifest missing: {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    print(f"Bundling {tier_name}: {manifest['file_count']} files, "
          f"{manifest['total_mb']} MB")

    BUNDLES.mkdir(parents=True, exist_ok=True)
    dest = BUNDLES / tier_name
    if dest.exists():
        print(f"  removing existing {dest}")
        shutil.rmtree(dest)
    dest.mkdir()

    copied = 0
    for entry in manifest["files"]:
        src = Path(entry["source"])
        if not src.exists():
            print(f"  ! source vanished: {src}")
            continue
        target = dest / entry["link"]
        shutil.copy2(src, target)
        copied += 1
        if copied % 250 == 0:
            print(f"  …copied {copied}/{manifest['file_count']}")

    # Drop the original manifest inside the bundle so the recipient knows
    # what's what and where it came from.
    shutil.copy2(manifest_path, dest / "manifest.json")
    (dest / "README.txt").write_text(
        f"{tier_name}\n"
        f"{manifest['file_count']} files · {manifest['total_mb']} MB\n"
        f"{manifest['note']}\n\n"
        "Drop the entire directory contents into a LAI matter to test.\n"
        "manifest.json records the original VDR-relative source path for\n"
        "each file (audit trail).\n",
    )
    print(f"  copied {copied} files into {dest}")

    tarball = BUNDLES / f"{tier_name}.tar.gz"
    if tarball.exists():
        tarball.unlink()
    print(f"  tarring → {tarball}")
    subprocess.run(
        ["tar", "-czf", str(tarball), "-C", str(BUNDLES), tier_name],
        check=True,
    )
    size_mb = tarball.stat().st_size / (1024 * 1024)
    print(f"  done: {tarball} ({size_mb:.1f} MB)")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(
            "usage: bundle_tier_for_delivery.py <tier>\n"
            "tiers: volume-10, volume-100, volume-500, volume-1000, "
            "volume-5000, format-adversarial",
        )
    for tier in sys.argv[1:]:
        bundle(tier)


if __name__ == "__main__":
    main()
