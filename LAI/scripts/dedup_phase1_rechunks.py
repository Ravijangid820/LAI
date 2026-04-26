"""
Remove duplicate parent_chunks (and their children + embeddings) created when
Phase 2 Step 2 inadvertently re-chunked Phase 1 source files.

Phase 1 chunked: VDRs, /de/gesetzes, DD reports, fachbuch (134K parents,
                 ids 1..173966)
Phase 2 chunked: same source_files AGAIN (134K duplicate parents,
                 ids 100M..100173966), plus the new corpora (mlp/gerdalir/etc).

The duplicates are byte-for-byte similar to Phase 1 chunks, sit in the
search pool, and compete for top-K slots. They displaced ~5pt of R@1 on
the val benchmark.

Strategy: keep the OLD parent (referenced by training_samples.parent_id);
drop the NEW duplicate.

Order matters:
  1. delete child_embeddings of duplicate children
  2. delete duplicate child_chunks
  3. delete duplicate parent_chunks

Usage:
    python scripts/dedup_phase1_rechunks.py             # dry-run
    python scripts/dedup_phase1_rechunks.py --apply     # perform
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "processed" / "pipeline_local.db"
OLD_RANGE_MAX = 200_000
NEW_RANGE_MIN = 100_000_000


def status(conn: sqlite3.Connection) -> dict:
    counts: dict[str, int] = {}
    for t in ("parent_chunks", "child_chunks", "child_embeddings"):
        counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=MEMORY")

    print("Before:")
    for k, v in status(conn).items():
        print(f"  {k:<20s}  {v:>14,}")

    # The duplicate parents: same source_file as a Phase 1 parent, but in new id range.
    # Materialize the set into a temp table so subsequent DELETEs are fast.
    print("\nIdentifying duplicates...")
    t0 = time.time()
    conn.execute("DROP TABLE IF EXISTS dup_parents")
    conn.execute("""
        CREATE TEMP TABLE dup_parents AS
        SELECT id FROM parent_chunks
        WHERE id >= ?
          AND source_file IN (SELECT source_file FROM parent_chunks WHERE id < ?)
    """, (NEW_RANGE_MIN, OLD_RANGE_MAX))
    n_dup_parents = conn.execute("SELECT COUNT(*) FROM dup_parents").fetchone()[0]
    print(f"  found {n_dup_parents:,} duplicate parents in {time.time()-t0:.1f}s")

    # Materialize duplicate children
    conn.execute("DROP TABLE IF EXISTS dup_children")
    conn.execute("""
        CREATE TEMP TABLE dup_children AS
        SELECT id FROM child_chunks
        WHERE parent_id IN (SELECT id FROM dup_parents)
    """)
    n_dup_children = conn.execute("SELECT COUNT(*) FROM dup_children").fetchone()[0]
    n_dup_emb = conn.execute("""
        SELECT COUNT(*) FROM child_embeddings
        WHERE child_id IN (SELECT id FROM dup_children)
    """).fetchone()[0]
    print(f"  {n_dup_children:,} duplicate children  ({n_dup_emb:,} already embedded)")

    if not args.apply:
        print("\n(dry-run; pass --apply to delete)")
        return

    print("\nDeleting...")
    t0 = time.time()
    n = conn.execute(
        "DELETE FROM child_embeddings WHERE child_id IN (SELECT id FROM dup_children)"
    ).rowcount
    conn.commit()
    print(f"  child_embeddings: -{n:,} rows in {time.time()-t0:.1f}s")

    t0 = time.time()
    n = conn.execute(
        "DELETE FROM child_chunks WHERE id IN (SELECT id FROM dup_children)"
    ).rowcount
    conn.commit()
    print(f"  child_chunks:     -{n:,} rows in {time.time()-t0:.1f}s")

    t0 = time.time()
    n = conn.execute(
        "DELETE FROM parent_chunks WHERE id IN (SELECT id FROM dup_parents)"
    ).rowcount
    conn.commit()
    print(f"  parent_chunks:    -{n:,} rows in {time.time()-t0:.1f}s")

    print("\nCheckpointing WAL...")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    print("\nAfter:")
    for k, v in status(conn).items():
        print(f"  {k:<20s}  {v:>14,}")


if __name__ == "__main__":
    main()
