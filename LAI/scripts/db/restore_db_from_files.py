"""
Rehydrate a fresh pipeline SQLite DB from a files-on-disk backup made by
backup_db_to_files.py.

Complements sqlite3 .recover (which takes hours on a multi-GB corrupt DB)
by restoring from the regular JSONL + NPZ snapshot in ~10× less time.

Typical usage after DB corruption:
    1. move the bad DB aside
    2. run this to rebuild from the most recent backup
    3. re-run any pipeline steps whose outputs post-date the backup

Usage:
    python scripts/restore_db_from_files.py --from 2026-04-24T100000
    python scripts/restore_db_from_files.py --from LATEST          # most recent
    python scripts/restore_db_from_files.py --from ... --into new.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

LAI_DIR     = Path(__file__).resolve().parents[1]
DB_DEFAULT  = LAI_DIR / "processed" / "pipeline_local.db"
BACKUP_ROOT = LAI_DIR / "processed" / "backups"


# Schemas — must match the existing DB init. When restoring into a
# brand-new file we need to create these.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parent_chunks (
    id INTEGER, doc_id TEXT, chunk_id TEXT, section TEXT, content TEXT,
    char_count INTEGER, language TEXT, doc_type TEXT, source_file TEXT,
    source_bucket TEXT, domain TEXT, page_start INTEGER, page_end INTEGER,
    metadata TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS child_chunks (
    id INTEGER, parent_id INTEGER, chunk_id TEXT, content TEXT,
    context_prefix TEXT, char_count INTEGER, embedding BLOB,
    search_vector TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS chunk_classifications (
    id INTEGER, parent_id INTEGER, domain TEXT, model_name TEXT,
    model_version TEXT, prompt_version TEXT, confidence REAL,
    raw_response TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS training_samples (
    id INTEGER, parent_id INTEGER, domain TEXT, task_type TEXT,
    messages TEXT, quality_score REAL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS child_embeddings (
    child_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS pilot_embeddings (
    child_id INTEGER, pool TEXT, embedding BLOB,
    PRIMARY KEY (child_id, pool)
);
"""


def latest_backup() -> Path:
    if not BACKUP_ROOT.exists():
        raise SystemExit(f"No backups at {BACKUP_ROOT}")
    candidates = [d for d in BACKUP_ROOT.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
    if not candidates:
        raise SystemExit(f"No backups found in {BACKUP_ROOT}")
    return max(candidates, key=lambda d: d.stat().st_mtime)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Performance pragmas during bulk load
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.commit()


def restore_jsonl(conn: sqlite3.Connection, jsonl: Path, table: str,
                  dry_run: bool, batch_size: int = 2000) -> int:
    if not jsonl.exists():
        print(f"  {table}: {jsonl.name} not found, skip")
        return 0
    # Detect schema
    cols_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = [c[1] for c in cols_info]
    placeholders = ",".join("?" * len(col_names))
    insert_sql = f"INSERT OR REPLACE INTO {table}({','.join(col_names)}) VALUES ({placeholders})"

    n = 0
    t0 = time.time()
    buf = []
    with open(jsonl) as f:
        for line in f:
            rec = json.loads(line)
            row = tuple(rec.get(c) for c in col_names)
            buf.append(row)
            if len(buf) >= batch_size:
                if not dry_run:
                    conn.executemany(insert_sql, buf)
                    conn.commit()
                n += len(buf)
                buf = []
                if n % 50_000 == 0:
                    print(f"  {table}: {n:>10,} rows  ({n / (time.time()-t0):.0f}/s)",
                          flush=True)
    if buf:
        if not dry_run:
            conn.executemany(insert_sql, buf)
            conn.commit()
        n += len(buf)
    print(f"  {table}: restored {n:,} rows in {time.time()-t0:.1f}s")
    return n


def restore_embeddings(conn: sqlite3.Connection, sub: Path, table: str,
                       dry_run: bool) -> int:
    if not sub.exists():
        print(f"  {table}: dir {sub.name}/ not found, skip")
        return 0
    files = sorted(sub.glob(f"{table}_*.npz"))
    if not files:
        print(f"  {table}: no .npz files in {sub.name}/, skip")
        return 0
    has_pool = "pool" in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    sql = (
        "INSERT OR REPLACE INTO pilot_embeddings(child_id, pool, embedding) VALUES (?,?,?)"
        if has_pool else
        "INSERT OR REPLACE INTO child_embeddings(child_id, embedding) VALUES (?,?)"
    )
    n = 0
    t0 = time.time()
    for fp in files:
        data = np.load(fp, allow_pickle=False)
        cids = data["child_ids"]
        embs = data["embeddings"]
        if has_pool:
            pools = data["pools"]
            rows = [(int(c), str(p), e.astype(np.float32).tobytes())
                    for c, p, e in zip(cids, pools, embs)]
        else:
            rows = [(int(c), e.astype(np.float32).tobytes())
                    for c, e in zip(cids, embs)]
        if not dry_run:
            conn.executemany(sql, rows)
            conn.commit()
        n += len(rows)
        print(f"  {table}: {n:>10,} rows  (file {fp.name})", flush=True)
    print(f"  {table}: restored {n:,} rows in {time.time()-t0:.1f}s")
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="src", default="LATEST",
                   help="Backup dir name (under processed/backups/) or LATEST")
    p.add_argument("--into", dest="dst", default=str(DB_DEFAULT),
                   help="Target DB path (default: processed/pipeline_local.db)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    src_dir = latest_backup() if args.src == "LATEST" else BACKUP_ROOT / args.src
    if not src_dir.exists():
        raise SystemExit(f"Backup dir not found: {src_dir}")
    manifest = json.loads((src_dir / "manifest.json").read_text())
    print(f"Restoring from: {src_dir}")
    print(f"  created: {manifest.get('created')}")
    print(f"  tables : {[t['table'] for t in manifest['tables']]}")
    print(f"  into   : {args.dst}")
    print(f"  mode   : {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()

    dst_path = Path(args.dst)
    if dst_path.exists() and not args.dry_run:
        ans = input(f"'{args.dst}' exists. Overwrite (INSERT OR REPLACE)? [y/N] ").strip().lower()
        if ans != "y":
            raise SystemExit("aborted")

    conn = sqlite3.connect(args.dst)
    ensure_schema(conn)

    total = 0
    for t in manifest["tables"]:
        name = t["table"]
        if name in ("child_embeddings", "pilot_embeddings"):
            total += restore_embeddings(conn, src_dir / name, name, args.dry_run)
        else:
            total += restore_jsonl(conn, src_dir / f"{name}.jsonl", name, args.dry_run)

    print(f"\nDone: {total:,} rows restored to {args.dst}")


if __name__ == "__main__":
    main()
