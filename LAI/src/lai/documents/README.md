# `lai.documents` — document ingestion

Ingestion pipeline for user-supplied documents (PDF/DOCX): parse → chunk → embed →
store, so uploaded files become retrievable.

| Module | Role |
|---|---|
| `parser.py` | Document → text extraction. |
| `chunker.py` | Text → chunks. |
| `embedder.py` | Chunk → embeddings. |
| `repository.py` | Persistence for ingested documents. |
| `routes.py` | FastAPI routes for upload / document management. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
