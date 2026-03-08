"""MinIO object storage client.

Manages buckets for user documents and processed outputs.
"""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Generator

from minio import Minio
from minio.error import S3Error

from lai.core.config import get_settings
from lai.core.exceptions import LAIError
from lai.core.logging import get_logger

logger = get_logger("lai.infra.minio")


class StorageError(LAIError):
    """MinIO storage operation error."""


@dataclass
class StoredFile:
    bucket: str
    path: str
    size: int
    content_type: str
    etag: str


class MinIOClient:
    """MinIO object storage client for document and artifact management."""

    def __init__(self) -> None:
        settings = get_settings().minio
        self._endpoint = settings.endpoint
        self._access_key = settings.access_key
        self._secret_key = settings.secret_key.get_secret_value()
        self._secure = settings.use_ssl
        self.user_documents_bucket = settings.user_documents_bucket
        self.datasets_bucket = settings.datasets_bucket
        self._client: Minio | None = None
        logger.info("MinIO client initialized", extra={"endpoint": self._endpoint})

    def _get_client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )
        return self._client

    def ensure_buckets(self) -> None:
        client = self._get_client()
        for bucket in [self.user_documents_bucket, self.datasets_bucket]:
            try:
                if not client.bucket_exists(bucket):
                    client.make_bucket(bucket)
                    logger.info("Created bucket: %s", bucket)
            except S3Error as e:
                logger.warning("Bucket check/create failed for %s: %s", bucket, e)

    def upload_file(self, bucket: str, path: str, file: BinaryIO, content_type: str = "application/octet-stream") -> StoredFile:
        client = self._get_client()
        try:
            file.seek(0, 2)
            size = file.tell()
            file.seek(0)
            result = client.put_object(bucket, path, file, size, content_type=content_type)
            logger.debug("Uploaded %s/%s (%d bytes)", bucket, path, size)
            return StoredFile(bucket=bucket, path=path, size=size, content_type=content_type, etag=result.etag)
        except S3Error as e:
            logger.error("Upload failed: %s/%s: %s", bucket, path, e)
            raise StorageError(f"Failed to upload: {e}") from e

    def upload_bytes(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> StoredFile:
        return self.upload_file(bucket, path, io.BytesIO(data), content_type)

    def upload_json(self, bucket: str, path: str, data: Any) -> StoredFile:
        return self.upload_bytes(bucket, path, json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json")

    def download_file(self, bucket: str, path: str) -> bytes:
        client = self._get_client()
        try:
            response = client.get_object(bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise StorageError(f"File not found: {bucket}/{path}") from e
            raise StorageError(f"Download failed: {e}") from e

    def download_json(self, bucket: str, path: str) -> Any:
        return json.loads(self.download_file(bucket, path).decode())

    def delete_file(self, bucket: str, path: str) -> bool:
        client = self._get_client()
        try:
            client.remove_object(bucket, path)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise StorageError(f"Delete failed: {e}") from e

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        client = self._get_client()
        deleted = 0
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            client.remove_object(bucket, obj.object_name)
            deleted += 1
        logger.info("Deleted %d objects with prefix %s/%s", deleted, bucket, prefix)
        return deleted

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True) -> Generator[dict, None, None]:
        client = self._get_client()
        for obj in client.list_objects(bucket, prefix=prefix, recursive=recursive):
            yield {"name": obj.object_name, "size": obj.size, "last_modified": obj.last_modified}

    def exists(self, bucket: str, path: str) -> bool:
        client = self._get_client()
        try:
            client.stat_object(bucket, path)
            return True
        except S3Error:
            return False

    def upload_user_document(self, user_id: str, doc_id: str, file: BinaryIO, filename: str, content_type: str = "application/pdf") -> StoredFile:
        ext = Path(filename).suffix or ".pdf"
        path = f"{user_id}/uploads/{doc_id}{ext}"
        result = self.upload_file(self.user_documents_bucket, path, file, content_type)
        self.upload_json(self.user_documents_bucket, f"{user_id}/metadata/{doc_id}.json", {
            "doc_id": doc_id, "user_id": user_id, "filename": filename, "content_type": content_type,
        })
        return result

    def delete_user_document(self, user_id: str, doc_id: str) -> int:
        deleted = 0
        for ext in [".pdf", ".docx", ".json"]:
            if self.delete_file(self.user_documents_bucket, f"{user_id}/uploads/{doc_id}{ext}"):
                deleted += 1
        if self.delete_file(self.user_documents_bucket, f"{user_id}/metadata/{doc_id}.json"):
            deleted += 1
        deleted += self.delete_prefix(self.datasets_bucket, f"{user_id}/{doc_id}/")
        return deleted

    def check_health(self) -> dict:
        try:
            client = self._get_client()
            buckets = client.list_buckets()
            return {"status": "healthy", "buckets": len(buckets)}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


_minio_client: MinIOClient | None = None


def get_minio_client() -> MinIOClient:
    global _minio_client
    if _minio_client is None:
        _minio_client = MinIOClient()
    return _minio_client
