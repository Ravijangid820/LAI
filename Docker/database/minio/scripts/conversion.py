# ======================================================================
#   INTEGRATED PIPELINE SCRIPT (Option B - Full MinIO Pipeline)
#   PDF → DOC JSON → NORMALIZE → CHUNKS → FINAL_CHUNKS
#   Accepts a SINGLE PDF file path
# ======================================================================

import argparse
import json
import os
import io
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from tqdm import tqdm
from minio import Minio
from minio.error import S3Error

# ---- Docling ----
from docling.document_converter import DocumentConverter


# ======================================================================
# ENVIRONMENT CONFIG
# ======================================================================

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "laiadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "superStrongPassword123!")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

BUCKET_PROCESSED = os.getenv("MINIO_BUCKET_PROCESSED", "lai-processed")

FAMILY = os.getenv("PIPELINE_FAMILY", "gesetzes")
LANGUAGE = os.getenv("PIPELINE_LANGUAGE", "en")

# Chunk config (final stage)
MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1200"))
MIN_CHARS = int(os.getenv("CHUNK_MIN_CHARS", "400"))
OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "200"))

DEFAULT_LANGUAGE_CODE = os.getenv("DOC_LANGUAGE_CODE", "de")
DEFAULT_DOC_TYPE = os.getenv("DOC_TYPE", "law")


# ======================================================================
# MinIO Client
# ======================================================================

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_USE_SSL,
)


# ======================================================================
# STEP 1 — PDF → DOC JSON
# ======================================================================

def get_converter() -> DocumentConverter:
    return DocumentConverter()


def convert_pdf_to_docling_json(pdf_path: Path) -> Dict[str, Any]:
    converter = get_converter()
    result = converter.convert(pdf_path)
    doc = result.document

    doc_dict = doc.export_to_dict()
    doc_dict.setdefault("metadata", {})
    doc_dict["metadata"]["source_path"] = str(pdf_path)
    doc_dict["metadata"]["file_name"] = pdf_path.name

    return doc_dict


def upload_doc_to_minio(doc_dict: Dict, family: str, lang: str, doc_id: str):
    body = json.dumps(doc_dict, ensure_ascii=False).encode("utf-8")
    stream = io.BytesIO(body)

    key = f"{family}/{lang}/{doc_id}/text.json"

    client.put_object(
        bucket_name=BUCKET_PROCESSED,
        object_name=key,
        data=stream,
        length=len(body),
        content_type="application/json",
    )
    return key


# ======================================================================
# STEP 2 — NORMALIZATION
# ======================================================================

def table_to_markdown(table_obj: Dict) -> str:
    try:
        grid = table_obj.get("data", {}).get("grid", [])
        if not grid:
            return "[Empty Table]"
        if not isinstance(grid[0], list):
            return "[Complex Table Structure]"

        lines = []
        for idx, row in enumerate(grid):
            cells = [str(cell.get("text", "")).strip().replace("\n", " ") for cell in row]
            lines.append("| " + " | ".join(cells) + " |")
            if idx == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error processing table: {e}]"


def normalize_document(doc: Dict) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []

    texts_map = {f"#/texts/{i}": t for i, t in enumerate(doc.get("texts", []))}
    tables_map = {f"#/tables/{i}": t for i, t in enumerate(doc.get("tables", []))}
    groups_map = {f"#/groups/{i}": t for i, t in enumerate(doc.get("groups", []))}

    root_children = doc.get("body", {}).get("children", [])
    source_filename = doc.get("origin", {}).get("filename", "unknown")

    current_section = "General"

    def process_ref(ref_obj):
        nonlocal current_section

        ref = ref_obj.get("$ref")
        if not ref:
            return

        item = None
        item_type = ""

        if "/texts/" in ref:
            item = texts_map.get(ref)
            item_type = "text"
        elif "/tables/" in ref:
            item = tables_map.get(ref)
            item_type = "table"
        elif "/groups/" in ref:
            group = groups_map.get(ref)
            if group:
                for c in group.get("children", []):
                    process_ref(c)
            return

        if not item:
            return

        label = item.get("label", "")

        if label in ("page_header", "page_footer"):
            return

        if label == "section_header":
            head = item.get("text", "")
            if isinstance(head, str) and head.strip():
                current_section = head.strip()

        page_numbers = [
            p.get("page_no") for p in item.get("prov", []) if p.get("page_no") is not None
        ]

        metadata = {
            "source_file": source_filename,
            "section": current_section,
            "type": label or item_type,
            "page_numbers": page_numbers,
        }

        if item_type == "table":
            content = table_to_markdown(item)
            metadata["is_table"] = True
        else:
            content = item.get("text", "")
            if not isinstance(content, str):
                content = str(content)

        if content.strip():
            chunks.append({"content": content, "metadata": metadata})

    for child in root_children:
        process_ref(child)

    return chunks


