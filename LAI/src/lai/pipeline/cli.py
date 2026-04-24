"""
CLI entry points for the data processing pipeline.

Usage:
    python -m lai.pipeline.cli step1 --dry-run
    python -m lai.pipeline.cli step1 --source "DD Reports/"
    python -m lai.pipeline.cli step2 --dry-run
"""

import argparse
import gc
import hashlib
import io
import json
import os
import signal
import sys
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Dict, Any, Optional

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.cli")

_shutdown = False
_shutdown_count = 0


def _signal_handler(signum, frame):
    global _shutdown, _shutdown_count
    _shutdown_count += 1
    if _shutdown_count == 1:
        logger.warning(f"Received signal {signum}, finishing current work and shutting down...")
        _shutdown = True
    elif _shutdown_count >= 2:
        logger.warning("Forced exit.")
        import sys
        sys.exit(1)


# Signal handlers are registered in main() — NOT at import time.
# Registering at import time causes _shutdown to be set by unrelated
# SIGTERM signals (e.g., timeout commands, process managers), breaking
# the processing loop before the final DB flush happens.


# ============================================================
# MinIO helpers (using minio SDK directly to avoid infra deps)
# ============================================================

_minio_client = None


def _get_minio():
    global _minio_client
    if _minio_client is None:
        from minio import Minio
        settings = get_settings().minio
        _minio_client = Minio(
            settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key.get_secret_value(),
            secure=settings.use_ssl,
        )
    return _minio_client


def _ensure_bucket(name: str):
    client = _get_minio()
    if not client.bucket_exists(name):
        client.make_bucket(name)
        logger.info(f"Created bucket: {name}")


def _list_objects(bucket: str, prefix: str = ""):
    client = _get_minio()
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        if not obj.is_dir:
            yield {"name": obj.object_name, "size": obj.size, "etag": obj.etag}


def _download(bucket: str, key: str) -> io.BytesIO:
    client = _get_minio()
    resp = None
    try:
        resp = client.get_object(bucket, key)
        return io.BytesIO(resp.read())
    finally:
        if resp:
            resp.close()
            resp.release_conn()


def _upload(bucket: str, key: str, data: io.BytesIO):
    client = _get_minio()
    data.seek(0)
    client.put_object(bucket, key, data, data.getbuffer().nbytes, content_type="application/jsonl")


def _object_exists(bucket: str, key: str) -> bool:
    client = _get_minio()
    try:
        client.stat_object(bucket, key)
        return True
    except Exception:
        return False


# ============================================================
# DB helpers
# ============================================================

_db_pool = None


def _get_db():
    global _db_pool
    if _db_pool is None:
        import psycopg2
        from psycopg2 import pool
        settings = get_settings().db
        _db_pool = pool.ThreadedConnectionPool(
            minconn=settings.pool_min_size,
            maxconn=settings.pool_max_size,
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.user,
            password=settings.password.get_secret_value(),
            connect_timeout=10,
        )
    return _db_pool


def _db_execute(query: str, params=None):
    pool = _get_db()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _db_fetch(query: str, params=None):
    pool = _get_db()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        pool.putconn(conn)


def _db_insert_returning(query: str, params=None):
    pool = _get_db()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def _db_transaction():
    """Get a connection for multi-statement transactions.

    Usage:
        with _db_transaction() as (conn, cur):
            cur.execute(...)
            cur.execute(...)
        # auto-commits on exit, rolls back on exception
    """
    pool = _get_db()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ============================================================
# Step 1: Raw → Segments
# ============================================================

