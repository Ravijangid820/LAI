#!/usr/bin/env python3
"""Delete a single matter_documents row by id, safely.

Only deletes rows whose status is 'failed' unless --force is passed, so a
careless run can't blow away a successfully-ingested document. Also drops
the on-disk file copy the worker stored under matter_uploads/.

Usage:
    python3 scripts/ops/delete_matter_document.py <doc_id>
    python3 scripts/ops/delete_matter_document.py <doc_id> --force   # any status

Owner-only: the sessions DB lives at processed/sessions.db and is writable
by user `rj`. Anyone else hits a permission error before any rows change.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "processed" / "sessions.db"
MATTER_UPLOADS_DIR = Path(__file__).resolve().parents[2] / "processed" / "matter_uploads"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("doc_id", type=int, help="matter_documents.id to delete")
    p.add_argument(
        "--force",
        action="store_true",
        help="Allow deleting rows whose status is not 'failed' (default: refuse).",
    )
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: sessions DB not found at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, session_id, doc_index, filename, status FROM matter_documents WHERE id = ?",
            (args.doc_id,),
        ).fetchone()
        if row is None:
            print(f"No matter_documents row with id={args.doc_id}", file=sys.stderr)
            return 1

        print("Target row:")
        for k in row.keys():
            print(f"  {k}: {row[k]}")

        if row["status"] != "failed" and not args.force:
            print(
                f"\nREFUSED: row has status={row['status']!r}, not 'failed'. "
                "Pass --force to delete anyway.",
                file=sys.stderr,
            )
            return 3

        # On-disk file copy the worker wrote — best-effort cleanup.
        sid = row["session_id"]
        candidates = list(MATTER_UPLOADS_DIR.glob(f"{sid}/{args.doc_id}.*"))
        for c in candidates:
            try:
                c.unlink()
                print(f"Removed file: {c}")
            except OSError as e:
                print(f"Could not remove {c}: {e}", file=sys.stderr)

        conn.execute("DELETE FROM matter_documents WHERE id = ?", (args.doc_id,))
        conn.commit()
        print(f"Deleted matter_documents.id={args.doc_id}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