def upload_chunks_jsonl(chunks: List[Dict], family: str, lang: str, doc_id: str):
    key = f"{family}/{lang}/{doc_id}/chunks.jsonl"

    body = ("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n").encode("utf-8")
    stream = io.BytesIO(body)

    client.put_object(
        bucket_name=BUCKET_PROCESSED,
        object_name=key,
        data=stream,
        length=len(body),
        content_type="application/jsonl",
    )
    return key


# ======================================================================
# STEP 3 — FINAL CHUNKING
# ======================================================================

def group_by_section(lines: List[Dict[str, Any]]):
    out = {}
    for idx, row in enumerate(lines):
        sec = row.get("metadata", {}).get("section") or "General"
        row["_order"] = idx
        out.setdefault(sec, []).append(row)

    for sec in out:
        out[sec].sort(key=lambda x: x["_order"])
    return out


def make_chunks_for_section(doc_id, section_name, rows):
    segments = []

    for row in rows:
        meta = row.get("metadata", {})
        content = row.get("content", "")

        if meta.get("type") in ("page_header", "page_footer"):
            continue

        text_piece = str(content).strip()
        if not text_piece:
            continue

        pages = meta.get("page_numbers") or []
        segments.append({"text": text_piece, "pages": pages})

    if not segments:
        return []

    chunks = []
    i = 0
    chunk_idx = 0
    source_file = rows[0].get("metadata", {}).get("source_file", "")

    while i < len(segments):
        text = ""
        pages = set()
        positions = []
        j = i

        while j < len(segments):
            seg = segments[j]
            addition = (("\n\n" if text else "") + seg["text"])
            new_len = len(text) + len(addition)

            if text and new_len > MAX_CHARS and len(text) >= MIN_CHARS:
                break

            text += addition
            pages.update(seg["pages"])
            positions.append((j, len(text)))
            j += 1

            if len(text) >= MAX_CHARS:
                break

        if not positions:
            seg = segments[j]
            text = seg["text"][:MAX_CHARS]
            pages.update(seg["pages"])
            positions.append((j, len(text)))
            j += 1

        pg_start = min(pages) if pages else None
        pg_end = max(pages) if pages else None

        chunk_id = f"{doc_id}_{section_name.replace(' ', '_')}_{chunk_idx:03d}"

        chunks.append({
            "doc_id": doc_id,
            "section": section_name,
            "chunk_id": chunk_id,
            "text": text.strip(),
            "page_start": pg_start,
            "page_end": pg_end,
            "language": DEFAULT_LANGUAGE_CODE,
            "doc_type": DEFAULT_DOC_TYPE,
            "source_file": source_file,
        })

        chunk_idx += 1

        if j >= len(segments):
            break

        total_len = positions[-1][1]
        overlap_target = max(0, total_len - OVERLAP_CHARS)

        new_i = i
        for seg_idx, cum_len in positions:
            if cum_len > overlap_target:
                new_i = seg_idx
                break

        if new_i <= i:
            new_i = j

        i = new_i

    return chunks


def upload_final_chunks(chunks: List[Dict], family: str, lang: str, doc_id: str):
    key = f"{family}/{lang}/{doc_id}/final_chunks.jsonl"

    body = ("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n").encode("utf-8")
    stream = io.BytesIO(body)

    client.put_object(
        bucket_name=BUCKET_PROCESSED,
        object_name=key,
        data=stream,
        length=len(body),
        content_type="application/jsonl"
    )
    return key


# ======================================================================
# MAIN PIPELINE — SINGLE PDF FILE
# ======================================================================

def run_pipeline_for_file(pdf_file: Path):

    if not pdf_file.exists():
        print(f"[ERROR] File not found: {pdf_file}")
        return

    if pdf_file.suffix.lower() != ".pdf":
        print(f"[ERROR] Not a PDF file: {pdf_file}")
        return

    doc_id = pdf_file.stem
    print(f"\n[PDF] Processing: {pdf_file.name}")

    # STEP 1: Convert PDF → Docling JSON
    doc = convert_pdf_to_docling_json(pdf_file)
    key1 = upload_doc_to_minio(doc, FAMILY, LANGUAGE, doc_id)
    print(f"  → Uploaded text.json: {key1}")

    # STEP 2: Normalize
    chunks = normalize_document(doc)
    key2 = upload_chunks_jsonl(chunks, FAMILY, LANGUAGE, doc_id)
    print(f"  → Uploaded chunks.jsonl: {key2}")

    # STEP 3: Final Chunking
    sections = group_by_section(chunks)
    final_chunks = []
    for sec_name, rows in sections.items():
        final_chunks += make_chunks_for_section(doc_id, sec_name, rows)

    key3 = upload_final_chunks(final_chunks, FAMILY, LANGUAGE, doc_id)
    print(f"  → Uploaded final_chunks.jsonl: {key3}")

    print("\n[✔] Pipeline complete for:", pdf_file.name)


# ======================================================================
# ENTRY POINT
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Integrated Docling → Normalize → Chunking Pipeline (Single PDF Version)"
    )
    parser.add_argument(
        "pdf_file",
        type=str,
        help="Path to a single PDF file"
    )

    args = parser.parse_args()
    run_pipeline_for_file(Path(args.pdf_file).resolve())
