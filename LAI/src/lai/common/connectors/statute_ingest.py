"""Pure helpers for the statute ingestion feed (Phase 4.3 Phase B).

Converts a :class:`ParsedLaw` into the chunker-input shape, derives the stable
identifiers used to wire statute rows into the ``corpus_*`` tables, and
computes the content hash the daily feed uses to detect amendments.

All functions here are **pure** (no I/O, no globals) — the DB writer + the
embed call live in :mod:`lai.pipeline.statute_feed`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from lai.common.connectors._gii_parser import ParsedLaw

__all__ = [
    "content_hash",
    "segments_from_parsed_law",
    "stable_chunk_id",
    "stable_doc_id",
]


def stable_doc_id(slug: str) -> str:
    """Deterministic ``doc_id`` for a law — 16 hex chars from ``sha256(slug)``.

    Stable across runs so re-ingesting a law targets the same corpus rows
    (cited references survive amendments — the rows get rebuilt, but the
    ``doc_id`` identifying "this is BImSchG" stays put).
    """
    return hashlib.sha256(slug.lower().encode("utf-8")).hexdigest()[:16]


def content_hash(parsed: ParsedLaw) -> str:
    """sha256 of the law's normalised text — the feed's amendment-detection key.

    Includes ``jurabk`` and every section's ``enbez`` / ``titel`` / ``text`` so
    a renamed paragraph or a body edit flips the hash. Deterministic; the same
    parsed law always hashes to the same value.
    """
    digest = hashlib.sha256()
    if parsed.jurabk:
        digest.update(parsed.jurabk.encode("utf-8"))
        digest.update(b"\n")
    for section in parsed.sections:
        if section.enbez:
            digest.update(section.enbez.encode("utf-8"))
            digest.update(b"\n")
        if section.titel:
            digest.update(section.titel.encode("utf-8"))
            digest.update(b"\n")
        digest.update(section.text.encode("utf-8"))
        digest.update(b"\n---\n")
    return digest.hexdigest()


def segments_from_parsed_law(parsed: ParsedLaw) -> list[dict[str, Any]]:
    """Map ``parsed.sections`` to the segment dicts ``process_document`` expects.

    Each non-empty section becomes one segment carrying its heading + body so
    the German-aware chunker's §/Artikel/Absatz boundary detection fires. The
    ``section`` field tracks the citable designation (``§ 1`` etc.) into the
    parent chunks for retrieval-time citation.
    """
    out: list[dict[str, Any]] = []
    for section in parsed.sections:
        body = section.text
        if not body and not section.titel:
            continue
        if section.titel and body:
            text = f"{section.titel}\n\n{body}"
        else:
            text = section.titel or body
        out.append(
            {
                "text": text,
                "section": section.enbez or section.titel or "Allgemein",
                "type": "text",
            }
        )
    return out


def stable_chunk_id(slug: str, section: str, index: int, *, kind: str) -> str:
    """Deterministic ``chunk_id`` for one parent (``kind='p'``) or child (``kind='c'``).

    Stable so re-ingesting the same law produces the same chunk identifiers,
    which keeps citations and debugging legible across runs.
    """
    raw = f"{slug.lower()}|{section}|{kind}|{index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
