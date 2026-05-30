#!/usr/bin/env python3
"""Fetch one German federal law from gesetze-im-internet.de and write per-§ files.

Roadmap Phase 4 (ingestion feed) / PROGRESS_V2 vm-5.

The Phase 3 architecture is **"RAG = current statute, fine-tune = reasoning"**:
the RAG corpus needs an authoritative, re-fetchable source for the laws we
answer questions about. This script is the first brick of that source — a
small, idempotent fetcher for **one law at a time**, starting with BImSchG.
The Phase-A library code (``lai.common.connectors.gesetze`` +
``_gii_parser``) and the dry-run TOC tool
(``python -m lai.pipeline.statute_feed``) already exist; this script is
deliberately a thin disk-writer wrapper, not a re-implementation:

* :class:`~lai.common.connectors.gesetze.GesetzeImInternetClient` does the
  HTTP fetch + unzip with the production retry / metrics / structured-log
  discipline.
* :func:`~lai.common.connectors._gii_parser.parse_law_xml` does the XML
  parsing (defusedxml-hardened, pure, unit-tested).

What this script adds on top is just durable, reviewable on-disk output:

  data/statutes/<slug>/
      meta.json                 # law-level metadata + per-section index
      sections/
          0000_eingangsformel.json
          0001_§-1.json
          0002_§-2.json
          ...

Each section file is one citable unit (typically one ``§``) with its enbez,
title, flattened body text, and a sha256 of the body. ``meta.json`` carries
the law-level abbreviation (jurabk), long title, source URL, fetched_at, an
xml.zip-level sha256 (the fast-path skip key), and the section index. The
layout is the same shape Phase B's chunker + embedder will read from so the
write path lands later without a re-fetch.

Idempotency
-----------
On re-run the script downloads the law's xml.zip and hashes it. If the hash
matches the existing ``meta.json``'s ``xml_sha256``, nothing is written and
the script exits cleanly. ``--force`` overrides the skip. The on-disk layout
is recomputed atomically (write to a sibling temp dir, then swap) so a crash
mid-write never leaves a partial state under the canonical path.

Usage
-----
    # default: fetch BImSchG into LAI/data/statutes/bimschg/
    python3 LAI/scripts/ingest/fetch_gesetze.py

    # extend to another law — see the slug column of `gii-toc.xml`:
    python3 LAI/scripts/ingest/fetch_gesetze.py --slug baugb
    python3 LAI/scripts/ingest/fetch_gesetze.py --slug eeg_2023

    # force a re-fetch + re-write even if the xml hasn't changed:
    python3 LAI/scripts/ingest/fetch_gesetze.py --slug bimschg --force

Exit codes
----------
  0  fetched + wrote, or already up-to-date
  1  configuration error (bad --slug / --out)
  2  network or parse failure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from lai.common.connectors._gii_parser import (
    ParsedLaw,
    StatuteSection,
    parse_law_xml,
)
from lai.common.connectors.config import GesetzeConfig
from lai.common.connectors.exceptions import GesetzeError
from lai.common.connectors.gesetze import GesetzeImInternetClient

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_FETCH = 2

# Default output root. The script writes per-law subdirectories underneath it.
# Two levels up from scripts/ingest/ lands on the repo LAI/ directory.
_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "statutes"

# A law slug as gesetze-im-internet.de uses it: lowercase, alphanumerics +
# underscores. Restrictive on purpose — a typo turns into a 404 instead of a
# write to an unexpected path.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")

# Sanitiser for an enbez ("§ 1", "§ 1a", "Inhaltsübersicht", ...) into a
# filesystem-safe slug. Drops the "§ " prefix and lowercases the rest, replaces
# any run of non-alphanumerics with a single hyphen. Keeps the result readable
# enough that an ops engineer can eyeball ``0042_12-absatz-3.json`` and know
# what they're looking at.
_ENBEZ_PUNCT = re.compile(r"[^a-z0-9]+")


def _sanitise_enbez(enbez: str | None, titel: str | None) -> str:
    raw = enbez or titel or "untitled"
    raw = raw.replace("§", "").strip().lower()
    cleaned = _ENBEZ_PUNCT.sub("-", raw).strip("-")
    return cleaned or "untitled"


def _section_to_dict(
    section: StatuteSection,
    *,
    seq: int,
    law_slug: str,
    jurabk: str | None,
    fetched_at: str,
) -> dict[str, object]:
    body_sha = hashlib.sha256(section.text.encode("utf-8")).hexdigest()
    return {
        "seq": seq,
        "law_slug": law_slug,
        "jurabk": jurabk,
        "enbez": section.enbez,
        "titel": section.titel,
        "text": section.text,
        "sha256": body_sha,
        "fetched_at": fetched_at,
    }


def _build_meta(
    *,
    law_slug: str,
    parsed: ParsedLaw,
    source_url: str,
    xml_sha: str,
    fetched_at: str,
    section_files: list[tuple[int, str, StatuteSection]],
) -> dict[str, object]:
    return {
        "slug": law_slug,
        "jurabk": parsed.jurabk,
        "long_title": parsed.long_title,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "xml_sha256": xml_sha,
        "n_sections": len(section_files),
        "n_paragraphs": sum(1 for _, _, s in section_files if s.enbez and s.enbez.startswith("§")),
        "sections": [
            {
                "seq": seq,
                "filename": fname,
                "enbez": section.enbez,
                "titel": section.titel,
            }
            for seq, fname, section in section_files
        ],
    }


def _existing_xml_sha(law_dir: Path) -> str | None:
    meta_path = law_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sha = meta.get("xml_sha256")
    return sha if isinstance(sha, str) else None


def _write_law(
    out_root: Path,
    *,
    law_slug: str,
    parsed: ParsedLaw,
    source_url: str,
    xml_sha: str,
) -> int:
    """Write meta.json + sections/ atomically. Returns sections written."""
    law_dir = out_root / law_slug
    # Atomic-ish swap: build the new tree in a sibling temp dir, then swap. A
    # crash before the swap leaves the canonical path untouched; after the swap
    # the previous tree is already replaced. We never hold an empty law_dir.
    tmp_dir = out_root / f".{law_slug}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    sections_dir = tmp_dir / "sections"
    sections_dir.mkdir(parents=True)

    fetched_at = datetime.now(UTC).isoformat()
    section_files: list[tuple[int, str, StatuteSection]] = []
    for seq, section in enumerate(parsed.sections):
        fname = f"{seq:04d}_{_sanitise_enbez(section.enbez, section.titel)}.json"
        payload = _section_to_dict(
            section,
            seq=seq,
            law_slug=law_slug,
            jurabk=parsed.jurabk,
            fetched_at=fetched_at,
        )
        (sections_dir / fname).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        section_files.append((seq, fname, section))

    meta = _build_meta(
        law_slug=law_slug,
        parsed=parsed,
        source_url=source_url,
        xml_sha=xml_sha,
        fetched_at=fetched_at,
        section_files=section_files,
    )
    (tmp_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Swap: rotate the existing tree to a backup name, move tmp into place,
    # then drop the backup. ``Path.replace`` would race with readers; the
    # rename → rename → rmtree dance keeps the canonical path readable through
    # the swap (a reader either sees the old or the new — never a mix).
    backup_dir: Path | None = None
    if law_dir.exists():
        backup_dir = out_root / f".{law_slug}.old"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        law_dir.rename(backup_dir)
    tmp_dir.rename(law_dir)
    if backup_dir is not None:
        shutil.rmtree(backup_dir)
    return len(section_files)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch_gesetze.py",
        description=(
            "Fetch one German federal law from gesetze-im-internet.de and "
            "write per-§ JSON files. Idempotent: skips when the xml.zip "
            "hash already matches what's on disk."
        ),
    )
    parser.add_argument(
        "--slug",
        default="bimschg",
        help="Law slug as used by gesetze-im-internet.de (default 'bimschg').",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output root (default {_DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-write even if the xml.zip hash matches the existing meta.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    slug = args.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        print(f"error: --slug {args.slug!r} is not a valid GII slug", file=sys.stderr)
        return EXIT_CONFIG

    args.out.mkdir(parents=True, exist_ok=True)
    law_dir = args.out / slug

    # Build the xml.zip URL directly from the connector config — fetch_law_xml
    # accepts a URL string, so we don't have to walk the entire ~6.5k-law TOC
    # just to discover one law. Honours ``LAI_GESETZE_BASE_URL`` if set.
    config = GesetzeConfig()
    source_url = f"{config.base_url.rstrip('/')}/{slug}/xml.zip"

    with GesetzeImInternetClient(config) as client:
        try:
            xml_bytes = client.fetch_law_xml(source_url)
        except GesetzeError as exc:
            print(f"error: fetch failed for {source_url}: {exc}", file=sys.stderr)
            return EXIT_FETCH

    xml_sha = hashlib.sha256(xml_bytes).hexdigest()
    if not args.force and _existing_xml_sha(law_dir) == xml_sha:
        print(f"up-to-date: {law_dir} (xml_sha256 {xml_sha[:12]}…) — use --force to re-write.")
        return EXIT_OK

    try:
        parsed = parse_law_xml(xml_bytes)
    except Exception as exc:  # parse_law_xml raises ElementTree.ParseError
        print(f"error: parse failed for {source_url}: {exc}", file=sys.stderr)
        return EXIT_FETCH

    n = _write_law(
        args.out,
        law_slug=slug,
        parsed=parsed,
        source_url=source_url,
        xml_sha=xml_sha,
    )
    paragraphs = sum(1 for s in parsed.sections if s.enbez and s.enbez.startswith("§"))
    print(f"wrote {n} section(s) ({paragraphs} §) to {law_dir} (jurabk={parsed.jurabk}, xml_sha256={xml_sha[:12]}…)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
