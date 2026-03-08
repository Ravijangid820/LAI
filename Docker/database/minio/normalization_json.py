import os
import io
import json
from typing import List, Dict, Any

from minio import Minio
from minio.error import S3Error

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# MinIO config (same as your existing scripts)
# ============================================================

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "laiadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "superStrongPassword123!")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

BUCKET_PROCESSED = os.getenv("MINIO_BUCKET_PROCESSED", "lai-processed")

# Which family/language to process
FAMILY = os.getenv("NORMALIZE_FAMILY", "gesetzes")
LANGUAGE = os.getenv("NORMALIZE_LANGUAGE", "en")

# Prefix in lai-processed where your JSON lives,
# based on your upload script:
#   {family}/{language}/{doc_id}/text.json
INPUT_PREFIX = f"{FAMILY}/{LANGUAGE}/"


# ============================================================
# MinIO client
# ============================================================

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_USE_SSL,
)


# ============================================================
# Docling helpers (your normalization logic, adapted)
# ============================================================

def table_to_markdown(table_obj: Dict) -> str:
    """Convert Docling table object into Markdown."""
    try:
        grid = table_obj.get("data", {}).get("grid", [])
        if not grid:
            return "[Empty Table]"

        if not isinstance(grid[0], list):
            return "[Complex Table Structure]"

        lines = []
        for row_idx, row in enumerate(grid):
            cells = []
            for cell in row:
                text = cell.get("text", "")
                if not isinstance(text, str):
                    text = str(text)
                cells.append(text.strip().replace("\n", " "))
            lines.append("| " + " | ".join(cells) + " |")
            if row_idx == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error processing table: {e}]"


def normalize_document(doc: Dict) -> List[Dict[str, Any]]:
    """
    Traverse Docling document and return linear list of chunks:
    { "content": str, "metadata": {...} }
    """
    chunks: List[Dict[str, Any]] = []

    texts_map = {f"#/texts/{i}": item for i, item in enumerate(doc.get("texts", []))}
    tables_map = {f"#/tables/{i}": item for i, item in enumerate(doc.get("tables", []))}
    groups_map = {f"#/groups/{i}": item for i, item in enumerate(doc.get("groups", []))}

    root_children = doc.get("body", {}).get("children", [])
    origin = doc.get("origin", {}) or {}
    source_filename = origin.get("filename", "unknown")

    current_section = "General"

    def process_ref(ref_obj: Dict):
        nonlocal current_section

        ref = ref_obj.get("$ref")
        if not ref:
            return

        item = None
        item_type = "unknown"

        if "/texts/" in ref:
            item = texts_map.get(ref)
            item_type = "text"
        elif "/tables/" in ref:
            item = tables_map.get(ref)
            item_type = "table"
        elif "/groups/" in ref:
            group = groups_map.get(ref)
            if group:
                for child in group.get("children", []):
                    process_ref(child)
            return

        if not item:
            return

        label = item.get("label", "")

        # Skip headers/footers
        if label in ("page_header", "page_footer"):
            return

        # Update current section
        if label == "section_header":
            head = item.get("text")
            if isinstance(head, str) and head.strip():
                current_section = head.strip()

        # Collect page numbers
        page_numbers = []
        for p in item.get("prov", []):
            page_no = p.get("page_no")
            if page_no is not None:
                page_numbers.append(page_no)

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


# ============================================================
# MinIO I/O helpers
# ============================================================

def load_json_from_minio(bucket: str, key: str) -> Dict:
    """Get JSON object from MinIO and parse it."""
    resp = client.get_object(bucket, key)
    try:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    finally:
        resp.close()
        resp.release_conn()


def save_jsonl_to_minio(bucket: str, key: str, chunks: List[Dict]):
    """Save list of {content, metadata} as JSONL to MinIO."""
    lines = [json.dumps(c, ensure_ascii=False) for c in chunks]
    body = ("\n".join(lines) + "\n").encode("utf-8")
    stream = io.BytesIO(body)

    client.put_object(
        bucket_name=bucket,
        object_name=key,
        data=stream,
        length=len(body),
        content_type="application/jsonl",
    )


# ============================================================
# Main routine
# ============================================================

def run():
    print(f"[INFO] Normalizing from bucket='{BUCKET_PROCESSED}', prefix='{INPUT_PREFIX}'")
    print("[INFO] Looking for .../{doc_id}/text.json objects")

    for obj in client.list_objects(
        BUCKET_PROCESSED, prefix=INPUT_PREFIX, recursive=True
    ):
        key = obj.object_name

        # We only care about ".../text.json"
        if not key.endswith("/text.json"):
            continue

        # key pattern: {family}/{language}/{doc_id}/text.json
        parts = key.split("/")
        if len(parts) < 4:
            print(f"[WARN] Unexpected key format, skipping: {key}")
            continue

        family, lang, doc_id = parts[0], parts[1], parts[2]

        print(f"\n[DOC] {family}/{lang}/{doc_id}")
        print(f"  - input : {key}")

        try:
            doc = load_json_from_minio(BUCKET_PROCESSED, key)
            chunks = normalize_document(doc)
            print(f"  - chunks: {len(chunks)}")

            out_key = f"{family}/{lang}/{doc_id}/chunks.jsonl"
            save_jsonl_to_minio(BUCKET_PROCESSED, out_key, chunks)
            print(f"  - output: {out_key}")

        except S3Error as e:
            print(f"  [S3Error] {e}")
        except Exception as e:
            print(f"  [ERROR] Failed to process {key}: {e}")


if __name__ == "__main__":
    run()
