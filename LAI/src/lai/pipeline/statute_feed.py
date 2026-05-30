"""Phase 4.3 — German federal statute ingestion feed.

Two modes:

* **Dry-run** (default) — fetches the table of contents, categorises every law,
  validates registry slugs, and optionally downloads a sample to prove the
  parse. Writes nothing.
* **Ingest** (``--ingest <slug>``) — fetches one law, parses it, chunks via
  :mod:`lai.pipeline.chunk`, embeds via :mod:`lai.pipeline.embed` (truncated
  4096 → 4000 to match the corpus's halfvec dim), and **transactionally
  upserts** into the live ``corpus_*`` tables. Idempotent: re-running with no
  upstream change is a no-op via the content hash in ``statute_feed_state``.

    python -m lai.pipeline.statute_feed                    # dry-run summary
    python -m lai.pipeline.statute_feed --fetch-sections   # + validate parse
    python -m lai.pipeline.statute_feed --ingest bimschg   # LIVE write to corpus_*
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector

from lai.common.connectors._gii_parser import parse_law_xml
from lai.common.connectors.gesetze import GesetzeImInternetClient
from lai.common.connectors.statute_categories import (
    DEFAULT_DOMAIN,
    categorize,
    mapped_slugs,
)
from lai.common.connectors.statute_ingest import (
    content_hash,
    segments_from_parsed_law,
    stable_chunk_id,
    stable_doc_id,
)
from lai.pipeline.chunk import process_document
from lai.pipeline.embed import embed_batch

# pgvector caps halfvec HNSW indexes at 4000 dims; truncate Qwen3-Embedding's
# 4096-d output to match what ``corpus_child_chunks.embedding halfvec(4000)``
# accepts. See ``LAI/scripts/ops/migrate_corpus.py:_blob_to_halfvec`` for the
# same truncation on the migrated corpus.
INDEX_DIM = 4000
EMBED_URL = os.environ.get("LAI_EMBED_URL", "http://localhost:8003")
EMBED_MODEL = os.environ.get("LAI_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run (read-only)
# ─────────────────────────────────────────────────────────────────────────────


def _run_dry(*, fetch_sections: bool, limit: int) -> int:
    with GesetzeImInternetClient() as client:
        laws = client.list_laws()

        by_domain: Counter[str] = Counter(categorize(law.slug) for law in laws)
        total = len(laws)
        catch_all = by_domain.get(DEFAULT_DOMAIN, 0)

        print(f"gesetze-im-internet.de TOC: {total} laws")
        print(f"  explicitly categorised : {total - catch_all}")
        print(f"  {DEFAULT_DOMAIN} (catch-all)    : {catch_all}")
        print("\nby domain:")
        for domain, n in sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {domain:24s} {n:>6}")

        toc_slugs = {law.slug for law in laws}
        missing = sorted(mapped_slugs() - toc_slugs)
        if missing:
            print(f"\n[warn] {len(missing)} registry slug(s) NOT in the TOC: {', '.join(missing)}")
        else:
            print("\n[ok] every registry slug exists in the TOC")

        if fetch_sections:
            sample = [law for law in laws if categorize(law.slug) != DEFAULT_DOMAIN][:limit]
            print(f"\nfetch-sections sample ({len(sample)} laws):")
            for law in sample:
                try:
                    parsed = parse_law_xml(client.fetch_law_xml(law))
                except Exception as exc:  # dry-run continues past a bad/transient law
                    print(f"  {law.slug:16s} FAILED: {exc}")
                    continue
                paragraphs = sum(1 for s in parsed.sections if s.enbez and s.enbez.startswith("§"))
                print(
                    f"  {law.slug:16s} {categorize(law.slug):22s} "
                    f"jurabk={parsed.jurabk} sections={len(parsed.sections)} §={paragraphs}"
                )

    print("\nDRY RUN — nothing written to the corpus.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Ingest one law (LIVE write to corpus_* — Phase 4.3 Phase B)
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def _pg_conn() -> Iterator[psycopg2.extensions.connection]:
    """Open a Postgres connection to ``lai_db`` with the pgvector adapter on.

    Reads PG* env vars (PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD); also
    honours the migrate_corpus DB_* names. ``PGPASSWORD`` has no default —
    source ``LAI/.env.auth`` first.
    """
    pw = os.environ.get("PGPASSWORD") or os.environ.get("DB_PASSWORD")
    if not pw:
        raise RuntimeError("PGPASSWORD / DB_PASSWORD not set in env — source LAI/.env.auth before running --ingest.")
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", os.environ.get("DB_HOST", "127.0.0.1")),
        port=int(os.environ.get("PGPORT", os.environ.get("DB_PORT", "5434"))),
        dbname=os.environ.get("PGDATABASE", os.environ.get("DB_NAME", "lai_db")),
        user=os.environ.get("PGUSER", os.environ.get("DB_USER", "lai_user")),
        password=pw,
        connect_timeout=30,
        application_name="lai.statute_feed",
    )
    try:
        register_vector(conn)
        yield conn
    finally:
        conn.close()


def _run_ingest(slug: str) -> int:
    """Fetch + parse + chunk + embed + transactionally upsert one law."""
    t0 = time.monotonic()
    slug = slug.lower()

    # 1. Fetch via the connector. Resolve the LawRef (so we also capture the
    # human title + canonical URL for the metadata).
    with GesetzeImInternetClient() as client:
        laws = client.list_laws()
        target = next((law for law in laws if law.slug == slug), None)
        if target is None:
            print(f"[error] slug '{slug}' not found in the live TOC")
            return 2
        print(f"[info] fetching {slug}: {target.title[:90]}")
        xml = client.fetch_law_xml(target)

    # 2. Parse
    parsed = parse_law_xml(xml)
    if not parsed.sections:
        print(f"[error] {slug}: parsed law has no sections")
        return 3

    doc_id = stable_doc_id(slug)
    new_hash = content_hash(parsed)
    domain = categorize(slug)
    source_url = target.xml_url

    # 3. Idempotency check — skip if upstream hash is unchanged.
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM statute_feed_state WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if row is not None and row[0] == new_hash:
            cur.execute(
                "UPDATE statute_feed_state SET last_seen = NOW() WHERE slug = %s",
                (slug,),
            )
            conn.commit()
            print(f"[skip] {slug}: unchanged (hash {new_hash[:12]}…) — {time.monotonic() - t0:.1f}s")
            return 0

    # 4. Build segments + chunk via the existing pipeline.
    segments = segments_from_parsed_law(parsed)
    doc = {"doc_id": doc_id, "segments": segments}
    parents, children_per_parent = process_document(doc)
    if not parents:
        print(f"[error] {slug}: chunking produced no parents")
        return 4
    total_children = sum(len(c) for c in children_per_parent)
    print(f"[info] {slug}: {len(segments)} segments → {len(parents)} parents → {total_children} children")

    # 5. Embed every child text once; same order as the flattened children.
    flat_children: list[str] = [child["text"] for child_list in children_per_parent for child in child_list]
    print(f"[info] embedding {len(flat_children)} children via {EMBED_URL}")
    raw_embeddings = embed_batch(
        flat_children,
        embed_url=EMBED_URL,
        embed_model=EMBED_MODEL,
        batch_size=32,
        timeout=120.0,
    )
    # 4096 fp32 → fp16 → first 4000 dims (matches migrate_corpus._blob_to_halfvec).
    embeddings_fp16 = [np.array(vec, dtype=np.float16)[:INDEX_DIM] for vec in raw_embeddings]

    # 6. Transactional upsert into corpus_*. Per-law DELETE-then-INSERT keeps
    # retrieval consistent: queries either see the old version or the new one,
    # never a half-applied state.
    metadata_json = json.dumps(
        {
            "jurabk": parsed.jurabk,
            "long_title": parsed.long_title,
            "slug": slug,
        }
    )
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM corpus_parent_chunks WHERE doc_id = %s", (doc_id,))
        deleted_parents = cur.rowcount

        parent_ids: list[int] = []
        for i, parent in enumerate(parents):
            section = str(parent.get("section") or "Allgemein")
            cur.execute("SELECT nextval('corpus_feed_id_seq')")
            pid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO corpus_parent_chunks
                   (id, doc_id, chunk_id, section, content, char_count,
                    language, doc_type, source_file, source_bucket, domain,
                    page_start, page_end, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s,
                           %s, %s, %s::jsonb)""",
                (
                    pid,
                    doc_id,
                    stable_chunk_id(slug, section, i, kind="p"),
                    section,
                    parent["text"],
                    parent["char_count"],
                    "de",
                    "gesetz",
                    source_url,
                    "gesetze-im-internet.de",
                    domain,
                    None,
                    None,
                    metadata_json,
                ),
            )
            parent_ids.append(pid)

        child_offset = 0
        for pidx, child_list in enumerate(children_per_parent):
            pid = parent_ids[pidx]
            section = str(parents[pidx].get("section") or "Allgemein")
            for c_idx, child in enumerate(child_list):
                cur.execute("SELECT nextval('corpus_feed_id_seq')")
                cid = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO corpus_child_chunks
                       (id, parent_id, chunk_id, content, embedding, char_count)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        cid,
                        pid,
                        stable_chunk_id(slug, f"{section}#p{pidx}", c_idx, kind="c"),
                        child["text"],
                        embeddings_fp16[child_offset],
                        child["char_count"],
                    ),
                )
                child_offset += 1

        cur.execute(
            """INSERT INTO statute_feed_state
               (slug, source_url, jurabk, doc_id, content_hash, domain, n_sections,
                last_seen, last_changed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
               ON CONFLICT (slug) DO UPDATE SET
                 source_url   = EXCLUDED.source_url,
                 jurabk       = EXCLUDED.jurabk,
                 content_hash = EXCLUDED.content_hash,
                 domain       = EXCLUDED.domain,
                 n_sections   = EXCLUDED.n_sections,
                 last_seen    = NOW(),
                 last_changed = NOW()""",
            (slug, source_url, parsed.jurabk, doc_id, new_hash, domain, len(parsed.sections)),
        )

        conn.commit()

    print(
        f"[ok] {slug}: ingested {len(parents)} parents + {total_children} children "
        f"(replaced {deleted_parents} old parents) — {time.monotonic() - t0:.1f}s"
    )
    return 0


