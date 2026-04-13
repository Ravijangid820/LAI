"""
Local storage adapters for running the pipeline without Docker services.

When Docker daemon is unavailable, MinIO and PostgreSQL data can still be
accessed directly from disk:
- MinIO: bind-mounted at Docker/database/minio/data/ (files have 32-byte binary header)
- PostgreSQL: replaced by SQLite for pipeline state tracking

Usage:
    # In cli.py, when --local flag is set:
    from lai.pipeline.local_storage import patch_cli_for_local
    patch_cli_for_local(minio_data_dir="/path/to/Docker/database/minio/data")

    # Or use components directly:
    from lai.pipeline.local_storage import LocalMinIO, LocalDB
"""

import io
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Generator, Optional

from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.local_storage")

# ============================================================
# Local MinIO: read files from bind-mount directory
# ============================================================

MINIO_HEADER_BYTES = 32  # MinIO prepends a binary header to stored objects


class LocalMinIO:
    """Drop-in replacement for MinIO operations using the bind-mount directory.

    MinIO stores objects as files at:
        <data_dir>/<bucket>/<object_key>/<uuid>/part.1

    Each part.1 file has a ~32-byte binary header before the actual content.
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"MinIO data directory not found: {data_dir}")
        logger.info(f"LocalMinIO initialized: {data_dir}")

    def bucket_exists(self, bucket: str) -> bool:
        return (self.data_dir / bucket).is_dir()

    def make_bucket(self, bucket: str):
        (self.data_dir / bucket).mkdir(parents=True, exist_ok=True)

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True):
        """Yield object info dicts matching the MinIO list_objects interface."""
        bucket_dir = self.data_dir / bucket
        if not bucket_dir.exists():
            return

        # MinIO stores each object as: <bucket>/<key>/<uuid>/part.1
        # The <key> path mirrors the original object key, with xl.meta alongside
        for xl_meta in bucket_dir.rglob("xl.meta"):
            obj_dir = xl_meta.parent
            # The object key is the relative path from bucket to the xl.meta's parent
            obj_key = str(obj_dir.relative_to(bucket_dir))

            if prefix and not obj_key.startswith(prefix):
                continue

            # Find the actual data file (part.1, part.2, etc.)
            part_files = sorted(obj_dir.glob("*/part.*"))
            if not part_files:
                continue

            total_size = sum(f.stat().st_size for f in part_files)
            yield _ObjectInfo(name=obj_key, size=total_size, is_dir=False)

    def get_object(self, bucket: str, key: str) -> "_LocalResponse":
        """Read an object from the local filesystem."""
        obj_dir = self.data_dir / bucket / key
        if not obj_dir.is_dir():
            raise FileNotFoundError(f"Object not found: {bucket}/{key}")

        # Find part files
        part_files = sorted(obj_dir.rglob("part.*"))
        if not part_files:
            raise FileNotFoundError(f"No part files for: {bucket}/{key}")

        # Concatenate all parts
        data = b""
        for pf in part_files:
            data += pf.read_bytes()

        return _LocalResponse(data)

    def stat_object(self, bucket: str, key: str):
        """Check if an object exists (raises if not)."""
        obj_dir = self.data_dir / bucket / key
        if not obj_dir.is_dir():
            raise FileNotFoundError(f"Object not found: {bucket}/{key}")
        part_files = list(obj_dir.rglob("part.*"))
        if not part_files:
            raise FileNotFoundError(f"No part files for: {bucket}/{key}")
        return True

    def put_object(self, bucket: str, key: str, data: io.BytesIO, length: int,
                   content_type: str = "application/octet-stream"):
        """Write an object to the local filesystem (for Step 1 output)."""
        import uuid
        obj_dir = self.data_dir / bucket / key
        obj_dir.mkdir(parents=True, exist_ok=True)

        part_id = str(uuid.uuid4())
        part_dir = obj_dir / part_id
        part_dir.mkdir(exist_ok=True)

        data.seek(0)
        # Write without MinIO header — our reader skips to first { anyway
        (part_dir / "part.1").write_bytes(data.read())

        # Write a minimal xl.meta so list_objects can find it
        (obj_dir / "xl.meta").write_bytes(b"")


class _ObjectInfo:
    """Mimics minio.datatypes.Object."""
    def __init__(self, name: str, size: int, is_dir: bool = False):
        self.object_name = name
        self.name = name
        self.size = size
        self.is_dir = is_dir
        self.etag = ""


class _LocalResponse:
    """Mimics urllib3.response.HTTPResponse for MinIO get_object."""
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self):
        pass

    def release_conn(self):
        pass


# ============================================================
# Local DB: SQLite replacement for PostgreSQL
# ============================================================

class LocalDB:
    """SQLite-based replacement for the PostgreSQL pipeline database.

    Implements the same schema as 01_init.sql but using SQLite.
    Thread-safe via a connection-per-thread model.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()
        logger.info(f"LocalDB initialized: {db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id          TEXT NOT NULL,
                chunk_id        TEXT NOT NULL UNIQUE,
                section         TEXT,
                content         TEXT NOT NULL,
                char_count      INTEGER NOT NULL,
                language        TEXT NOT NULL,
                doc_type        TEXT NOT NULL,
                source_file     TEXT NOT NULL,
                source_bucket   TEXT DEFAULT 'lai-raw',
                domain          TEXT,  -- JSON array as text
                page_start      INTEGER,
                page_end        INTEGER,
                metadata        TEXT DEFAULT '{}',
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS child_chunks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id       INTEGER REFERENCES parent_chunks(id) ON DELETE CASCADE,
                chunk_id        TEXT NOT NULL UNIQUE,
                content         TEXT NOT NULL,
                context_prefix  TEXT,
                char_count      INTEGER NOT NULL,
                embedding       TEXT,  -- JSON array as text (no vector type in SQLite)
                search_vector   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS training_samples (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id       INTEGER REFERENCES parent_chunks(id) ON DELETE SET NULL,
                domain          TEXT,
                task_type       TEXT,
                messages        TEXT NOT NULL,  -- JSON
                quality_score   REAL,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chunk_classifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id       INTEGER REFERENCES parent_chunks(id) ON DELETE CASCADE,
                domain          TEXT,
                model_name      TEXT,
                model_version   TEXT,
                prompt_version  TEXT,
                raw_response    TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_parent_doc_id ON parent_chunks(doc_id);
            CREATE INDEX IF NOT EXISTS idx_parent_chunk_id ON parent_chunks(chunk_id);
            CREATE INDEX IF NOT EXISTS idx_child_parent ON child_chunks(parent_id);
            CREATE INDEX IF NOT EXISTS idx_child_chunk_id ON child_chunks(chunk_id);
            CREATE INDEX IF NOT EXISTS idx_training_domain ON training_samples(domain);
            CREATE INDEX IF NOT EXISTS idx_training_task ON training_samples(task_type);
        """)
        conn.commit()

    def execute(self, query: str, params=None):
        """Execute a write query (INSERT/UPDATE/DELETE)."""
        conn = self._get_conn()
        query = _pg_to_sqlite(query)
        try:
            conn.execute(query, params or ())
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fetch(self, query: str, params=None) -> list:
        """Execute a read query and return all rows."""
        conn = self._get_conn()
        query = _pg_to_sqlite(query)
        cur = conn.execute(query, params or ())
        return cur.fetchall()

    def insert_returning(self, query: str, params=None):
        """Execute INSERT ... RETURNING and return the row."""
        conn = self._get_conn()
        query = _pg_to_sqlite(query)
        # SQLite doesn't support RETURNING, use lastrowid
        cur = conn.execute(query, params or ())
        conn.commit()
        return (cur.lastrowid,)

    def batch_insert_parents(self, rows: list) -> list:
        """Batch insert parent chunks, return list of (id, chunk_id) for inserted rows."""
        conn = self._get_conn()
        inserted = []
        for row in rows:
            # row = (doc_id, chunk_id, section, content, char_count, language,
            #        doc_type, source_file, page_start, page_end, metadata)
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO parent_chunks
                       (doc_id, chunk_id, section, content, char_count, language,
                        doc_type, source_file, page_start, page_end, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                if cur.rowcount > 0:
                    inserted.append((cur.lastrowid, row[1]))  # (id, chunk_id)
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        return inserted

    def batch_insert_children(self, rows: list):
        """Batch insert child chunks. rows = [(parent_id, chunk_id, content, char_count), ...]"""
        conn = self._get_conn()
        conn.executemany(
            """INSERT OR IGNORE INTO child_chunks (parent_id, chunk_id, content, char_count)
               VALUES (?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def get_stats(self) -> dict:
        """Return table counts for progress reporting."""
        conn = self._get_conn()
        stats = {}
        for table in ["parent_chunks", "child_chunks", "training_samples", "chunk_classifications"]:
            cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = cur.fetchone()[0]
        return stats


def _pg_to_sqlite(query: str) -> str:
    """Convert common PostgreSQL syntax to SQLite equivalents."""
    import re

    q = query

    # %s -> ? (positional params)
    q = q.replace("%s", "?")

    # PostgreSQL array literal '{}' -> JSON '[]'
    q = q.replace("= '{}'", "= '[]'")
    q = q.replace("domain = '{}'", "domain = '[]'")

    # domain IS NULL OR domain = '{}' -> domain IS NULL OR domain = '[]' OR domain = '{}'
    # (handle both formats)

    # Remove ::vector, ::jsonb casts
    q = re.sub(r"::\w+", "", q)

    # to_tsvector('german', ...) -> just the text (no full-text in SQLite)
    q = re.sub(r"to_tsvector\([^,]+,\s*", "", q)
    # Clean up dangling )
    if "search_vector" in q and "to_tsvector" not in q:
        pass  # already cleaned

    # ON CONFLICT (chunk_id) DO NOTHING -> OR IGNORE (already handled in batch methods)
    q = re.sub(r"ON CONFLICT\s*\([^)]+\)\s*DO NOTHING", "", q)

    # RETURNING ... -> remove (handled by lastrowid)
    q = re.sub(r"RETURNING\s+.*$", "", q, flags=re.MULTILINE)

    # COALESCE works in SQLite too
    # LEFT JOIN works in SQLite too

    return q.strip()


# ============================================================
# Patch CLI to use local storage
# ============================================================

def _find_minio_data_dir() -> str:
    """Auto-detect the MinIO bind-mount directory."""
    candidates = [
        # Relative to project root
        os.path.join(os.getcwd(), "Docker", "database", "minio", "data"),
        # Absolute common path
        "/data/projects/lai/Docker/database/minio/data",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    raise FileNotFoundError(
        "Cannot find MinIO data directory. "
        "Pass --minio-data-dir or run from the project root."
    )


def _find_db_path() -> str:
    """Return path for local SQLite database."""
    candidates = [
        os.path.join(os.getcwd(), "LAI", "processed", "pipeline_local.db"),
        "/data/projects/lai/LAI/processed/pipeline_local.db",
    ]
    for c in candidates:
        parent = os.path.dirname(c)
        if os.path.isdir(parent):
            return c
    # Fallback
    return os.path.join(os.getcwd(), "pipeline_local.db")


def patch_cli_for_local(minio_data_dir: str | None = None, db_path: str | None = None):
    """Monkey-patch cli.py helper functions to use local storage.

    Call this before running any pipeline step. It replaces:
    - _get_minio() / _ensure_bucket / _list_objects / _download / _upload / _object_exists
    - _get_db() / _db_execute / _db_fetch / _db_insert_returning / _db_transaction
    """
    import sys
    # When run as `python -m lai.pipeline.cli`, the module is __main__,
    # not lai.pipeline.cli. We need to patch the actual running module.
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "_get_minio"):
        cli = sys.modules["__main__"]
    else:
        import lai.pipeline.cli as cli

    # --- Storage ---
    data_dir = minio_data_dir or _find_minio_data_dir()
    local_minio = LocalMinIO(data_dir)

    def _patched_get_minio():
        return local_minio

    def _patched_ensure_bucket(name: str):
        if not local_minio.bucket_exists(name):
            local_minio.make_bucket(name)
            logger.info(f"Created local bucket dir: {name}")

    def _patched_list_objects(bucket: str, prefix: str = ""):
        for obj in local_minio.list_objects(bucket, prefix=prefix):
            if not obj.is_dir:
                yield {"name": obj.name, "size": obj.size, "etag": obj.etag}

    def _patched_download(bucket: str, key: str) -> io.BytesIO:
        resp = local_minio.get_object(bucket, key)
        return io.BytesIO(resp.read())

    def _patched_upload(bucket: str, key: str, data: io.BytesIO):
        data.seek(0)
        local_minio.put_object(bucket, key, data, data.getbuffer().nbytes)

    def _patched_object_exists(bucket: str, key: str) -> bool:
        try:
            local_minio.stat_object(bucket, key)
            return True
        except FileNotFoundError:
            return False

    cli._get_minio = _patched_get_minio
    cli._ensure_bucket = _patched_ensure_bucket
    cli._list_objects = _patched_list_objects
    cli._download = _patched_download
    cli._upload = _patched_upload
    cli._object_exists = _patched_object_exists

    # --- Database ---
    local_db_path = db_path or _find_db_path()
    local_db = LocalDB(local_db_path)

    def _patched_get_db():
        return local_db

    def _patched_db_execute(query: str, params=None):
        local_db.execute(query, params)

    def _patched_db_fetch(query: str, params=None):
        return local_db.fetch(query, params)

    def _patched_db_insert_returning(query: str, params=None):
        return local_db.insert_returning(query, params)

    from contextlib import contextmanager

    @contextmanager
    def _patched_db_transaction():
        conn = local_db._get_conn()
        cur = conn.cursor()
        try:
            yield conn, _SQLiteCursorAdapter(cur)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    cli._get_db = _patched_get_db
    cli._db_execute = _patched_db_execute
    cli._db_fetch = _patched_db_fetch
    cli._db_insert_returning = _patched_db_insert_returning
    cli._db_transaction = _patched_db_transaction

    # Patch psycopg2.extras.execute_values for Step 2/3 compatibility
    patch_execute_values()

    logger.info("=" * 60)
    logger.info("LOCAL MODE ACTIVE")
    logger.info(f"  MinIO data: {data_dir}")
    logger.info(f"  SQLite DB:  {local_db_path}")
    logger.info("=" * 60)

    return local_minio, local_db


class _SQLiteCursorAdapter:
    """Wraps sqlite3.Cursor to handle psycopg2-style %s params and execute_values."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cur = cursor
        self._conn = cursor.connection

    def execute(self, query: str, params=None):
        query = _pg_to_sqlite(query)
        self._cur.execute(query, params or ())
        return self._cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid


def _sqlite_execute_values(cur, query: str, argslist, template=None, fetch=False):
    """Drop-in replacement for psycopg2.extras.execute_values using SQLite.

    Handles INSERT ... VALUES %s patterns by inserting rows one at a time.
    When fetch=True, returns list of (id, chunk_id) tuples for inserted rows.
    """
    import re

    query = _pg_to_sqlite(query)

    # Extract the column list and table from the INSERT statement
    # Pattern: INSERT INTO table (col1, col2, ...) VALUES %s ...
    match = re.search(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*", query, re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot parse INSERT query for execute_values: {query}")

    table = match.group(1)
    columns = match.group(2)
    col_count = len(columns.split(","))
    placeholders = ", ".join(["?"] * col_count)

    insert_sql = f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"

    results = []
    adapter = cur if isinstance(cur, _SQLiteCursorAdapter) else cur

    for row in argslist:
        try:
            if isinstance(adapter, _SQLiteCursorAdapter):
                adapter._cur.execute(insert_sql, row)
                if fetch and adapter._cur.rowcount > 0:
                    # For parent_chunks: return (id, chunk_id)
                    results.append((adapter._cur.lastrowid, row[1]))
            else:
                cur.execute(insert_sql, row)
                if fetch and cur.rowcount > 0:
                    results.append((cur.lastrowid, row[1]))
        except sqlite3.IntegrityError:
            pass

    if fetch:
        return results


def patch_execute_values():
    """Monkey-patch psycopg2.extras.execute_values with SQLite version.

    Imports the real psycopg2.extras (if installed) and replaces just
    the execute_values function. If psycopg2 is not installed, creates
    a minimal fake module.
    """
    import types
    import sys

    try:
        import psycopg2.extras
        psycopg2.extras.execute_values = _sqlite_execute_values
    except ImportError:
        # psycopg2 not installed — create minimal fakes
        if "psycopg2" not in sys.modules:
            psycopg2_mod = types.ModuleType("psycopg2")
            psycopg2_mod.pool = types.ModuleType("psycopg2.pool")
            sys.modules["psycopg2"] = psycopg2_mod
            sys.modules["psycopg2.pool"] = psycopg2_mod.pool

        if "psycopg2.extras" not in sys.modules:
            extras_mod = types.ModuleType("psycopg2.extras")
            sys.modules["psycopg2.extras"] = extras_mod

        sys.modules["psycopg2.extras"].execute_values = _sqlite_execute_values
