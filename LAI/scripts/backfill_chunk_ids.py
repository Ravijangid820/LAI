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

Batched per `--batch-rows` rows per COMMIT. This keeps WAL small
(~1 GB per batch instead of >150 GB for a single monolithic UPDATE)
and gives incremental progress visibility.

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


def batched_update(conn: sqlite3.Connection, label: str, sql: str,
                   batch_rows: int) -> int:
    """Apply `sql` in rowid-range batches of `batch_rows` each.

    `sql` must contain two `?` placeholders for (min_rowid, max_rowid).
    Returns total rows updated.
    """
    # We can't easily know the target rowid range here — caller passes
    # sql parameterized on rowid windows. We just iterate until no rows
    # are updated in a batch.
    total = 0
    t0 = time.time()
    # Determine upper bound from a COUNT / MAX
    # (caller should size batches reasonably)
    start = 1
    table = "parent_chunks" if "parent_chunks" in sql.split("SET")[0] else "child_chunks"
    max_rowid = conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0
    print(f"  [{label}] target max_rowid={max_rowid:,}")

    while start <= max_rowid:
        end = start + batch_rows - 1
        t_batch = time.time()
        n = conn.execute(sql, (start, end)).rowcount
        conn.commit()
        if n > 0:
            total += n
            rate = total / (time.time() - t0)
            eta = (max_rowid - end) / (batch_rows / max(time.time() - t_batch, 0.001)) / 60
            print(f"  [{label}] rowid {start:>10,}-{end:>10,}  "
                  f"updated={n:>8,}  total={total:>12,}  "
                  f"({time.time()-t_batch:.1f}s/batch, {rate:.0f} rows/s, ETA {eta:.1f} min)",
                  flush=True)
        start = end + 1
    print(f"  [{label}] DONE: {total:,} rows updated in {(time.time()-t0)/60:.1f} min")
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Perform the backfill")
    p.add_argument("--batch-rows", type=int, default=1_000_000,
                   help="Rows per batch commit (default: 1M)")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    # Safer than OFF, faster than FULL — survives process crashes (WAL intact)
    # but not OS power loss (no fsync). Acceptable trade for batch work.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=MEMORY")

    print("Before:")
    status(conn)

    # Sanity: no id should exceed what this offset would produce
    # (i.e. max_rowid + OFFSET). Ids above OFFSET are either from a
    # previous partial backfill (id = rowid + OFFSET, legal to resume)
    # or stale garbage (would overlap new backfill values, fatal).
    for t in ("parent_chunks", "child_chunks"):
        max_id, max_rowid = conn.execute(
            f"SELECT MAX(id), MAX(rowid) FROM {t}"
        ).fetchone()
        max_id = max_id or 0
        max_rowid = max_rowid or 0
        legal_ceiling = max_rowid + OFFSET
        if max_id > legal_ceiling:
            raise SystemExit(
                f"ABORT: {t} max(id)={max_id} exceeds legal ceiling "
                f"max(rowid)+OFFSET={legal_ceiling}"
            )
        if max_id > OFFSET:
            # Resumption path — verify the offset-region ids were
            # produced by this script (id == rowid + OFFSET).
            bad = conn.execute(
                f"SELECT COUNT(*) FROM {t} "
                f"WHERE id >= ? AND id != rowid + ?",
                (OFFSET, OFFSET),
            ).fetchone()[0]
            if bad > 0:
                raise SystemExit(
                    f"ABORT: {t} has {bad} rows where id >= OFFSET but "
                    f"id != rowid+OFFSET — not a clean partial backfill"
                )

    if not args.apply:
        print(f"\n(dry-run; pass --apply to perform backfill with offset {OFFSET:,})")
        return

    # 1. Backfill parent_chunks.id from rowid + OFFSET
    batched_update(
        conn, "parent_chunks.id",
        f"UPDATE parent_chunks SET id = rowid + {OFFSET} "
        f"WHERE id IS NULL AND rowid BETWEEN ? AND ?",
        args.batch_rows,
    )

    # 2. Shift cc.parent_id for new children
    # cc.id IS NULL identifies new rows (pre-backfill).
    # Those rows have cc.parent_id = pc.rowid; we need cc.parent_id = pc.id = pc.rowid + OFFSET
    batched_update(
        conn, "child_chunks.parent_id (shift)",
        f"UPDATE child_chunks SET parent_id = parent_id + {OFFSET} "
        f"WHERE id IS NULL AND rowid BETWEEN ? AND ?",
        args.batch_rows,
    )

    # 3. Backfill child_chunks.id
    batched_update(
        conn, "child_chunks.id",
        f"UPDATE child_chunks SET id = rowid + {OFFSET} "
        f"WHERE id IS NULL AND rowid BETWEEN ? AND ?",
        args.batch_rows,
    )

    # 4. Checkpoint the WAL so it shrinks
    print("\nCheckpointing WAL...")
    t0 = time.time()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"  checkpoint done in {time.time()-t0:.1f}s")

    # 5. Verify integrity
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
