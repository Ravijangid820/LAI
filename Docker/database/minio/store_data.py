import os
import csv
from pathlib import Path
from typing import Optional, Dict, Tuple

from minio import Minio
from minio.error import S3Error

# Optional: load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================
# CONFIG: MinIO connection
# ==============================

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "laiadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "superStrongPassword123!")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

BUCKET_RAW = os.getenv("MINIO_BUCKET_RAW", "lai-raw")
BUCKET_PROCESSED = os.getenv("MINIO_BUCKET_PROCESSED", "lai-processed")

# ==============================
# CONFIG: Local data structure
# ==============================
# doc_id is derived from filename WITHOUT extension.
#
# For each block here:
#   - family:   high-level corpus (gesetzes, noise_pollution, land_rules, ...)
#   - language: ISO code (de, en, ...)
#   - *_dir:    local folders for each format
#
# For now we only configure "gesetzes" in German and English.
# Update the paths below to your real directories.

DATA_FAMILIES = [
    {
        "family": "gesetzes",
        "language": "de",
        "pdf_dir": "/data/projects/lai/Data/german/gesetze_pdfs",
        "md_dir": "/data/projects/lai/Data/german/md",
        "json_dir": "/data/projects/lai/Data/german/json",
    },
    {
        "family": "gesetzes",
        "language": "en",
        "pdf_dir": "/data/projects/lai/Data/english/english_pdfs",
        "md_dir": "/data/projects/lai/Data/english/md",
        "json_dir": "/data/projects/lai/Data/english/json",
    },
    # Later you can add more blocks here for other families/languages
]

# ==============================
# MinIO client setup
# ==============================

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_USE_SSL,
)


def ensure_bucket(name: str) -> None:
    """Create bucket if it does not exist."""
    found = client.bucket_exists(name)
    if not found:
        print(f"[MinIO] Creating bucket: {name}")
        client.make_bucket(name)
    else:
        print(f"[MinIO] Bucket exists: {name}")


def collect_files_by_doc_id(folder: Optional[str], exts: Tuple[str, ...]) -> Dict[str, Path]:
    """
    Walk a folder and build a mapping:
      doc_id -> file_path

    doc_id is filename without extension.
    Only includes files matching given extensions.
    """
    result: Dict[str, Path] = {}
    if not folder:
        return result

    folder_path = Path(folder)
    if not folder_path.exists():
        print(f"[WARN] Folder does not exist, skipping: {folder}")
        return result

    for path in folder_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        doc_id = path.stem  # filename without extension
        result[doc_id] = path
    return result


def upload_file(bucket: str, local_path: Path, object_name: str, content_type: str) -> None:
    """Upload single file to MinIO with logging."""
    local_path = local_path.resolve()
    print(f"[UPLOAD] {local_path} -> {bucket}/{object_name}")

    client.fput_object(
        bucket_name=bucket,
        object_name=object_name,
        file_path=str(local_path),
        content_type=content_type,
    )


def main() -> None:
    # Ensure buckets exist
    ensure_bucket(BUCKET_RAW)
    ensure_bucket(BUCKET_PROCESSED)

    # CSV for documents metadata (for Postgres seed)
    csv_rows = []
    csv_headers = [
        "family",
        "language",
        "doc_id",
        "raw_bucket",
        "raw_key",
        "processed_bucket",
        "processed_md_key",
        "processed_json_key",
    ]

    for family_cfg in DATA_FAMILIES:
        family = family_cfg["family"]
        language = family_cfg.get("language", "unknown")

        print(f"\n=== Processing family: {family} (lang={language}) ===")

        pdf_files = collect_files_by_doc_id(family_cfg.get("pdf_dir"), (".pdf",))
        md_files = collect_files_by_doc_id(family_cfg.get("md_dir"), (".md", ".markdown"))
        json_files = collect_files_by_doc_id(family_cfg.get("json_dir"), (".json",))

        # Union of all doc_ids present in any of the three formats
        all_doc_ids = set(pdf_files.keys()) | set(md_files.keys()) | set(json_files.keys())

        if not all_doc_ids:
            print(f"[INFO] No files found for family={family}, lang={language}, skipping.")
            continue

        print(f"[INFO] Found {len(all_doc_ids)} documents in family={family}, lang={language}")

        for doc_id in sorted(all_doc_ids):
            pdf_path = pdf_files.get(doc_id)
            md_path = md_files.get(doc_id)
            json_path = json_files.get(doc_id)

            # Construct MinIO keys (now include language)
            raw_key: Optional[str] = None
            processed_md_key: Optional[str] = None
            processed_json_key: Optional[str] = None

            # 1) Upload PDF to lai-raw/{family}/{language}/{doc_id}/source.pdf
            if pdf_path:
                raw_key = f"{family}/{language}/{doc_id}/source.pdf"
                try:
                    upload_file(
                        bucket=BUCKET_RAW,
                        local_path=pdf_path,
                        object_name=raw_key,
                        content_type="application/pdf",
                    )
                except S3Error as e:
                    print(f"[ERROR] Failed to upload PDF for {family}:{language}:{doc_id} -> {e}")

            # 2) Upload MD to lai-processed/{family}/{language}/{doc_id}/text.md
            if md_path:
                processed_md_key = f"{family}/{language}/{doc_id}/text.md"
                try:
                    upload_file(
                        bucket=BUCKET_PROCESSED,
                        local_path=md_path,
                        object_name=processed_md_key,
                        content_type="text/markdown",
                    )
                except S3Error as e:
                    print(f"[ERROR] Failed to upload MD for {family}:{language}:{doc_id} -> {e}")

            # 3) Upload JSON to lai-processed/{family}/{language}/{doc_id}/text.json
            if json_path:
                processed_json_key = f"{family}/{language}/{doc_id}/text.json"
                try:
                    upload_file(
                        bucket=BUCKET_PROCESSED,
                        local_path=json_path,
                        object_name=processed_json_key,
                        content_type="application/json",
                    )
                except S3Error as e:
                    print(f"[ERROR] Failed to upload JSON for {family}:{language}:{doc_id} -> {e}")

            # If nothing uploaded, skip adding metadata row
            if not any([raw_key, processed_md_key, processed_json_key]):
                print(f"[WARN] No files available for {family}:{language}:{doc_id}, skipping metadata row.")
                continue

            csv_rows.append(
                {
                    "family": family,
                    "language": language,
                    "doc_id": doc_id,
                    "raw_bucket": BUCKET_RAW if raw_key else "",
                    "raw_key": raw_key or "",
                    "processed_bucket": BUCKET_PROCESSED if (processed_md_key or processed_json_key) else "",
                    "processed_md_key": processed_md_key or "",
                    "processed_json_key": processed_json_key or "",
                }
            )

    # Write CSV
    if csv_rows:
        output_csv = Path("documents_seed.csv")
        print(f"\n[INFO] Writing metadata CSV: {output_csv.resolve()}")
        with output_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers)
            writer.writeheader()
            writer.writerows(csv_rows)
    else:
        print("\n[INFO] No documents were uploaded, CSV will not be created.")


if __name__ == "__main__":
    try:
        main()
        print("\n[DONE] Migration finished.")
    except Exception as e:
        print("[FATAL] Unexpected error:", e)