def _process_single_file(task: Dict[str, Any]) -> Dict[str, Any]:
    """Worker: download → convert → upload segments."""
    from lai.pipeline.convert import (
        convert_file, build_output_documents, get_source_type,
        infer_language_doctype, release_gpu_memory,
    )

    file_path = task["file_path"]
    bucket_raw = task["bucket_raw"]
    bucket_segments = task["bucket_segments"]
    result = {"file_path": file_path, "status": "FAILED", "error": "", "count": 0}

    try:
        base = os.path.splitext(file_path)[0]
        target_key = f"{base}.segments.jsonl"

        if not task.get("force") and _object_exists(bucket_segments, target_key):
            return {**result, "status": "SKIPPED"}

        file_bytes = _download(bucket_raw, file_path)
        language, doc_type = infer_language_doctype(file_path)
        source_type = get_source_type(file_path)

        raw_segments = convert_file(file_bytes, file_path)
        if not raw_segments:
            return {**result, "error": "No segments extracted"}

        docs = build_output_documents(file_path, source_type, raw_segments, language, doc_type)

        buf = io.BytesIO()
        for doc in docs:
            buf.write((json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8"))

        _upload(bucket_segments, target_key, buf)
        result["status"] = "COMPLETED"
        result["count"] = len(docs)

    except Exception as e:
        result["error"] = str(e)[:500]
        logger.error(f"Failed: {file_path}: {e}")
    finally:
        release_gpu_memory()

    return result


def run_step1(args):
    """Step 1: Convert raw files to normalized segments."""
    settings = get_settings()
    bucket_raw = settings.pipeline.bucket_raw
    bucket_segments = settings.pipeline.bucket_segments

    logger.info("=" * 60)
    logger.info("Step 1: Raw → Normalized Segments")
    logger.info(f"  Raw bucket:      {bucket_raw}")
    logger.info(f"  Segments bucket:  {bucket_segments}")
    logger.info(f"  Source filter:    {args.source or '(all)'}")
    logger.info(f"  Force re-process: {getattr(args, 'force', False)}")
    logger.info("=" * 60)

    _ensure_bucket(bucket_segments)

    prefix = args.source or ""
    raw_files = list(_list_objects(bucket_raw, prefix=prefix))
    raw_files = [f for f in raw_files if not f["name"].endswith("/.folder")]
    logger.info(f"Found {len(raw_files)} raw files")

    if args.dry_run:
        from collections import Counter
        from lai.pipeline.convert import get_source_type
        types = Counter(get_source_type(f["name"]) for f in raw_files)
        for t, count in types.most_common():
            logger.info(f"  {t:25s} {count:>8d}")
        return

    max_workers = args.workers or settings.pipeline.max_workers
    if max_workers <= 0:
        max_workers = max(1, os.cpu_count() - 2)

    logger.info(f"Using {max_workers} workers")

    completed = failed = skipped = 0
    batch_size = 100

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for i in range(0, len(raw_files), batch_size):
            if _shutdown:
                break

            batch = raw_files[i:i + batch_size]
            force = getattr(args, "force", False)
            tasks = [{"file_path": f["name"], "bucket_raw": bucket_raw, "bucket_segments": bucket_segments, "force": force} for f in batch]

            futures = {executor.submit(_process_single_file, t): t for t in tasks}
            for future in as_completed(futures):
                if _shutdown:
                    break
                res = future.result()
                if res["status"] == "COMPLETED":
                    completed += 1
                elif res["status"] == "SKIPPED":
                    skipped += 1
                else:
                    failed += 1
                    logger.error(f"FAILED: {res['file_path']}: {res['error']}")

                if (completed + skipped) % 100 == 0:
                    logger.info(f"Progress: {completed} done, {skipped} skipped, {failed} failed")

    logger.info(f"Step 1 complete: {completed} converted, {skipped} skipped, {failed} failed")


# ============================================================
# Step 2: Segments → Parent-Child Chunks
# ============================================================

def _download_and_chunk(file_name: str, bucket: str):
    """Download a segment file from MinIO and chunk it.

    Returns pre-flattened parent_rows and child_rows ready for batch insert.
    """
    import re
    from lai.pipeline.chunk import process_document

    data = _download(bucket, file_name)
    raw = data.getvalue()
    start = raw.find(b"{")
    if start < 0:
        return [], []

    text = raw[start:].decode("utf-8", errors="replace")
    parent_rows = []  # flat list of tuples
    child_map = {}    # parent_chunk_id -> list of (chunk_id, text, char_count)

    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue

        parents, children_per_parent = process_document(doc)
        if not parents:
            continue

        doc_id = doc["doc_id"]
        source_file = doc["source_file"]
        language = doc["language"]
        doc_type = doc["doc_type"]
        metadata = json.dumps(doc.get("metadata", {}), ensure_ascii=False)

        for p_idx, parent in enumerate(parents):
            safe_section = re.sub(r"[^a-zA-Z0-9_]", "_", parent.get("section", "general"))[:50]
            parent_chunk_id = f"{doc_id}_{safe_section}_{p_idx:04d}"

            parent_rows.append((
                doc_id, parent_chunk_id, parent.get("section"),
                parent["text"], parent["char_count"],
                language, doc_type, source_file,
                parent.get("page_start"), parent.get("page_end"), metadata,
            ))

            children = []
            for c_idx, child in enumerate(children_per_parent[p_idx]):
                child_chunk_id = f"{parent_chunk_id}_c{c_idx:03d}"
                children.append((child_chunk_id, child["text"], child["char_count"]))
            child_map[parent_chunk_id] = children

    return parent_rows, child_map


def run_step2(args):
    """Step 2: Chunk segments into parent-child chunks in PostgreSQL.

    Downloads + chunks files, then batch-inserts into DB using execute_values.
    Files are processed sequentially; DB writes are batched for throughput.
    """
    from psycopg2.extras import execute_values
    import time as _time

    settings = get_settings()
    bucket_segments = settings.pipeline.bucket_segments
    chunk_cfg = settings.chunking

    # Fail fast if DB is unreachable
    try:
        _db_fetch("SELECT 1")
        logger.info("DB connection OK")
    except Exception as e:
        logger.error(f"Cannot connect to PostgreSQL: {e}")
        logger.error("Start the database first: cd Docker/database/pgvector && docker compose up -d")
        return

    db_batch = 20  # files to accumulate before flushing to DB

    logger.info("=" * 60)
    logger.info("Step 2: Segments → Parent-Child Chunks")
    logger.info(f"  Parent: {chunk_cfg.parent_target_chars}-{chunk_cfg.parent_max_chars} chars")
    logger.info(f"  Child:  {chunk_cfg.child_target_chars} target, {chunk_cfg.child_overlap_chars} overlap")
    logger.info(f"  DB batch: {db_batch} files per transaction")
    logger.info("=" * 60)

    prefix = args.source or ""
    seg_files = list(_list_objects(bucket_segments, prefix=prefix))
    seg_files = [f for f in seg_files if f["name"].endswith(".segments.jsonl")]
    logger.info(f"Found {len(seg_files)} segment files")

    if args.dry_run or not seg_files:
        return

    total_parents = total_children = files_done = files_failed = 0
    t0 = _time.time()

    # Accumulate rows across files, flush in batches
    pending_parents = []
    pending_child_map = {}
    pending_count = 0

    def _flush_to_db():
        """Batch-insert accumulated parents + children in one transaction."""
        nonlocal total_parents, total_children, pending_parents, pending_child_map, pending_count
        if not pending_parents:
            pending_count = 0
            return

        with _db_transaction() as (conn, cur):
            inserted = execute_values(
                cur,
                """INSERT INTO parent_chunks
                       (doc_id, chunk_id, section, content, char_count, language,
                        doc_type, source_file, page_start, page_end, metadata)
                   VALUES %s
                   ON CONFLICT (chunk_id) DO NOTHING
                   RETURNING id, chunk_id""",
                pending_parents,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                fetch=True,
            )

            total_parents += len(inserted)

            child_rows = []
            for parent_db_id, parent_chunk_id in inserted:
                for child_chunk_id, text, char_count in pending_child_map.get(parent_chunk_id, []):
                    child_rows.append((parent_db_id, child_chunk_id, text, char_count))

            if child_rows:
                execute_values(
                    cur,
                    """INSERT INTO child_chunks (parent_id, chunk_id, content, char_count)
                       VALUES %s
                       ON CONFLICT (chunk_id) DO NOTHING""",
                    child_rows,
                    template="(%s,%s,%s,%s)",
                )
                total_children += len(child_rows)

        logger.info(
            f"Flushed {pending_count} files — "
            f"{files_done}/{len(seg_files)} ({files_done*100//len(seg_files)}%), "
            f"{total_parents} parents, {total_children} children"
        )
        pending_parents = []
        pending_child_map = {}
        pending_count = 0

    # Process files one by one — simple, reliable, and fast enough
    logger.info(f"Starting processing loop over {len(seg_files)} files...")
    for i, seg_file in enumerate(seg_files):
        if _shutdown:
            logger.info("Shutdown flag detected, breaking loop")
            break

        file_name = seg_file["name"]
        logger.info(f"[{i+1}/{len(seg_files)}] {file_name}")
        try:
            parent_rows, child_map = _download_and_chunk(file_name, bucket_segments)
            logger.info(f"  -> {len(parent_rows)} parents, {len(child_map)} child groups")
            files_done += 1

            if parent_rows:
                pending_parents.extend(parent_rows)
                pending_child_map.update(child_map)
                pending_count += 1

            # Flush when enough files accumulated
            if pending_count >= db_batch:
                _flush_to_db()

        except Exception as e:
            logger.error(f"Failed: {file_name}: {e}")
            files_failed += 1

        # Progress every 500 files
        if (i + 1) % 500 == 0:
            elapsed = _time.time() - t0
            rate = files_done / elapsed if elapsed > 0 else 0
            eta = (len(seg_files) - files_done) / rate if rate > 0 else 0
            logger.info(
                f"Progress: {files_done}/{len(seg_files)} "
                f"({files_done*100//len(seg_files)}%), "
                f"{rate:.1f} files/s, ETA {eta/60:.0f}m"
            )

    # Flush remaining
    if pending_parents:
        try:
            _flush_to_db()
        except Exception as e:
            logger.error(f"Final DB batch insert failed: {e}")
            files_failed += pending_count

    elapsed = _time.time() - t0
    logger.info(
        f"Step 2 complete: {files_done} files, "
        f"{total_parents} parents, {total_children} children, "
        f"{files_failed} failed, {elapsed/60:.1f}m elapsed"
    )


# ============================================================
# Step 3: Domain Classification
# ============================================================

def run_step3(args):
    """Step 3: Classify parent chunks into legal domains.

    Writes to both parent_chunks.domain (latest) and chunk_classifications (history).
    Re-running with a new prompt/model version adds new history rows without
    deleting old ones. Use the latest_classifications view to query.
    """
    from lai.pipeline.classify import classify_batch, PROMPT_VERSION
    from psycopg2.extras import execute_values

    settings = get_settings()
    pipe = settings.pipeline
    batch_size = args.batch_size
    model_version = getattr(args, "model_version", "1") or "1"

    logger.info("=" * 60)
    logger.info("Step 3: Domain Classification (parent chunks)")
    logger.info(f"  LLM: {pipe.synth_llm_model}")
    logger.info(f"  Model version: {model_version}, Prompt version: {PROMPT_VERSION}")
    logger.info(f"  Batch size: {batch_size}")
    logger.info("=" * 60)

    # Reclassify: reset all domains (history is preserved in chunk_classifications)
    if getattr(args, "reclassify", False):
        count = _db_fetch("SELECT COUNT(*) FROM parent_chunks WHERE domain IS NOT NULL")[0][0]
        logger.info(f"Reclassify mode: resetting {count} existing classifications (history preserved)")
        _db_execute("UPDATE parent_chunks SET domain = NULL")

    # Count unclassified parents (domain IS NULL or empty)
    rows = _db_fetch("SELECT COUNT(*) FROM parent_chunks WHERE domain IS NULL OR domain = '{}'")
    total_unclassified = rows[0][0]
    logger.info(f"Found {total_unclassified} unclassified parent chunks")

    if args.dry_run or total_unclassified == 0:
        return

    classified = 0

    while not _shutdown:
        rows = _db_fetch("""
            SELECT id, content, doc_type, section
            FROM parent_chunks
            WHERE domain IS NULL OR domain = '{}'
            ORDER BY id
            LIMIT %s
        """, (batch_size,))

        if not rows:
            break

        chunks = [{"id": r[0], "content": r[1], "doc_type": r[2], "section": r[3]} for r in rows]

        results = classify_batch(
            chunks,
            llm_url=pipe.synth_llm_url,
            llm_model=pipe.synth_llm_model,
        )

        # Write to both tables in one transaction per batch
        with _db_transaction() as (conn, cur):
            history_rows = []
            for parent_id, (domains, raw_response) in results.items():
                # Update parent_chunks.domain (latest value)
                cur.execute(
                    "UPDATE parent_chunks SET domain = %s WHERE id = %s",
                    (domains, parent_id),
                )
                # Prepare history row
                history_rows.append((
                    parent_id, domains, pipe.synth_llm_model,
                    model_version, PROMPT_VERSION, raw_response,
                ))

            # Batch insert into classification history
            if history_rows:
                execute_values(
                    cur,
                    """INSERT INTO chunk_classifications
                           (parent_id, domain, model_name, model_version, prompt_version, raw_response)
                       VALUES %s""",
                    history_rows,
                    template="(%s,%s,%s,%s,%s,%s)",
                )

        classified += len(results)
        logger.info(f"Progress: {classified}/{total_unclassified} classified")

    logger.info(f"Step 3 complete: {classified} parent chunks classified")


# ============================================================
# Step 4: Contextual Enrichment
# ============================================================

def run_step4(args):
    """Step 4: Generate context prefixes for child chunks.

    Processes children in large concurrent batches — sends 16 LLM requests
    in parallel across multiple parents for maximum throughput.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from lai.pipeline.enrich import generate_context_prefix
    import time as _time

    settings = get_settings()
    pipe = settings.pipeline
    batch_size = args.batch_size
    max_concurrent = 16

    logger.info("=" * 60)
    logger.info("Step 4: Contextual Enrichment (child chunks)")
    logger.info(f"  LLM: {pipe.synth_llm_model}")
    logger.info(f"  Concurrent requests: {max_concurrent}")
    logger.info("=" * 60)

    # Count children without context_prefix
    rows = _db_fetch("SELECT COUNT(*) FROM child_chunks WHERE context_prefix IS NULL")
    total_unenriched = rows[0][0]
    logger.info(f"Found {total_unenriched} child chunks without context prefix")

    if args.dry_run or total_unenriched == 0:
        return

    enriched = 0
    failed = 0
    t0 = _time.time()

    while not _shutdown:
        # Fetch a batch of unenriched children with their parent info
        rows = _db_fetch("""
            SELECT c.id, c.content, p.content, p.doc_type, p.section, p.domain
            FROM child_chunks c
            JOIN parent_chunks p ON p.id = c.parent_id
            WHERE c.context_prefix IS NULL
            ORDER BY c.id
            LIMIT %s
        """, (batch_size,))

        if not rows:
            break

        # Build work items
        work = []
        for r in rows:
            work.append({
                "child_id": r[0], "child_text": r[1],
                "parent_text": r[2], "doc_type": r[3] or "",
                "section": r[4] or "", "domains": r[5] or [],
            })

        def _enrich_one(item):
            if len(item["child_text"]) < 50:
                return item["child_id"], ""
            prefix = generate_context_prefix(
                item["parent_text"], item["child_text"],
                doc_type=item["doc_type"], section=item["section"],
                domains=item["domains"],
                llm_url=pipe.synth_llm_url, llm_model=pipe.synth_llm_model,
            )
            return item["child_id"], prefix

        # Process batch concurrently
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(_enrich_one, w): w["child_id"] for w in work}
            for future in as_completed(futures):
                if _shutdown:
                    break
                try:
                    child_id, prefix = future.result(timeout=120)
                    _db_execute(
                        "UPDATE child_chunks SET context_prefix = %s WHERE id = %s",
                        (prefix, child_id),
                    )
                    enriched += 1
                except Exception as e:
                    child_id = futures[future]
                    logger.warning(f"Child {child_id} enrichment failed: {e}")
                    failed += 1

        elapsed = _time.time() - t0
        rate = enriched / elapsed if elapsed > 0 else 0
        eta = (total_unenriched - enriched) / rate if rate > 0 else 0
        logger.info(
            f"Progress: {enriched}/{total_unenriched} enriched "
            f"({enriched*100//max(total_unenriched,1)}%), "
            f"{rate:.1f} chunks/s, ETA {eta/60:.0f}m"
        )

    elapsed = _time.time() - t0
    logger.info(
        f"Step 4 complete: {enriched} enriched, {failed} failed, "
        f"{elapsed/60:.1f}m elapsed"
    )


# ============================================================
# Step 5: Synthetic Fine-Tuning Data
# ============================================================

def run_step5(args):
    """Step 5: Generate synthetic training samples from parent chunks."""
    from lai.pipeline.generate import generate_samples_for_parent

    settings = get_settings()
    pipe = settings.pipeline
    batch_size = args.batch_size
    target = args.max_samples or pipe.target_training_samples

    logger.info("=" * 60)
    logger.info("Step 5: Synthetic Fine-Tuning Data Generation")
    logger.info(f"  LLM: {pipe.synth_llm_model}")
    logger.info(f"  Target samples: {target}")
    logger.info(f"  Refusal ratio: {pipe.refusal_ratio}")
    logger.info("=" * 60)

    # Check current count
    rows = _db_fetch("SELECT COUNT(*) FROM training_samples")
    existing_count = rows[0][0]
    logger.info(f"Existing training samples: {existing_count}")

    if existing_count >= target:
        logger.info("Target already met!")
        return

    # Find parents that haven't been used for training yet (or used less)
    # Use a subquery to count samples per parent
    rows = _db_fetch("SELECT COUNT(DISTINCT parent_id) FROM training_samples")
    used_parents = rows[0][0]

    rows = _db_fetch("SELECT COUNT(*) FROM parent_chunks")
    total_parents = rows[0][0]
    logger.info(f"Parents: {total_parents} total, {used_parents} already used for training")

    if args.dry_run:
        samples_per_parent = (target - existing_count) / max(1, total_parents - used_parents) if total_parents > used_parents else 0
        logger.info(f"Would need ~{samples_per_parent:.1f} samples per remaining parent")
        return

    generated = 0
    total_count = existing_count
    parents_processed = 0
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0 = _time.time()

    # Process multiple parents concurrently
    # vLLM has max_num_seqs=16, KV cache <2% used → plenty of headroom
    # 8 parents * ~4 tasks staggered = ~16 requests in flight at once
    concurrent_parents = getattr(args, "workers", None) or 8

    while not _shutdown and total_count < target:
        # Fetch parents not yet used, or with few samples
        parent_rows = _db_fetch("""
            SELECT p.id, p.content, p.doc_type, p.section, p.domain,
                   COALESCE(ts.cnt, 0) as sample_count
            FROM parent_chunks p
            LEFT JOIN (
                SELECT parent_id, COUNT(*) as cnt FROM training_samples GROUP BY parent_id
            ) ts ON ts.parent_id = p.id
            WHERE p.char_count >= 200
            ORDER BY COALESCE(ts.cnt, 0) ASC, p.id
            LIMIT %s
        """, (batch_size,))

        if not parent_rows:
            break

        # Build parent dicts
        parents = []
        for p_row in parent_rows:
            parents.append({
                "id": p_row[0], "content": p_row[1],
                "doc_type": p_row[2], "section": p_row[3],
                "domain": p_row[4] or [],
            })

        # Process parents in concurrent batches
        for batch_start in range(0, len(parents), concurrent_parents):
            if _shutdown or total_count >= target:
                break

            batch = parents[batch_start:batch_start + concurrent_parents]

            with ThreadPoolExecutor(max_workers=concurrent_parents) as executor:
                future_to_parent = {
                    executor.submit(
                        generate_samples_for_parent, parent,
                        llm_url=pipe.synth_llm_url,
                        llm_model=pipe.synth_llm_model,
                        temperature=pipe.synth_temperature,
                        max_tokens=pipe.synth_max_tokens,
                        refusal_ratio=pipe.refusal_ratio,
                    ): parent
                    for parent in batch
                }

                for future in as_completed(future_to_parent):
                    parent = future_to_parent[future]
                    try:
                        samples = future.result(timeout=300)
                    except Exception as e:
                        logger.error(f"Parent {parent['id']} generation failed: {e}")
                        continue

                    for sample in samples:
                        _db_execute("""
                            INSERT INTO training_samples (parent_id, domain, task_type, messages, quality_score)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            sample["parent_id"],
                            sample["domain"],
                            sample["task_type"],
                            json.dumps(sample["messages"], ensure_ascii=False),
                            sample.get("quality_score"),
                        ))
                        generated += 1
                        total_count += 1
                    parents_processed += 1

                    # Progress every 10 parents
                    if parents_processed % 10 == 0:
                        elapsed = _time.time() - t0
                        rate = generated / elapsed if elapsed > 0 else 0
                        eta = (target - total_count) / rate / 60 if rate > 0 else 0
                        logger.info(
                            f"Progress: {total_count}/{target} samples "
                            f"({total_count*100//target}%), "
                            f"{parents_processed} parents done, "
                            f"{rate:.1f} samples/s, ETA {eta:.0f}m"
                        )

    elapsed = _time.time() - t0
    logger.info(f"Step 5 complete: {generated} new samples, {total_count} total, {elapsed/60:.1f}m elapsed")


