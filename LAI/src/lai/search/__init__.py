"""
lai.search — Search and retrieval domain.

Owns: query analysis, hybrid search (dense + BM25 + RRF), reranking, metadata filtering.
Routes: POST /query
DB: reads document_chunks (public + user schemas)
"""
