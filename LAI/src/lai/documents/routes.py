"""Document upload and management routes.

POST /documents/upload — upload PDF/DOCX
GET  /documents       — list user documents
DELETE /documents/{id} — delete document
"""

import uuid
from io import BytesIO

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from lai.core.config import get_settings
from lai.core.exceptions import DocumentProcessingError
from lai.core.logging import get_logger

logger = get_logger("lai.documents.routes")
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    x_user_id: str = Header(...),
):
    """Upload a document for processing (parse, chunk, embed, store)."""
    from lai.documents.chunker import Chunker
    from lai.documents.embedder import get_embedder
    from lai.documents.parser import parse_document
    from lai.documents.repository import create_user_schema, insert_chunks
    from lai.infra.minio import get_minio_client

    settings = get_settings().chunking
    if file.filename and not any(file.filename.lower().endswith(ext) for ext in settings.allowed_extensions):
        raise HTTPException(status_code=400, detail=f"Unsupported format. Allowed: {settings.allowed_extensions}")

    doc_id = str(uuid.uuid4())
    logger.info("Document upload: %s by user %s (doc_id=%s)", file.filename, x_user_id, doc_id)

    try:
        # Store original in MinIO
        content = await file.read()
        minio = get_minio_client()
        minio.upload_user_document(x_user_id, doc_id, BytesIO(content), file.filename or "document.pdf", file.content_type or "application/pdf")

        # Parse
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=file.filename or ".pdf", delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            parsed = parse_document(tmp.name)

        # Chunk
        chunker = Chunker()
        chunks = chunker.chunk_text(parsed.text)

        # Embed
        embedder = get_embedder()
        texts = [c.text for c in chunks]
        embeddings = await embedder.embed_batch(texts)

        # Ensure user schema exists
        schema = await create_user_schema(x_user_id)

        # Store in DB
        chunk_dicts = []
        for chunk, embedding in zip(chunks, embeddings):
            d = chunk.to_dict()
            d["document_id"] = uuid.UUID(doc_id)
            d["user_id"] = x_user_id
            d["embedding"] = embedding
            chunk_dicts.append(d)

        inserted = await insert_chunks(chunk_dicts, schema)
        logger.info("Document processed: %s -> %d chunks stored", doc_id, inserted)

        return {"doc_id": doc_id, "filename": file.filename, "chunks": inserted, "pages": parsed.page_count}

    except DocumentProcessingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Document upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Document processing failed")


@router.get("")
async def list_documents(x_user_id: str = Header(...)):
    """List documents for a user."""
    from lai.infra.minio import get_minio_client

    minio = get_minio_client()
    docs = []
    for obj in minio.list_objects(minio.user_documents_bucket, prefix=f"{x_user_id}/metadata/"):
        try:
            meta = minio.download_json(minio.user_documents_bucket, obj["name"])
            docs.append(meta)
        except Exception:
            pass
    return {"documents": docs}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, x_user_id: str = Header(...)):
    """Delete a document and all its chunks."""
    from lai.documents.repository import delete_document_chunks
    from lai.infra.minio import get_minio_client

    schema = f"user_{x_user_id.replace('-', '_')}"
    deleted_chunks = await delete_document_chunks(doc_id, schema)
    deleted_files = get_minio_client().delete_user_document(x_user_id, doc_id)

    logger.info("Deleted document %s: %d chunks, %d files", doc_id, deleted_chunks, deleted_files)
    return {"deleted_chunks": deleted_chunks, "deleted_files": deleted_files}
