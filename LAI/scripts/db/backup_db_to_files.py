"""
Snapshot the local pipeline SQLite DB to portable files on disk.

Writes each logical table to its own file:
  - text tables (parent_chunks, child_chunks, chunk_classifications,
    training_samples) → JSONL, one record per line
  - embeddings (child_embeddings, pilot_embeddings) → NPZ
    (child_id + embedding arrays, compressed)

Why files, not another DB: after DB corruption they survive independently,
they're cheap to inspect, diff, rsync, and restore. They also complement
`scripts/lai-segments/` (Step 1 outputs) so the full pipeline is backed
up at every stage.

Usage:
    python scripts/backup_db_to_files.py             # snapshot to timestamped dir
    python scripts/backup_db_to_files.py --into NAME # snapshot to processed/backups/NAME
    python scripts/backup_db_to_files.py --tables training_samples child_embeddings
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

LAI_DIR     = Path(__file__).resolve().parents[1]
DB          = LAI_DIR / "processed" / "pipeline_local.db"
BACKUP_ROOT = LAI_DIR / "processed" / "backups"

EMBED_DIM = 4096
BATCH     = 5000         # rows per file for text tables
EMB_BATCH = 10_000       # embeddings per NPZ (~160 MB each)

TEXT_TABLES = (
    "parent_chunks",
    "child_chunks",
    "chunk_classifications",
    "training_samples",
)
EMB_TABLES = ("child_embeddings", "pilot_embeddings")


def iter_batches(conn: sqlite3.Connection, table: str, size: int):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    # content column in child_chunks is huge + duplicated in chunk text;
    # embedding BLOB column is only present in the main child_chunks table
    # and we skip it here (exported via child_embeddings NPZ)
    skip = {"embedding"} if table == "child_chunks" else set()
    read_cols = [c for c in cols if c not in skip]
    sql = f"SELECT rowid, {','.join(read_cols)} FROM {table} WHERE rowid > ? ORDER BY rowid LIMIT ?"
    last = 0
    while True:
        rows = conn.execute(sql, (last, size)).fetchall()
        if not rows:
            break
        yield read_cols, rows
        last = rows[-1][0]


def backup_text(conn: sqlite3.Connection, table: str, out_dir: Path) -> dict:
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if total == 0:
        print(f"  {table}: empty, skip")
        return {"table": table, "rows": 0, "files": 0}
    out_file = out_dir / f"{table}.jsonl"
    written = 0
    t0 = time.time()
    with open(out_file, "w", encoding="utf-8") as f:
        for cols, rows in iter_batches(conn, table, BATCH):
            for row in rows:
                # row = (rowid, col0, col1, ...)
                rec = {"_rowid": row[0]}
                for c, v in zip(cols, row[1:]):
                    rec[c] = v
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                written += 1
            if written % 50_000 == 0 or written == total:
                print(f"  {table}: {written:>10,}/{total:,} ({100*written/total:.1f}%)",
                      flush=True)
    sz = out_file.stat().st_size
    print(f"  {table}: {written:,} rows -> {out_file.name} "
          f"({sz/1024**2:.1f} MB, {time.time()-t0:.1f}s)")
    return {"table": table, "rows": written, "file": out_file.name,
            "bytes": sz, "elapsed_s": round(time.time() - t0, 1)}


def backup_embeddings(conn: sqlite3.Connection, table: str, out_dir: Path) -> dict:
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if total == 0:
        print(f"  {table}: empty, skip")
        return {"table": table, "rows": 0, "files": 0}
    # Detect the pool column (only on pilot_embeddings)
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    has_pool = "pool" in cols

    sub = out_dir / table
    sub.mkdir(parents=True, exist_ok=True)
    files = 0
    written = 0
    t0 = time.time()
    order_col = "child_id" if "child_id" in cols else cols[0]

    last = -1
    while True:
        sel_cols = f"child_id, embedding" + (", pool" if has_pool else "")
        rows = conn.execute(
            f"SELECT {sel_cols} FROM {table} WHERE {order_col} > ? "
            f"ORDER BY {order_col} LIMIT ?",
            (last, EMB_BATCH),
        ).fetchall()
        if not rows:
            break
        last = rows[-1][0]

        child_ids = np.asarray([r[0] for r in rows], dtype=np.int64)
        embs = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        if embs.shape[1] != EMBED_DIM:
            raise RuntimeError(f"{table}: expected {EMBED_DIM}-dim, got {embs.shape[1]}")
        fpath = sub / f"{table}_{files:05d}.npz"
        save_kwargs = {"child_ids": child_ids, "embeddings": embs}
        if has_pool:
            save_kwargs["pools"] = np.asarray([r[2] for r in rows], dtype="U16")
        np.savez_compressed(fpath, **save_kwargs)

        files += 1
        written += len(rows)
        if files % 5 == 0 or written == total:
            print(f"  {table}: {written:>10,}/{total:,} ({100*written/total:.1f}%)"
                  f"  file={fpath.name}", flush=True)
    print(f"  {table}: {written:,} rows -> {files} files in {sub.name}/ "
          f"({time.time()-t0:.1f}s)")
    return {"table": table, "rows": written, "files": files,
            "dir": sub.name, "elapsed_s": round(time.time() - t0, 1)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--into", type=str, default=None,
                   help="Backup dir name under processed/backups/ (default: timestamp)")
    p.add_argument("--tables", nargs="+",
                   default=list(TEXT_TABLES) + list(EMB_TABLES),
                   help="Which tables to back up (default: all)")
    p.add_argument("--db-path", type=str, default=str(DB))
    args = p.parse_args()

    name = args.into or datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out_dir = BACKUP_ROOT / name
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    print(f"Backup DB {args.db_path}")
    print(f"       -> {out_dir}")
    print(f"Tables: {args.tables}")
    print()

    results = []
    for t in args.tables:
        if t in TEXT_TABLES:
            results.append(backup_text(conn, t, out_dir))
        elif t in EMB_TABLES:
            results.append(backup_embeddings(conn, t, out_dir))
        else:
            print(f"  SKIP unknown table: {t}")

    manifest = {
        "created": datetime.now().isoformat(),
        "db_path": args.db_path,
        "tables": results,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    total_rows = sum(r.get("rows", 0) for r in results)
    print(f"\nDone: {total_rows:,} total rows. Manifest: {out_dir/'manifest.json'}")


if __name__ == "__main__":
    main()
