"""Phase 4.3 — German federal statute ingestion feed.

Modes:

* **Dry-run** (default) — fetch the TOC, categorise every law, validate the
  registry, optionally download a sample. Writes nothing.
* **Ingest one** (``--ingest <slug>``) — fetch + parse + chunk + embed +
  transactionally upsert one law into the live ``corpus_*`` tables.
* **Backfill** (``--backfill mapped|all [--limit N]``) — same per-law work
  but iterated; one shared HTTP client (no per-law TOC re-fetch); per-law
  try/except so a bad XML never aborts the batch. Idempotent via
  ``content_hash`` — unchanged laws skip cheaply.
* **Prune removed** (``--prune-removed [--missing-days N]``) — DELETE laws
  whose ``statute_feed_state.last_seen`` is older than ``N`` days AND that
  are absent from the current TOC. Conservative window guards against a
  transient TOC fetch error wiping rows.
* **Status** (``--status``) — print the feed-state summary (laws per domain,
  corpus row counts, last-seen distribution).

    python -m lai.pipeline.statute_feed                       # dry-run summary
    python -m lai.pipeline.statute_feed --ingest bimschg      # one law live
    python -m lai.pipeline.statute_feed --backfill mapped     # the 29 mapped laws
    python -m lai.pipeline.statute_feed --backfill all --limit 50  # smoke-test full
    python -m lai.pipeline.statute_feed --prune-removed       # default 7-day window
    python -m lai.pipeline.statute_feed --status              # current feed state
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

from lai.common.connectors._gii_parser import LawRef, parse_law_xml
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
# DB connection (LIVE writes — Phase 4.3 Phase B/C)
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def _pg_conn() -> Iterator[psycopg2.extensions.connection]:
    """Open a Postgres connection to ``lai_db`` with the pgvector adapter on.

    Reads PG* env vars (PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD); also
    honours the migrate_corpus DB_* names. ``PGPASSWORD`` has no default —
    source ``LAI/micro-services/.env`` first.
    """
    pw = os.environ.get("PGPASSWORD") or os.environ.get("DB_PASSWORD")
    if not pw:
        raise RuntimeError("PGPASSWORD / DB_PASSWORD not set in env — source LAI/micro-services/.env before running.")
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


# ─────────────────────────────────────────────────────────────────────────────
# Ingest one law (the unit of work both --ingest and the backfills reuse)
# ─────────────────────────────────────────────────────────────────────────────


def _ingest_one(law: LawRef, client: GesetzeImInternetClient) -> int:
    """Fetch + parse + chunk + embed + transactionally upsert one resolved law.

    ``client`` MUST be an already-open ``GesetzeImInternetClient`` — the
    backfill loops pass the same one across many laws to avoid re-fetching
    the TOC per law. Returns 0 on success or [skip]; non-zero on a soft
    error (no sections, no parents).
    """
    t0 = time.monotonic()
    slug = law.slug.lower()
    print(f"[info] fetching {slug}: {law.title[:90]}")
    xml = client.fetch_law_xml(law)

    parsed = parse_law_xml(xml)
    if not parsed.sections:
        print(f"[error] {slug}: parsed law has no sections")
        return 3

    doc_id = stable_doc_id(slug)
    new_hash = content_hash(parsed)
    domain = categorize(slug)
    source_url = law.xml_url

    # Idempotency check — skip if upstream hash is unchanged.
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

    # Chunk via the existing pipeline.
    segments = segments_from_parsed_law(parsed)
    doc = {"doc_id": doc_id, "segments": segments}
    parents, children_per_parent = process_document(doc)
    if not parents:
        print(f"[error] {slug}: chunking produced no parents")
        return 4
    total_children = sum(len(c) for c in children_per_parent)
    print(f"[info] {slug}: {len(segments)} segments → {len(parents)} parents → {total_children} children")

    # Embed every child text once; same order as the flattened children.
    flat_children: list[str] = [child["text"] for child_list in children_per_parent for child in child_list]
    print(f"[info] embedding {len(flat_children)} children via {EMBED_URL}")
    raw_embeddings = embed_batch(
        flat_children,
        embed_url=EMBED_URL,
        embed_model=EMBED_MODEL,
        batch_size=32,
        timeout=120.0,
    )
    embeddings_fp16 = [np.array(vec, dtype=np.float16)[:INDEX_DIM] for vec in raw_embeddings]

    # Transactional upsert. Per-law DELETE-then-INSERT: queries see either the
    # old or the new version, never a half-applied state.
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


def _run_ingest(slug: str) -> int:
    """Thin wrapper around :func:`_ingest_one` for the ``--ingest <slug>`` CLI."""
    slug = slug.lower()
    with GesetzeImInternetClient() as client:
        laws = client.list_laws()
        target = next((law for law in laws if law.slug == slug), None)
        if target is None:
            print(f"[error] slug '{slug}' not found in the live TOC")
            return 2
        return _ingest_one(target, client)


# ─────────────────────────────────────────────────────────────────────────────
# Backfill helpers (shared loop)
# ─────────────────────────────────────────────────────────────────────────────


def _backfill_loop(laws: list[LawRef], client: GesetzeImInternetClient, label: str) -> int:
    """Run :func:`_ingest_one` over every ``law`` in order, with per-law
    try/except + a final summary. Shared by ``--backfill mapped|all``."""
    t0 = time.monotonic()
    print(f"[info] backfill ({label}): {len(laws)} laws (sequential)")
    ok: list[str] = []
    failed: list[tuple[str, str]] = []
    for i, law in enumerate(laws, 1):
        print(f"\n[{i}/{len(laws)}] --- {law.slug} ---")
        try:
            rc = _ingest_one(law, client)
        except Exception as exc:  # one bad law must not abort the whole batch
            print(f"[error] {law.slug}: {type(exc).__name__}: {exc}")
            failed.append((law.slug, f"{type(exc).__name__}: {str(exc)[:200]}"))
            continue
        if rc == 0:
            ok.append(law.slug)
        else:
            failed.append((law.slug, f"exit_code={rc}"))

    elapsed = time.monotonic() - t0
    print(f"\n=== backfill {label} summary ===")
    print(f"  total:    {len(laws)}")
    print(f"  ok:       {len(ok)}")
    print(f"  failed:   {len(failed)}")
    print(f"  elapsed:  {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    if failed:
        print(f"\nfailed laws ({len(failed)}):")
        for slug, msg in failed[:50]:
            print(f"  {slug:24s} {msg}")
        if len(failed) > 50:
            print(f"  … and {len(failed) - 50} more")
        return 1
    return 0


def _run_backfill_mapped() -> int:
    """Sequentially ingest the registry-mapped wind-relevant laws."""
    wanted = sorted(mapped_slugs())
    with GesetzeImInternetClient() as client:
        toc = list(client.list_laws())
        slug_to_law = {law.slug: law for law in toc}
        laws = [slug_to_law[s] for s in wanted if s in slug_to_law]
        if len(laws) < len(wanted):
            missing = sorted(set(wanted) - set(slug_to_law))
            print(f"[warn] {len(missing)} registry slug(s) absent from TOC: {', '.join(missing)}")
        return _backfill_loop(laws, client, "mapped")


def _run_backfill_all(*, limit: int | None) -> int:
    """Sequentially ingest every law in the TOC, optionally capped at ``limit``."""
    with GesetzeImInternetClient() as client:
        laws = list(client.list_laws())
        if limit is not None and limit > 0:
            laws = laws[:limit]
            print(f"[info] --limit {limit} applied; processing first {len(laws)} of TOC")
        return _backfill_loop(laws, client, f"all (n={len(laws)})")


# ─────────────────────────────────────────────────────────────────────────────
# Prune removed laws
# ─────────────────────────────────────────────────────────────────────────────


def _run_prune_removed(*, missing_days: int) -> int:
    """Delete laws whose ``last_seen`` is older than ``missing_days`` AND that
    are absent from the current TOC.

    Conservative two-condition guard: a transient TOC fetch error cannot
    delete corpus rows, because ``last_seen`` is bumped on every successful
    sighting (even no-op skips). A row only falls into the prune set if it
    has been missing for ``missing_days`` consecutive runs.
    """
    with GesetzeImInternetClient() as client:
        current_slugs = {law.slug for law in client.list_laws()}

    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, doc_id, last_seen FROM statute_feed_state WHERE last_seen < NOW() - INTERVAL '%s days'",
            (missing_days,),
        )
        candidates = cur.fetchall()
        to_prune = [(slug, doc_id, last_seen) for slug, doc_id, last_seen in candidates if slug not in current_slugs]
        print(
            f"[info] prune-removed: {len(candidates)} state rows older than "
            f"{missing_days} days; {len(to_prune)} also absent from current TOC"
        )
        if not to_prune:
            print("[ok] nothing to prune")
            return 0

        total_parents = 0
        for slug, doc_id, last_seen in to_prune:
            cur.execute(
                "DELETE FROM corpus_parent_chunks WHERE doc_id = %s",
                (doc_id,),
            )
            n_parents = cur.rowcount
            total_parents += n_parents
            cur.execute("DELETE FROM statute_feed_state WHERE slug = %s", (slug,))
            print(f"  pruned {slug:24s} doc_id={doc_id} parents={n_parents} last_seen={last_seen}")
        conn.commit()

    print(f"[ok] pruned {len(to_prune)} laws ({total_parents} parents removed)")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────


def _run_status() -> int:
    """Print the current feed state — laws per domain, corpus row counts,
    last-seen distribution."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM statute_feed_state")
        n_state = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM corpus_parent_chunks WHERE id >= 9000000000")
        n_parents = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM corpus_child_chunks WHERE id >= 9000000000")
        n_children = cur.fetchone()[0]
        cur.execute("SELECT MIN(last_seen), MAX(last_seen) FROM statute_feed_state")
        seen_min, seen_max = cur.fetchone()
        cur.execute("SELECT MIN(last_changed), MAX(last_changed) FROM statute_feed_state")
        chg_min, chg_max = cur.fetchone()
        cur.execute(
            "SELECT domain, COUNT(*), COALESCE(SUM(n_sections), 0) "
            "FROM statute_feed_state GROUP BY domain ORDER BY COUNT(*) DESC, domain"
        )
        by_domain = cur.fetchall()

    print("=== statute feed status ===")
    print(f"  state rows           : {n_state}")
    print(f"  corpus parents (≥9e9): {n_parents}")
    print(f"  corpus children(≥9e9): {n_children}")
    print(f"  last_seen   range    : {seen_min}  →  {seen_max}")
    print(f"  last_changed range   : {chg_min}  →  {chg_max}")
    print("\nby domain:")
    print(f"  {'domain':24s} {'laws':>6} {'sections':>10}")
    for domain, n_laws, n_secs in by_domain:
        print(f"  {domain:24s} {n_laws:>6} {n_secs:>10}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


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
        help="(with dry-run) also download + parse a sample to validate the parse.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap the law count. With --fetch-sections: sample size (default 5). "
        "With --backfill all: cap the backfill (default: no cap).",
    )
    parser.add_argument(
        "--ingest",
        metavar="SLUG",
        help="ingest one law (LIVE write to corpus_*). Requires PGPASSWORD env.",
    )
    parser.add_argument(
        "--backfill",
        choices=["mapped", "all"],
        help="bulk ingest. 'mapped': the 29 registry-mapped wind-relevant laws; "
        "'all': every law in the TOC (combine with --limit N to cap). LIVE write.",
    )
    parser.add_argument(
        "--prune-removed",
        action="store_true",
        help="DELETE corpus rows for laws missing from the TOC + with "
        "last_seen older than --missing-days. LIVE delete.",
    )
    parser.add_argument(
        "--missing-days",
        type=int,
        default=7,
        help="how long a law must be missing from the TOC before --prune-removed deletes it (default 7).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print the current feed state — laws per domain, row counts.",
    )
    args = parser.parse_args(argv)

    if args.status:
        return _run_status()
    if args.prune_removed:
        return _run_prune_removed(missing_days=args.missing_days)
    if args.backfill == "mapped":
        return _run_backfill_mapped()
    if args.backfill == "all":
        return _run_backfill_all(limit=args.limit)
    if args.ingest:
        return _run_ingest(slug=args.ingest)
    # Dry-run is the default; --fetch-sections default-limit is 5 when --limit not set.
    fetch_limit = args.limit if args.limit is not None else 5
    return _run_dry(fetch_sections=args.fetch_sections, limit=fetch_limit)


if __name__ == "__main__":
    raise SystemExit(main())
