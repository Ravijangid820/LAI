"""
Export PostgreSQL databases to SQLite files.

Creates portable .db files that can be read with Python's built-in sqlite3
module — no Docker, no PostgreSQL server, no sudo required.

Embeddings are stored as binary BLOBs (compact) and can be converted back
to Python lists/numpy arrays with:
    import struct
    floats = struct.unpack(f'{1024}f', blob)

Usage:
    # Export pipeline DB
    python scripts/export_to_sqlite.py pipeline

    # Export app DB (large — 25.8M rows with embeddings)
    python scripts/export_to_sqlite.py app

    # Export both
    python scripts/export_to_sqlite.py all
"""

import os
import re
import sqlite3
import struct
import sys
import time

import psycopg2
import psycopg2.extras


# ============================================================
# Config
# ============================================================

PIPELINE_DB = {
    "host": "localhost",
    "port": 5434,
    "dbname": "lai_db",
    "user": "lai_user",
    "password": "lai_test_password_2024",
}

APP_DB = {
    "host": "localhost",
    "port": 5433,
    "dbname": "lai_db",
    "user": "lai_user",
    "password": "lai_test_password_2024",
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "processed", "db_export")


# ============================================================
# Helpers
# ============================================================

def get_pg_conn(config: dict):
    return psycopg2.connect(**config)


def get_tables(pg_conn) -> list[str]:
    """Get all user tables in public schema."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)
        return [row[0] for row in cur.fetchall()]


def get_columns(pg_conn, table: str) -> list[tuple]:
    """Get column names and types for a table."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        return cur.fetchall()


def get_row_count(pg_conn, table: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def pg_type_to_sqlite(data_type: str, udt_name: str) -> str:
    """Map PostgreSQL types to SQLite types."""
    if udt_name == "vector":
        return "BLOB"  # Store embeddings as binary
    if data_type in ("integer", "bigint", "smallint"):
        return "INTEGER"
    if data_type in ("real", "double precision", "numeric"):
        return "REAL"
    if data_type == "boolean":
        return "INTEGER"
    if data_type == "ARRAY":
        return "TEXT"  # Store arrays as JSON text
    if udt_name == "tsvector":
        return "TEXT"
    if udt_name == "jsonb" or udt_name == "json":
        return "TEXT"
    if udt_name == "uuid":
        return "TEXT"
    return "TEXT"


def parse_pg_vector(val: str) -> bytes:
    """Convert PostgreSQL vector string '[0.1,0.2,...]' to binary BLOB."""
    if val is None:
        return None
    # Strip brackets and parse
    val = val.strip("[]")
    floats = [float(x) for x in val.split(",")]
    return struct.pack(f"{len(floats)}f", *floats)


def convert_value(val, data_type: str, udt_name: str):
    """Convert a PostgreSQL value to SQLite-compatible format."""
    if val is None:
        return None
    if udt_name == "vector":
        return parse_pg_vector(str(val))
    if data_type == "ARRAY":
        import json
        # PostgreSQL arrays come as Python lists via psycopg2
        return json.dumps(val, ensure_ascii=False) if val else "[]"
    if udt_name in ("jsonb", "json"):
        import json
        return json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
    if udt_name == "tsvector":
        return str(val) if val else None
    if isinstance(val, bool):
        return 1 if val else 0
    return val


# ============================================================
# Export logic
# ============================================================

def export_table(pg_conn, sqlite_conn, table: str, columns: list[tuple],
                 batch_size: int = 10000):
    """Export a single table from PostgreSQL to SQLite."""
    col_names = [c[0] for c in columns]
    col_types = [(c[1], c[2]) for c in columns]

    # Create SQLite table
    sqlite_cols = []
    for name, (data_type, udt_name) in zip(col_names, col_types):
        sqlite_type = pg_type_to_sqlite(data_type, udt_name)
        sqlite_cols.append(f'"{name}" {sqlite_type}')

    create_sql = f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(sqlite_cols)})'
    sqlite_conn.execute(create_sql)

    # Count rows
    total = get_row_count(pg_conn, table)
    if total == 0:
        print(f"  {table}: 0 rows (empty)")
        return 0

    # Use server-side cursor for large tables
    cursor_name = f"export_{table}"
    pg_cur = pg_conn.cursor(name=cursor_name)
    pg_cur.itersize = batch_size

    select_sql = f'SELECT {", ".join(f"{c}" for c in col_names)} FROM "{table}"'
    pg_cur.execute(select_sql)

    placeholders = ", ".join(["?"] * len(col_names))
    insert_sql = f'INSERT INTO "{table}" ({", ".join(f"{c}" for c in col_names)}) VALUES ({placeholders})'

    exported = 0
    t0 = time.time()

    while True:
        rows = pg_cur.fetchmany(batch_size)
        if not rows:
            break

        converted = []
        for row in rows:
            converted.append(tuple(
                convert_value(val, dt, udt)
                for val, (dt, udt) in zip(row, col_types)
            ))

        sqlite_conn.executemany(insert_sql, converted)
        sqlite_conn.commit()

        exported += len(rows)
        elapsed = time.time() - t0
        rate = exported / elapsed if elapsed > 0 else 0
        eta = (total - exported) / rate if rate > 0 else 0
        pct = exported * 100 // total

        print(f"\r  {table}: {exported:,}/{total:,} ({pct}%) "
              f"- {rate:,.0f} rows/s, ETA {eta/60:.0f}m", end="", flush=True)

    pg_cur.close()
    elapsed = time.time() - t0
    print(f"\r  {table}: {exported:,} rows exported in {elapsed:.0f}s" + " " * 30)
    return exported


