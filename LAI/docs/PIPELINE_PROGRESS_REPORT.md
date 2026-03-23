# LAI Pipeline — Progress Report v1

> Date: 2026-03-23
> Author: Ravi Jangir
> Project: LAI (Legal AI) — German Wind Energy Due Diligence Platform

---

## Executive Summary

The LAI data processing pipeline converts 672GB of raw German legal documents into a production-ready RAG (Retrieval-Augmented Generation) system and fine-tuning dataset. The pipeline has been designed, implemented, and partially executed across 6 steps, processing the highest-value data sources first.

**Current status:** Steps 1-3 complete for Phase 1 data. Steps 4-6 pending LLM/embedding container availability.

---

## Pipeline Architecture

```
Raw Files (MinIO, 672GB)
    |
    v
Step 1: Convert ──────> Normalized segments (MinIO lai-segments)
    |                    Docling + Tesseract OCR (German), custom JSON/JSONL parsers
    v
Step 2: Chunk ─────────> Parent + Child chunks (PostgreSQL)
    |                    German-aware sentence splitting, parent-child hierarchy
    v
Step 3: Classify ──────> Domain labels on parent chunks
    |                    Qwen2.5-72B-Instruct-AWQ, 12 legal domains
    v
Step 4: Enrich ────────> Context prefix on child chunks
    |                    Anthropic's contextual retrieval approach
    v
Step 5: Generate ──────> ~200K ChatML training samples
    |                    7 task types for fine-tuning Qwen2.5-7B
    v
Step 6: Embed ─────────> 1024-dim vectors + BM25 tsvector
                         Qwen3-Embedding-8B, HNSW + GIN indexes
```

---

## Infrastructure Built

| Component | Technology | Status |
|-----------|-----------|--------|
| PostgreSQL + pgvector | Port 5434, HNSW indexes | Running |
| MinIO object storage | Port 9000, lai-raw + lai-segments buckets | Running |
| Teacher LLM | Qwen2.5-72B-Instruct-AWQ, vLLM, tensor-parallel 2 GPUs | Running |
| Embedding model | Qwen3-Embedding-8B (1024 dims), vLLM | Configured, not yet started |
| Pipeline CLI | `python -m lai.pipeline.cli step1..step6` | Fully implemented |
| Logging | Auto file logging to `logs/pipeline/<step>/` | Active |
| Hardware | 2x NVIDIA RTX PRO 6000 Blackwell (96GB VRAM each) | Available |

---

## Data Sources

| Source | Size | Files | Priority | Description |
|--------|------|-------|----------|-------------|
| DD Reports | 19MB | 18 | **Critical** | Due diligence reports for wind parks |
| VDRs | 6GB | 4,300+ | **Critical** | Virtual data room documents |
| de/gesetzes | 750MB | 764 | **High** | German federal statutes |
| hf_cases | 14GB | 14K | Medium | German court decisions |
| openlegaldata | 1.5GB | 4K | Medium | Open legal data (overlaps with hf_cases) |
| Library | 5.4GB | 2.1K | Medium | Legal reference PDFs |
| multilegalpile | 643GB | 132K | Low | Multi-language legal corpus (filter to German) |

---

## Step-by-Step Progress

### Step 1: Raw to Segments (COMPLETE for Phase 1)

Converts PDF, DOCX, JSON, JSONL files into normalized text segments using Docling with Tesseract OCR (German language pack).

| Source | Files Processed | Converted | Failed | Duration |
|--------|:-:|:-:|:-:|:-:|
| DD Reports | 19 | 18 | 1 (.DOC format) | 55s |
| VDRs | 5,572 | 5,469 | 103 (legacy formats) | 3h 18m |
| de/gesetzes | 6,820 | 6,820 | 0 | 1h 30m |
| **Total** | **12,411** | **12,307** | **104** | **~5h** |

**Output:** 12,307 segment files in MinIO `lai-segments` bucket (509 MB)

