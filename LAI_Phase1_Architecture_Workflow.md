# LAI – Phase 1 Architecture + Workflow (Full Detailed Version)

## 1. Goal of Phase 1
Phase 1 establishes a **complete, production-grade local Retrieval-Augmented Generation (RAG) pipeline** for all documents relevant to the LAI system, including laws, guidelines, contracts, DD reports, and general literature.

All processing, storage, indexing, semantic search, and LLM inference run **fully on our own server**, with no external dependencies.

Phase 1 converts documents into structured, searchable chunks that can be used by the LAI Brain and future modules.

---

## 2. Component Overview

### 2.1 MinIO – Raw + Processed Document Storage
MinIO stores:
- All raw PDFs, DOCX, MD, TXT  
- Normalized JSON extracted from those files  
- Optional per-chunk JSON files  

Buckets:
- `lai-raw-docs` → raw uploads  
- `lai-processed-docs` → cleaned and chunked output  

MinIO acts as the “document memory” for the entire system.

---

### 2.2 Vector Database – Semantic Retrieval Layer
Two options:
- **pgvector** (Postgres extension, simplest unified DB)
- **Qdrant** (dedicated vector DB with better scaling)

Stores:
- Embeddings for all chunks  
- Payload metadata for filtering (document type, section, IDs, etc.)

Used for:
- Top-k similarity search  
- RAG context retrieval  

---

### 2.3 SQL Database – Metadata & Ingestion Tracking
Any SQL DB (Postgres/MySQL) tracks:
- Document registry  
- Chunk registry  
- Ingestion stages (uploaded → processed → chunked → indexed)  
- Paths to MinIO files  
- References linking chunks → documents  

This DB ensures the pipeline is fully traceable and resume-safe.

---

### 2.4 Local Models – Embeddings + LLM
Two categories of local models run on the server:

#### Local Embedding Model
Examples:
- BGE base  
- all-MiniLM-L6-v2  
- Mistral embedding variants  
- E5-large  

Used for:
- Chunk embeddings  
- Query embeddings  
- Filtering and semantic retrieval

#### Local LLM
Examples:
- LLaMA 3  
- Mistral 7B/8x22B  
- Gemma 2  
- Qwen  

Used for:
- Answer generation  
- Summaries  
- Legal explanations  
- Contract-specific responses  

All inference is local — no external API calls.

---

## 3. Complete Phase 1 Ingestion Workflow

### Step 1 – Upload & Register Documents
1. Upload raw file (PDF/DOCX/MD) into:  
   - MinIO → `lai-raw-docs/<type>/<filename>`
2. Insert SQL record in `documents` table:
   - `document_id`  
   - `type` (law/guideline/contract/etc.)  
   - `minio_path_raw`  
   - `status = 'uploaded'`

---

### Step 2 – Local Text Extraction
1. Download the raw file from MinIO  
2. Run **local text extraction**  
3. Extract:
   - Full text  
   - Page ranges  
   - Headings, sections  
4. Save normalized JSON to MinIO  
5. Update SQL: `status = 'processed'`

---

### Step 3 – Chunking
1. Download cleaned JSON  
2. Split text into 300–800 token chunks  
3. Insert chunk metadata into SQL  
4. Optional: Store chunk JSON in MinIO  
5. Update SQL: `status = 'chunked'`

---

### Step 4 – Embedding & Indexing
1. Generate embeddings using local model  
2. Store embeddings in pgvector/Qdrant  
3. Mark chunks as embedded  
4. Update document status to `indexed`  

---

## 4. Query-Time RAG Workflow (Production Flow)

### 1. User Query
User sends a question with optional filters.

### 2. Query Embedding
Local embedding model → query vector.

### 3. Vector Search
Vector DB retrieves top-k chunks with metadata.

### 4. Context Builder
Fetch chunk text + metadata from MinIO/SQL.

### 5. Local LLM Answer
Local LLM produces grounded answer with references.

---

## 5. Deliverables for Phase 1
- Local MinIO  
- Local SQL  
- Local pgvector/Qdrant  
- Local embedding model  
- Local LLM  
- Ingestion pipeline  
- Semantic search  
- RAG Q&A endpoint  
- Test UI/CLI  

---

## 6. Phase 1 Outcome
A complete fully local RAG foundation capable of:
- Handling large document sets  
- Producing legally grounded answers  
- Running offline end-to-end  
- Scaling into next phases (DD reports, LAI Brain)