def create_indexes(sqlite_conn, db_type: str):
    """Create useful indexes on the SQLite database."""
    if db_type == "pipeline":
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_pc_chunk_id ON parent_chunks(chunk_id)",
            "CREATE INDEX IF NOT EXISTS idx_pc_doc_id ON parent_chunks(doc_id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_parent ON child_chunks(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_chunk_id ON child_chunks(chunk_id)",
            "CREATE INDEX IF NOT EXISTS idx_ts_domain ON training_samples(domain)",
            "CREATE INDEX IF NOT EXISTS idx_ts_task ON training_samples(task_type)",
        ]
    elif db_type == "app":
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_chunks_doctype ON chunks(doc_type)",
            "CREATE INDEX IF NOT EXISTS idx_lc_id ON law_chunks(id)",
            "CREATE INDEX IF NOT EXISTS idx_fi_status ON file_inventory(status)",
        ]
    else:
        return

    for idx_sql in indexes:
        try:
            sqlite_conn.execute(idx_sql)
        except Exception as e:
            print(f"  Index warning: {e}")
    sqlite_conn.commit()


def export_database(pg_config: dict, sqlite_path: str, db_type: str):
    """Export an entire PostgreSQL database to SQLite."""
    print(f"\n{'=' * 60}")
    print(f"Exporting {db_type} DB to: {sqlite_path}")
    print(f"{'=' * 60}")

    pg_conn = get_pg_conn(pg_config)
    pg_conn.set_session(readonly=True)

    # Remove existing SQLite file if present
    if os.path.exists(sqlite_path):
        os.remove(sqlite_path)

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.execute("PRAGMA journal_mode=WAL")
    sqlite_conn.execute("PRAGMA synchronous=NORMAL")
    sqlite_conn.execute("PRAGMA cache_size=-2000000")  # 2GB cache

    tables = get_tables(pg_conn)
    print(f"Found {len(tables)} tables: {', '.join(tables)}")

    total_rows = 0
    t0 = time.time()

    for table in tables:
        columns = get_columns(pg_conn, table)
        count = export_table(pg_conn, sqlite_conn, table, columns)
        total_rows += count

    print(f"\nCreating indexes...")
    create_indexes(sqlite_conn, db_type)

    elapsed = time.time() - t0
    final_size = os.path.getsize(sqlite_path) / (1024 ** 3)

    print(f"\n{'=' * 60}")
    print(f"Export complete!")
    print(f"  Tables: {len(tables)}")
    print(f"  Total rows: {total_rows:,}")
    print(f"  File size: {final_size:.1f} GB")
    print(f"  Time: {elapsed/60:.1f} minutes")
    print(f"  Path: {sqlite_path}")
    print(f"{'=' * 60}")

    sqlite_conn.close()
    pg_conn.close()


# ============================================================
# Main
# ============================================================

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("pipeline", "app", "all"):
        print("Usage: python export_to_sqlite.py [pipeline|app|all]")
        sys.exit(1)

    target = sys.argv[1]
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if target in ("pipeline", "all"):
        export_database(
            PIPELINE_DB,
            os.path.join(OUTPUT_DIR, "pipeline.db"),
            "pipeline",
        )

    if target in ("app", "all"):
        export_database(
            APP_DB,
            os.path.join(OUTPUT_DIR, "app.db"),
            "app",
        )


if __name__ == "__main__":
    main()
