import os
import time
import subprocess
from pathlib import Path

from minio import Minio
from minio.error import S3Error, BucketNotFound

# ========================================================
# Configuration
# ========================================================

PROCESSED_BUCKET = "lai-processed"   # pipeline outputs stored here

FAMILY = os.getenv("PIPELINE_FAMILY", "gesetzes")
LANGUAGE = os.getenv("PIPELINE_LANGUAGE", "en")

PIPELINE_SCRIPT = "pipeline_integrated.py"

SCAN_INTERVAL_SECONDS = 24 * 60 * 60  # 1 day

# MinIO Credentials
MINIO_ENDPOINT = "localhost:9000"
MINIO_ROOT_USER = "laiadmin"
MINIO_ROOT_PASSWORD = "superStrongPassword123!"
MINIO_USE_SSL = False

# ========================================================
# MinIO Client
# ========================================================

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ROOT_USER,
    secret_key=MINIO_ROOT_PASSWORD,
    secure=MINIO_USE_SSL
)

# ========================================================
# Helper Functions
# ========================================================

def list_all_buckets():
    """Return all MinIO buckets."""
    return client.list_buckets()


def list_all_objects(bucket_name: str):
    """Return all objects in a bucket (recursive)."""
    try:
        return client.list_objects(bucket_name, recursive=True)
    except BucketNotFound:
        return []


def is_processed(doc_id: str):
    """Check if final_chunks.jsonl exists."""
    final_key = f"{FAMILY}/{LANGUAGE}/{doc_id}/final_chunks.jsonl"
    try:
        client.stat_object(PROCESSED_BUCKET, final_key)
        return True
    except S3Error:
        return False


def download_to_temp(bucket: str, key: str):
    """Download file to temp directory."""
    temp_dir = Path("temp_files")
    temp_dir.mkdir(exist_ok=True)

    file_name = Path(key).name
    local_path = temp_dir / file_name

    response = client.get_object(bucket, key)
    try:
        with open(local_path, "wb") as f:
            f.write(response.read())
    finally:
        response.close()
        response.release_conn()

    return local_path


def run_pipeline(local_pdf_path: Path):
    """Run pipeline_integrated.py on local file."""
    print(f"[PIPELINE] Running pipeline on: {local_pdf_path}")
    subprocess.run(["python3", PIPELINE_SCRIPT, str(local_pdf_path)], check=True)


# ========================================================
# MAIN LOOP
# ========================================================

def watch_all_buckets():
    print("\n=======================================")
    print("  MinIO Global PDF Watcher")
    print("  Scans ALL buckets and ALL files")
    print("=======================================\n")

    while True:
        print("\n[SCAN] Starting global MinIO scan...")

        buckets = list_all_buckets()
        print(f"[INFO] Found {len(buckets)} buckets\n")

        for bucket in buckets:
            bucket_name = bucket.name
            print(f"\n[BUCKET] Scanning: {bucket_name}")

            for obj in list_all_objects(bucket_name):
                key = obj.object_name

                if not key.lower().endswith(".pdf"):
                    continue

                pdf_name = Path(key).name
                doc_id = Path(pdf_name).stem

                print(f"\n[FILE] Found PDF: {bucket_name}/{key}")
                print(f"  → doc_id = {doc_id}")

                if is_processed(doc_id):
                    print("  → Already processed. Skipping.")
                    continue

                print("  → Not processed. Downloading...")

                # Download the file locally
                try:
                    local_file = download_to_temp(bucket_name, key)
                except Exception as e:
                    print(f"[ERROR] Failed to download: {e}")
                    continue

                # Run pipeline
                try:
                    run_pipeline(local_file)
                    print("  → Processing complete.")
                except Exception as e:
                    print(f"[ERROR] Pipeline failed: {e}")
                finally:
                    # Cleanup temp file
                    if local_file.exists():
                        local_file.unlink()

        print(f"\n[SLEEP] Scan complete. Sleeping 1 day ({SCAN_INTERVAL_SECONDS} seconds)...")
        time.sleep(SCAN_INTERVAL_SECONDS)


# ========================================================
# Entry Point
# ========================================================

if __name__ == "__main__":
    watch_all_buckets()
