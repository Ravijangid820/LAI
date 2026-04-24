"""
Backfill child_chunks.id / parent_chunks.id / child_chunks.parent_id
for rows with NULL id.

Local (SQLite) mode leaves `id` NULL because the table was declared
`INTEGER` without AUTOINCREMENT. Most code paths (Step 6 cursor,
rag_eval.py, pilot eval) join on `id`, so NULL ids are silently
excluded.

Naive backfill `id = rowid` collides: old `pc.id` ranges 282-173,966
and old `cc.id` ranges 332-217,496, which overlap with new rowids.
child_embeddings has PRIMARY KEY on child_id — a collision would make
embeddings ambiguous between old and new rows.

Fix: backfill with a safe offset (100,000,000) so new ids live in a
disjoint range. Also rewrite cc.parent_id for new rows so it points to
the backfilled pc.id (not pc.rowid).

Usage:
    python scripts/backfill_chunk_ids.py              # dry-run counts
    python scripts/backfill_chunk_ids.py --apply      # perform backfill
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "processed" / "pipeline_local.db"
OFFSET = 100_000_000  # disjoint from all existing Postgres-assigned ids


def status(conn: sqlite3.Connection) -> None:
    for t in ("parent_chunks", "child_chunks"):
        total, null, min_id, max_id = conn.execute(
            f"SELECT COUNT(*),"
            f"       SUM(CASE WHEN id IS NULL THEN 1 ELSE 0 END),"
            f"       MIN(id), MAX(id) FROM {t}"
        ).fetchone()
        print(f"  {t:<16s}  total={total:>12,}  id_NULL={null:>12,}  "
              f"id range=[{min_id}, {max_id}]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Perform the backfill")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    print("Before:")
    status(conn)

    # Sanity: ensure OFFSET is larger than the max existing id in both tables
    max_pc = conn.execute("SELECT MAX(id) FROM parent_chunks").fetchone()[0] or 0
    max_cc = conn.execute("SELECT MAX(id) FROM child_chunks").fetchone()[0] or 0
    if OFFSET <= max(max_pc, max_cc):
        raise SystemExit(
            f"ABORT: OFFSET={OFFSET} but existing max id is {max(max_pc, max_cc)}"
        )

    if not args.apply:
        print(f"\n(dry-run; pass --apply to perform backfill with offset {OFFSET:,})")
        return

    # 1. Backfill parent_chunks.id from rowid + OFFSET
    t0 = time.time()
    n = conn.execute(
        f"UPDATE parent_chunks SET id = rowid + {OFFSET} WHERE id IS NULL"
    ).rowcount
    conn.commit()
    print(f"  parent_chunks: backfilled {n:,} rows in {time.time()-t0:.1f}s")

    # 2. Fix cc.parent_id for new children — they were inserted with
    # pc.rowid (because pc.id was NULL); now pc.id = pc.rowid + OFFSET,
    # so cc.parent_id also needs the offset added.
    t0 = time.time()
    n = conn.execute(
        f"UPDATE child_chunks SET parent_id = parent_id + {OFFSET} "
        f"WHERE id IS NULL"
    ).rowcount
    conn.commit()
    print(f"  child_chunks.parent_id: shifted {n:,} rows in {time.time()-t0:.1f}s")

    # 3. Backfill child_chunks.id
    t0 = time.time()
    n = conn.execute(
        f"UPDATE child_chunks SET id = rowid + {OFFSET} WHERE id IS NULL"
    ).rowcount
    conn.commit()
    print(f"  child_chunks.id: backfilled {n:,} rows in {time.time()-t0:.1f}s")

    # 4. Verify integrity: every child's parent_id should resolve to a parent
    orphans = conn.execute("""
        SELECT COUNT(*) FROM child_chunks cc
        WHERE NOT EXISTS (SELECT 1 FROM parent_chunks pc WHERE pc.id = cc.parent_id)
    """).fetchone()[0]
    print(f"\n  Orphan children (parent_id has no matching parent): {orphans:,}")
    if orphans > 0:
        print("  ^^ investigate before running Step 6")

    print("\nAfter:")
    status(conn)


if __name__ == "__main__":
    main()