# ============================================================
# Step 6: Embeddings → pgvector
# ============================================================

def _format_pgvector(emb):
    """Format a Python list of floats as a pgvector/halfvec string literal.

    pgvector accepts '[f1,f2,...]' for both `vector` and `halfvec` when
    cast explicitly (`::halfvec`). This avoids needing the pgvector-python
    adapter while still being type-safe.
    """
    return "[" + ",".join(f"{x:.7g}" for x in emb) + "]"


def run_step6(args):
    """Step 6: Generate embeddings and tsvector for child chunks.

    Qwen3-Embedding-8B outputs 4096-dim vectors natively. Stored as
    `halfvec(4096)` (fp16) in pgvector, which halves storage vs `vector`.
    No HNSW index — 4096 dims exceed pgvector's HNSW limit (4000 for halfvec).
    Exact cosine search is fast enough for 217K rows with pre-filters.

    In --local mode embeddings are written as INSERTs to a dedicated
    `child_embeddings(child_id PK, embedding BLOB)` table. UPDATE on a
    BLOB column in the main child_chunks table triggers btree rebalancing
    and is ~100× slower than a clean INSERT into a fresh table.
    """
    from lai.pipeline.embed import embed_batch, build_search_text

    settings = get_settings()
    embed_cfg = settings.embedding
    batch_size = args.batch_size
    embed_batch_size = args.embed_batch_size
    local_mode = getattr(args, "local", False)

    logger.info("=" * 60)
    logger.info("Step 6: Embeddings → pgvector (halfvec 4096)")
    logger.info(f"  Model: {embed_cfg.model}")
    logger.info(f"  URL: {embed_cfg.url}")
    logger.info(f"  Dimension: {embed_cfg.dimension}")
    logger.info(f"  Mode: {'local (SQLite)' if local_mode else 'postgres (halfvec)'}")
    logger.info("=" * 60)

    # In local mode, ensure the sidecar table exists and is populated from
    # any pre-existing embeddings in child_chunks (from an older run).
    if local_mode:
        pool = _get_db()
        pool.execute("""
            CREATE TABLE IF NOT EXISTS child_embeddings (
                child_id  INTEGER PRIMARY KEY,
                embedding BLOB NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migrate legacy rows where the embedding was written into child_chunks.
        pool.execute("""
            INSERT OR IGNORE INTO child_embeddings (child_id, embedding)
            SELECT id, embedding FROM child_chunks WHERE embedding IS NOT NULL
        """)
        total_unembedded = pool.fetch("""
            SELECT COUNT(*) FROM child_chunks c
            WHERE NOT EXISTS (SELECT 1 FROM child_embeddings e WHERE e.child_id = c.id)
        """)[0][0]
    else:
        rows = _db_fetch("SELECT COUNT(*) FROM child_chunks WHERE embedding IS NULL")
        total_unembedded = rows[0][0]

    logger.info(f"Found {total_unembedded} child chunks without embeddings")

    if args.dry_run:
        return

    embedded = 0
    last_id = 0  # primary-key cursor

    # Dual-write every batch of embeddings to disk alongside the DB insert.
    # A DB corruption otherwise forces a ~48h Step-6 re-run; files on disk
    # can be slurped back in ~20 min via scripts/restore_db_from_files.py.
    # Disabled with --no-backup-embeddings.
    backup_embeddings = getattr(args, "backup_embeddings", True)
    backup_dir = None
    backup_batch_idx = 0
    if backup_embeddings and local_mode:
        from pathlib import Path as _Path
        backup_dir = _Path(__file__).resolve().parents[3] / "data" / "lai-embeddings" / "child_embeddings"
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Continue numbering from whatever files already exist
        existing = sorted(backup_dir.glob("child_embeddings_*.npz"))
        if existing:
            last_name = existing[-1].stem
            try:
                backup_batch_idx = int(last_name.rsplit("_", 1)[-1]) + 1
            except ValueError:
                pass
        logger.info(f"  Embedding backup -> {backup_dir} (starting batch #{backup_batch_idx})")

    # Optional: exclude children whose parent's metadata.raw_type matches.
    # Used to drop noisy buckets (e.g. multilegalpile legal-mc4) without
    # re-chunking. Pilot A/B showed legal-mc4 doesn't hurt retrieval, but
    # it costs ~67% of multilegalpile embedding time with no measurable
    # gain, so dropping it is pure compute savings.
    exclude_raw = getattr(args, "exclude_raw_types", None) or []
    if exclude_raw:
        logger.info(f"  Excluding raw_type IN {exclude_raw}")
    exclude_join_sql = exclude_where_sql = ""
    exclude_params: tuple = ()
    if exclude_raw:
        placeholders = ",".join("?" * len(exclude_raw))
        exclude_join_sql = "JOIN parent_chunks p ON p.id = c.parent_id"
        exclude_where_sql = (
            f"AND (json_extract(p.metadata, '$.raw_type') IS NULL "
            f"     OR json_extract(p.metadata, '$.raw_type') NOT IN ({placeholders}))"
        )
        exclude_params = tuple(exclude_raw)

    while not _shutdown:
        if local_mode:
            # LEFT join against the sidecar table — primary-key index on
            # both sides, so this is O(log n) per iteration.
            child_rows = _db_fetch(f"""
                SELECT c.id, c.content, c.context_prefix
                FROM child_chunks c
                LEFT JOIN child_embeddings e ON e.child_id = c.id
                {exclude_join_sql}
                WHERE c.id > %s AND e.child_id IS NULL
                  {exclude_where_sql}
                ORDER BY c.id
                LIMIT %s
            """, (last_id, *exclude_params, batch_size))
        else:
            child_rows = _db_fetch("""
                SELECT id, content, context_prefix
                FROM child_chunks
                WHERE id > %s AND embedding IS NULL
                ORDER BY id
                LIMIT %s
            """, (last_id, batch_size))

        if not child_rows:
            break
        last_id = child_rows[-1][0]

        ids = [r[0] for r in child_rows]
        texts = [build_search_text(r[1], r[2] or "") for r in child_rows]

        try:
            embeddings = embed_batch(
                texts,
                embed_url=embed_cfg.url,
                embed_model=embed_cfg.model,
                batch_size=embed_batch_size,
                timeout=embed_cfg.timeout,
            )
        except Exception as e:
            logger.error(f"Embedding API error: {e}")
            break

        if local_mode:
            # SQLite path: INSERT into a dedicated child_embeddings table.
            # UPDATE on the main child_chunks table's embedding column
            # triggers btree page splits and is ~100× slower.
            # search_vector is not populated in local mode (SQLite lacks
            # tsvector); BM25 is only used on the postgres deployment.
            import struct
            pool = _get_db()  # patched to LocalDB
            insert_rows = [
                (child_id, struct.pack(f"{len(emb)}f", *emb))
                for child_id, emb in zip(ids, embeddings)
            ]
            pool.executemany(
                "INSERT OR REPLACE INTO child_embeddings (child_id, embedding) VALUES (?, ?)",
                insert_rows,
            )
            # Mirror to disk so the 48h of embedding work survives DB loss
            if backup_dir is not None:
                import numpy as _np
                fp = backup_dir / f"child_embeddings_{backup_batch_idx:06d}.npz"
                _np.savez_compressed(
                    fp,
                    child_ids=_np.asarray(ids, dtype=_np.int64),
                    embeddings=_np.asarray(embeddings, dtype=_np.float32),
                )
                backup_batch_idx += 1
            embedded += len(ids)
        else:
            # PostgreSQL path: cast to halfvec(4096)
            pool = _get_db()
            conn = pool.getconn()
            try:
                with conn.cursor() as cur:
                    for child_id, emb, text in zip(ids, embeddings, texts):
                        cur.execute("""
                            UPDATE child_chunks
                            SET embedding = %s::halfvec,
                                search_vector = to_tsvector('german', %s)
                            WHERE id = %s
                        """, (_format_pgvector(emb), text[:50000], child_id))
                conn.commit()
                embedded += len(ids)
            except Exception as e:
                conn.rollback()
                logger.error(f"DB update error: {e}")
                break
            finally:
                pool.putconn(conn)

        logger.info(f"Progress: {embedded}/{total_unembedded} embedded")

    # Only GIN index on search_vector (BM25); no HNSW — dims too large.
    # Exact cosine search is used at query time.
    if args.create_indexes and not _shutdown and not local_mode:
        logger.info("Creating GIN index on search_vector (BM25)...")
        _db_execute("""
            CREATE INDEX IF NOT EXISTS idx_child_search ON child_chunks
            USING gin (search_vector)
        """)
        logger.info(
            "Skipping HNSW on embedding: 4096 dims > pgvector halfvec HNSW "
            "limit of 4000. Use exact search at query time (fast enough for "
            "217K rows with pre-filters)."
        )
        logger.info("Indexes created successfully")

    logger.info(f"Step 6 complete: {embedded} child chunks embedded")


# ============================================================
# Main CLI
# ============================================================

def _build_log_name(step: str, args) -> str:
    """Build a descriptive log file name from step and args."""
    parts = [step]
    source = getattr(args, "source", None)
    if source:
        # "DD Reports/" -> "dd_reports"
        clean = source.strip("/").replace(" ", "_").replace("/", "_").lower()
        parts.append(clean)
    if getattr(args, "dry_run", False):
        parts.append("dryrun")
    return "_".join(parts)


def main():
    from lai.core.logging import setup_logging

    # Register signal handlers HERE, not at import time
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Parse args first (before logging setup) to get step name
    parser = argparse.ArgumentParser(description="LAI Data Processing Pipeline")
    sub = parser.add_subparsers(dest="step", required=True)

    # Step 1
    p1 = sub.add_parser("step1", help="Raw → Normalized Segments")
    p1.add_argument("--source", type=str, default=None, help="MinIO prefix filter (e.g., 'DD Reports/')")
    p1.add_argument("--workers", type=int, default=0, help="Max workers (0=auto)")
    p1.add_argument("--dry-run", action="store_true")
    p1.add_argument("--force", action="store_true", help="Re-process files even if segments already exist")

    # Step 2
    p2 = sub.add_parser("step2", help="Segments → Parent-Child Chunks")
    p2.add_argument("--source", type=str, default=None, help="Segment prefix filter")
    p2.add_argument("--dry-run", action="store_true")

    # Step 3
    p3 = sub.add_parser("step3", help="Domain Classification (parent chunks)")
    p3.add_argument("--batch-size", type=int, default=100, help="DB fetch batch size")
    p3.add_argument("--model-version", type=str, default="1", help="Version tag for classification history")
    p3.add_argument("--reclassify", action="store_true", help="Reset all domains and re-classify from scratch")
    p3.add_argument("--dry-run", action="store_true")

    # Step 4
    p4 = sub.add_parser("step4", help="Contextual Enrichment (child chunks)")
    p4.add_argument("--batch-size", type=int, default=50, help="DB fetch batch size")
    p4.add_argument("--dry-run", action="store_true")

    # Step 5
    p5 = sub.add_parser("step5", help="Synthetic Fine-Tuning Data Generation")
    p5.add_argument("--batch-size", type=int, default=50, help="DB fetch batch size")
    p5.add_argument("--max-samples", type=int, default=0, help="Stop after N samples (0=use config target)")
    p5.add_argument("--workers", type=int, default=0, help="Concurrent parents (0=auto, default 8)")
    p5.add_argument("--dry-run", action="store_true")

    # Step 6
    p6 = sub.add_parser("step6", help="Embeddings → pgvector")
    p6.add_argument("--batch-size", type=int, default=100, help="DB fetch batch size")
    p6.add_argument("--embed-batch-size", type=int, default=32, help="Embedding API batch size")
    p6.add_argument("--dry-run", action="store_true")
    p6.add_argument("--create-indexes", action="store_true", help="Create HNSW + GIN indexes after embedding")
    p6.add_argument("--exclude-raw-types", type=lambda s: [x for x in s.split(",") if x],
                    default=[],
                    help="Skip children whose parent's metadata.raw_type is in this comma-separated "
                         "list. Use for dropping noisy buckets like 'legal-mc4' from multilegalpile.")
    p6.add_argument("--no-backup-embeddings", dest="backup_embeddings", action="store_false",
                    help="Skip mirroring each embedding batch to disk at "
                         "data/lai-embeddings/. Default: backup enabled in --local mode.")
    p6.set_defaults(backup_embeddings=True)

    # Global --local flag on each subparser
    for p in [p1, p2, p3, p4, p5, p6]:
        p.add_argument("--local", action="store_true",
                        help="Run without Docker (read MinIO from disk, use SQLite instead of PostgreSQL)")
        p.add_argument("--minio-data-dir", type=str, default=None,
                        help="Path to MinIO bind-mount data dir (auto-detected if omitted)")
        p.add_argument("--db-path", type=str, default=None,
                        help="Path to local SQLite database (auto-detected if omitted)")

    args = parser.parse_args()

    # Activate local mode if requested
    if getattr(args, "local", False):
        from lai.pipeline.local_storage import patch_cli_for_local
        patch_cli_for_local(
            minio_data_dir=getattr(args, "minio_data_dir", None),
            db_path=getattr(args, "db_path", None),
        )

    # Setup logging with auto file output
    log_name = _build_log_name(args.step, args)
    log_file = setup_logging(log_name=log_name)

    step_fn = {
        "step1": lambda: (multiprocessing.set_start_method("spawn", force=True), run_step1(args)),
        "step2": lambda: run_step2(args),
        "step3": lambda: run_step3(args),
        "step4": lambda: run_step4(args),
        "step5": lambda: run_step5(args),
        "step6": lambda: run_step6(args),
    }

    fn = step_fn.get(args.step)
    if fn:
        t0 = time.perf_counter()
        if log_file:
            logger.info(f"Logging to: {log_file}")
        logger.info(f"Starting {args.step}...")
        try:
            fn()
        finally:
            elapsed = time.perf_counter() - t0
            hours, rem = divmod(elapsed, 3600)
            mins, secs = divmod(rem, 60)
            logger.info(f"{args.step} finished in {int(hours)}h {int(mins)}m {secs:.1f}s")


if __name__ == "__main__":
    main()