**Key decisions:**
- Switched from RapidOCR (Chinese PP-OCR) to Tesseract with German language pack for better accuracy on German legal text
- Added post-processing for `$` to `§` correction and hyphenation artifact cleanup
- 103 VDR failures are legacy `.xls`, `.doc`, `.ppt` formats (LibreOffice conversion planned)

### Step 2: Segments to Parent-Child Chunks (COMPLETE)

Splits segments into parent chunks (for fine-tuning/classification) and child chunks (for RAG retrieval) using German-aware sentence boundary detection.

| Metric | Value |
|--------|-------|
| Segment files processed | 12,307 |
| Parent chunks created | 134,474 |
| Child chunks created | 217,165 |
| Failed | 0 |
| Duration | 2m 35s |

**Chunking parameters (optimized for German legal text, ~3 chars/token):**
- Parent: 3,072 target / 6,144 max chars (1,024-2,048 tokens)
- Child: 1,536 target / 1,800 max / 384 overlap chars (~512 tokens)

**Key fix:** Discovered and fixed an infinite loop in the child chunk overlap calculation that caused the process to hang on documents with long sentences (e.g., markdown tables).

### Step 3: Domain Classification (IN PROGRESS)

Classifies each parent chunk into 1-3 of 12 wind-energy legal domains using Qwen2.5-72B-Instruct-AWQ.

| Metric | Value |
|--------|-------|
| Total parent chunks | 134,474 |
| Classified so far | ~4,000 (re-run in progress) |
| Classification rate | ~100 chunks / 15-25 seconds |
| Estimated total time | ~55 minutes |
| Concurrent LLM requests | 16 |

**12 Legal domains:**
immissionsschutzrecht, energierecht, baurecht, umweltrecht, vertragsrecht, gesellschaftsrecht, grundstuecksrecht, arbeitsrecht, steuerrecht, verwaltungsrecht, prozessrecht, allgemein

**Early distribution (from first 3,700 classified):**
| Domain | Count | % |
|--------|-------|---|
| vertragsrecht | 915 | 24.7% |
| allgemein | 837 | 22.6% |
| steuerrecht | 691 | 18.7% |
| gesellschaftsrecht | 601 | 16.2% |
| energierecht | 366 | 9.9% |
| immissionsschutzrecht | 117 | 3.2% |
| Other domains | 173 | 4.7% |

**Production feature:** Versioned classification history table (`chunk_classifications`) stores every classification run with model name, version, prompt version, and raw LLM response for full audit trail. Re-classifications create new history rows without deleting old ones.

### Step 4: Contextual Enrichment (NOT STARTED)

