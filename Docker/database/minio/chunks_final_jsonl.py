# chunk_jsonl_minio.py

import os
import io
import json
from typing import List, Dict, Any, Tuple
from collections import defaultdict

from minio import Minio
from minio.error import S3Error

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================
# MinIO config
# ==============================

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "laiadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "superStrongPassword123!")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

BUCKET_PROCESSED = os.getenv("MINIO_BUCKET_PROCESSED", "lai-processed")

# Which family/language to process (matches your upload script)
FAMILY = os.getenv("CHUNK_FAMILY", "gesetzes")
LANGUAGE = os.getenv("CHUNK_LANGUAGE", "en")

INPUT_PREFIX = f"{FAMILY}/{LANGUAGE}/"   # e.g. "gesetzes/de/"

# Chunk size config
MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1200"))
MIN_CHARS = int(os.getenv("CHUNK_MIN_CHARS", "400"))
OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "200"))

# Default doc metadata (for now; can make smarter later)
DEFAULT_LANGUAGE_CODE = os.getenv("DOC_LANGUAGE_CODE", "de")
DEFAULT_DOC_TYPE = os.getenv("DOC_TYPE", "law")


client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_USE_SSL,
)


# ==============================
# Helpers
# ==============================

def load_jsonl_from_minio(bucket: str, key: str) -> List[Dict[str, Any]]:
    """Load a JSONL file (list of lines) from MinIO."""
    resp = client.get_object(bucket, key)
    try:
        raw = resp.read().decode("utf-8")
    finally:
        resp.close()
        resp.release_conn()

    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        lines.append(json.loads(line))
    return lines


def save_jsonl_to_minio(bucket: str, key: str, rows: List[Dict[str, Any]]) -> None:
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    data = body.encode("utf-8")
    stream = io.BytesIO(data)

    client.put_object(
        bucket_name=bucket,
        object_name=key,
        data=stream,
        length=len(data),
        content_type="application/jsonl",
    )


