"""
lai.documents — Document ingestion and processing domain.

Owns: PDF parsing, chunking, embedding, deduplication, document CRUD.
Routes: POST /documents, GET /documents/{id}, DELETE /documents/{id}
DB: document_chunks, documents tables
Storage: MinIO buckets (user-documents, user-processed)
"""