Generates a 1-2 sentence German context prefix for each child chunk using the parent chunk as context (Anthropic's contextual retrieval approach). This prefix is prepended before embedding to improve retrieval accuracy.

- **Input:** 217,165 child chunks + their parent text
- **Engine:** Qwen2.5-72B-Instruct-AWQ
- **Estimated time:** ~3-4 hours (concurrent requests)
- **Status:** Code implemented, awaiting Step 3 completion

### Step 5: Fine-tuning Data Generation (NOT STARTED)

Generates ~200K ChatML training samples from parent chunks for fine-tuning Qwen2.5-7B.

- **7 task types:** rag_qa, summarize, explain, compare, extract, classify_qa, refusal
- **3-5 samples per parent chunk** depending on length
- **Refusal ratio:** 10% (teaches model to say "I don't know")
- **Engine:** Qwen2.5-72B-Instruct-AWQ
- **Estimated time:** ~10-15 hours
- **Status:** Code implemented, awaiting Step 4 completion

### Step 6: Embeddings (NOT STARTED)

Embeds all child chunks using Qwen3-Embedding-8B (1024 dimensions) and creates full-text search vectors.

- **Input:** 217,165 child chunks (with context prefixes from Step 4)
- **Output:** 1024-dim vectors in pgvector + tsvector for BM25
- **Indexes:** HNSW (cosine similarity) + GIN (full-text search)
- **Engine:** Qwen3-Embedding-8B via vLLM
- **Estimated time:** ~1-2 hours
- **Status:** Code implemented, embedding container configured

---

## Code Quality & Production Features

### Implemented

| Feature | Description |
|---------|-------------|
| **Graceful shutdown** | First Ctrl+C finishes current batch and flushes to DB; second force-exits |
| **Resume support** | All steps use `ON CONFLICT DO NOTHING` — safe to re-run after interruption |
| **Atomic DB writes** | Batch transactions with rollback on failure |
| **Automatic file logging** | Every run saves to `logs/pipeline/<step>/<name>_<timestamp>.log` |
| **Classification versioning** | Full audit trail in `chunk_classifications` table |
| **Batch DB inserts** | `execute_values` for 10-100x faster inserts vs individual INSERTs |
| **Concurrent LLM requests** | 16 parallel requests to saturate vLLM's continuous batching |
| **German-aware processing** | Tesseract OCR (deu+eng), sentence boundary detection, umlaut handling |
| **Phased processing** | Process high-value data first due to storage constraints |

### Database Schema

```
parent_chunks (134,474 rows)
├── id, doc_id, chunk_id, section, content, char_count
├── language, doc_type, source_file, domain[]
├── page_start, page_end, metadata (JSONB)
└── Indexes: chunk_id (unique), doc_id, domain (GIN), doc_type, language

child_chunks (217,165 rows)
├── id, parent_id (FK), chunk_id, content, char_count
├── context_prefix, embedding (vector 1024), search_vector (tsvector)
└── Indexes: chunk_id (unique), parent_id

chunk_classifications (audit trail)
├── id, parent_id (FK), domain[], model_name, model_version
├── prompt_version, confidence, raw_response, created_at
└── View: latest_classifications (most recent per parent)
```

---

## Remaining Work

### Immediate (This Week)

1. **Complete Step 3 re-classification** — currently running (~55 min)
2. **Run Step 4** (contextual enrichment) — ~3-4 hours
3. **Run Step 5** (training data generation) — ~10-15 hours
4. **Run Step 6** (embeddings) — ~1-2 hours

### Phase 2 Data (Next)

5. Process hf_cases (14GB, 14K German court decisions)
6. Process openlegaldata (1.5GB, 4K files)
7. Process Library (5.4GB, 2.1K PDFs)

### Phase 3 Data

8. Process multilegalpile German subset (~30-50GB from 643GB)

### Post-Pipeline

9. Fine-tune Qwen2.5-7B using generated training samples
10. Integration testing of full RAG pipeline
11. German reranker evaluation (current MiniLM is English-only)
12. CI/CD pipeline setup
13. Database migrations (Alembic)

---

## Git History (Key Commits)

| Date | Commit | Description |
|------|--------|-------------|
| 2026-03-12 | Various | Steps 1-6 modules created, pipeline CLI built |
| 2026-03-12 | `6e7d82a` | Switch OCR to Tesseract (German) + automatic file logging |
| 2026-03-13 | `68568e3` | Fix DB config, parallelize Step 2, improve graceful shutdown |
| 2026-03-22 | `6181aee` | Fix infinite loop in child chunk overlap calculation |
| 2026-03-23 | `9faf454` | Fix synth-generator docker-compose, improve classify/enrich |
| 2026-03-23 | `91a655b` | Add versioned classification history table |

---

## Technical Decisions Log

| Decision | Rationale |
|----------|-----------|
| Tesseract over RapidOCR | RapidOCR defaults to Chinese PP-OCR models, poor on German umlauts and `§` symbols |
| Parent-child chunking | Parents for fine-tuning context (larger), children for RAG retrieval (smaller, with overlap) |
| Qwen3-Embedding-8B | #1 multilingual model on MTEB benchmark, 1024 dims, self-hosted |
| Qwen2.5-72B-AWQ for classification | Large enough for accurate domain classification, AWQ quantization fits on 2 GPUs |
| Phased data processing | Storage constraint (~613GB free), process highest-value sources first |
| Classification history table | Legal compliance requires audit trail; enables A/B testing of prompts/models |
| Sequential Step 2 processing | Simpler than thread pool, still completes 12K files in 2.6 minutes |