def _run_backfill_mapped() -> int:
    """Sequentially ingest the registry-mapped wind-relevant laws.

    Per-law try/except: one bad XML or transient failure does NOT abort the
    batch. Idempotent: laws with unchanged content_hash skip in ~1-2 s each,
    so re-runs are cheap. Returns 0 if every law succeeded, 1 if any failed.
    """
    t0 = time.monotonic()
    slugs = sorted(mapped_slugs())
    print(f"[info] backfill: {len(slugs)} mapped laws (sequential)")

    ok: list[str] = []
    failed: list[tuple[str, str]] = []
    for i, slug in enumerate(slugs, 1):
        print(f"\n[{i}/{len(slugs)}] --- {slug} ---")
        try:
            rc = _run_ingest(slug)
        except Exception as exc:  # one bad law must not abort the whole batch
            print(f"[error] {slug}: {type(exc).__name__}: {exc}")
            failed.append((slug, f"{type(exc).__name__}: {str(exc)[:200]}"))
            continue
        if rc == 0:
            ok.append(slug)
        else:
            failed.append((slug, f"exit_code={rc}"))

    elapsed = time.monotonic() - t0
    print("\n=== backfill mapped summary ===")
    print(f"  total:    {len(slugs)}")
    print(f"  ok:       {len(ok)}")
    print(f"  failed:   {len(failed)}")
    print(f"  elapsed:  {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    if failed:
        print(f"\nfailed laws ({len(failed)}):")
        for slug, msg in failed:
            print(f"  {slug:18s} {msg}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="statute-feed",
        description="German federal statute ingestion feed (Phase 4.3).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(default) report what would be ingested; write nothing.",
    )
    parser.add_argument(
        "--fetch-sections",
        action="store_true",
        help="also download + parse a sample of mapped laws to validate the parse.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="number of laws to fetch with --fetch-sections (default 5).",
    )
    parser.add_argument(
        "--ingest",
        metavar="SLUG",
        help="ingest one law (LIVE write to corpus_*). Requires PGPASSWORD env.",
    )
    parser.add_argument(
        "--backfill",
        choices=["mapped"],
        help="bulk ingest. 'mapped': the 29 registry-mapped wind-relevant laws "
        "(LIVE write; idempotent — unchanged laws skip cheaply).",
    )
    args = parser.parse_args(argv)
    if args.backfill == "mapped":
        return _run_backfill_mapped()
    if args.ingest:
        return _run_ingest(slug=args.ingest)
    return _run_dry(fetch_sections=args.fetch_sections, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