def group_by_section(lines: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group atomic lines by section name.
    Assumes each line has: {"content": ..., "metadata": { "section": ... }}.
    """
    sections: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for idx, row in enumerate(lines):
        meta = row.get("metadata", {})
        section = meta.get("section") or "General"
        # preserve original order with index
        row["_order"] = idx
        sections[section].append(row)

    # sort each section by original order (just in case)
    for sec in sections:
        sections[sec].sort(key=lambda r: r["_order"])
    return sections


def make_chunks_for_section(
    doc_id: str,
    section_name: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Build RAG chunks for one section using char-based windowing
    WITH overlap (OVERLAP_CHARS).
    """
    chunks: List[Dict[str, Any]] = []

    # 1) Precompute "segments" = cleaned pieces of text with pages
    segments = []
    for row in rows:
        meta = row.get("metadata", {})
        content = row.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        label = meta.get("type", "")
        # skip noise if needed
        if label in ("page_header", "page_footer"):
            continue

        text_piece = content.strip()
        if not text_piece:
            continue

        pages = meta.get("page_numbers") or []
        pages = [p for p in pages if isinstance(p, int)]

        segments.append({"text": text_piece, "pages": pages})

    if not segments:
        return chunks

    # 2) Sliding window over segments with char-based overlap
    i = 0
    chunk_index = 0
    source_file = rows[0].get("metadata", {}).get("source_file", "")

    while i < len(segments):
        chunk_text = ""
        chunk_pages = set()
        positions = []  # list of (seg_idx, cumulative_len)
        j = i

        # Grow chunk from segments[i:j]
        while j < len(segments):
            seg = segments[j]
            seg_text = seg["text"]

            # text we would add (with separator if needed)
            addition = (("\n\n" if chunk_text else "") + seg_text)
            new_len = len(chunk_text) + len(addition)

            # If chunk already has enough and adding this would exceed MAX → stop
            if chunk_text and new_len > MAX_CHARS and len(chunk_text) >= MIN_CHARS:
                break

            # Otherwise include this segment
            chunk_text += addition
            chunk_pages.update(seg["pages"])
            positions.append((j, len(chunk_text)))
            j += 1

            # Hard cap: if we hit MAX_CHARS, we stop this chunk
            if len(chunk_text) >= MAX_CHARS:
                break

        # If we somehow didn't include anything (e.g. single huge segment),
        # force-add at least that one to avoid infinite loop.
        if not positions:
            seg = segments[j]
            seg_text = seg["text"]
            chunk_text = seg_text[:MAX_CHARS]
            chunk_pages.update(seg["pages"])
            positions.append((j, len(chunk_text)))
            j += 1

        # Build chunk metadata
        page_start = min(chunk_pages) if chunk_pages else None
        page_end = max(chunk_pages) if chunk_pages else None

        chunk_id = f"{doc_id}_{section_name.replace(' ', '_').replace('§', 'S')}_{chunk_index:03d}"

        chunks.append(
            {
                "doc_id": doc_id,
                "section": section_name,
                "chunk_id": chunk_id,
                "text": chunk_text.strip(),
                "page_start": page_start,
                "page_end": page_end,
                "language": DEFAULT_LANGUAGE_CODE,
                "doc_type": DEFAULT_DOC_TYPE,
                "source_file": source_file,
            }
        )
        chunk_index += 1

        # 3) Compute new i based on OVERLAP_CHARS
        if j >= len(segments):
            # no more segments → done
            break

        total_len = positions[-1][1]
        overlap_target = max(0, total_len - OVERLAP_CHARS)

        # Find first segment inside this chunk whose cumulative length
        # goes beyond overlap_target → that segment index is new i
        new_i = i
        for seg_idx, cum_len in positions:
            if cum_len > overlap_target:
                new_i = seg_idx
                break

        # Safety: if overlap logic doesn't move us forward, just start at j
        if new_i <= i:
            new_i = j

        i = new_i

    return chunks



# ==============================
# Main
# ==============================

def run():
    print(f"[INFO] Building final chunks from bucket='{BUCKET_PROCESSED}', prefix='{INPUT_PREFIX}'")
    print("[INFO] Expecting '{family}/{lang}/{doc_id}/chunks.jsonl' per document")

    for obj in client.list_objects(BUCKET_PROCESSED, prefix=INPUT_PREFIX, recursive=True):
        key = obj.object_name
        if not key.endswith("/chunks.jsonl"):
            continue

        parts = key.split("/")
        if len(parts) < 4:
            print(f"[WARN] Unexpected key format, skipping: {key}")
            continue

        family, lang, doc_id = parts[0], parts[1], parts[2]

        print(f"\n[DOC] {family}/{lang}/{doc_id}")
        print(f"  - input : {key}")

        try:
            lines = load_jsonl_from_minio(BUCKET_PROCESSED, key)
            if not lines:
                print("  - no lines found, skipping")
                continue

            # derive doc_id from source_file if possible
            first_meta = lines[0].get("metadata", {})
            source_file = first_meta.get("source_file", "")
            if source_file and source_file.endswith(".pdf"):
                doc_id_clean = os.path.splitext(os.path.basename(source_file))[0]
            else:
                doc_id_clean = doc_id

            sections = group_by_section(lines)

            all_chunks: List[Dict[str, Any]] = []
            for sec_name, rows in sections.items():
                sec_chunks = make_chunks_for_section(doc_id_clean, sec_name, rows)
                all_chunks.extend(sec_chunks)

            print(f"  - sections: {len(sections)}")
            print(f"  - chunks  : {len(all_chunks)}")

            out_key = f"{family}/{lang}/{doc_id}/final_chunks.jsonl"
            save_jsonl_to_minio(BUCKET_PROCESSED, out_key, all_chunks)
            print(f"  - output : {out_key}")

        except S3Error as e:
            print(f"  [S3Error] {e}")
        except Exception as e:
            print(f"  [ERROR] Failed to process {key}: {e}")


if __name__ == "__main__":
    run()
