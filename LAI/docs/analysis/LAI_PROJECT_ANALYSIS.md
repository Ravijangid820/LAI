# LAI (Legal AI) - Comprehensive Project Analysis

> **Generated:** 2026-03-05
> **Scope:** Full analysis of LAIV1 through LAIV4, supporting infrastructure, data processing, and deployment

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Structure](#2-directory-structure)
3. [Version Evolution Summary](#3-version-evolution-summary)
4. [LAIV1 - Modular Risk Assessment Platform](#4-laiv1---modular-risk-assessment-platform)
5. [LAIV2 - Fine-Tuning & Data Pipeline](#5-laiv2---fine-tuning--data-pipeline)
6. [LAIV3 - Production-Grade RAG System](#6-laiv3---production-grade-rag-system)
7. [LAIV4 - Multi-Agent Agentic Platform](#7-laiv4---multi-agent-agentic-platform)
8. [Models Comparison Across Versions](#8-models-comparison-across-versions)
9. [Numerical Parameters Comparison](#9-numerical-parameters-comparison)
10. [Infrastructure & Docker Services](#10-infrastructure--docker-services)
11. [Data Processing Pipeline](#11-data-processing-pipeline)
12. [Training Architecture & Datasets](#12-training-architecture--datasets)
13. [Supporting Directories](#13-supporting-directories)
14. [Architecture Decision Records](#14-architecture-decision-records)
15. [Key File Reference](#15-key-file-reference)

---

## 1. Project Overview

**LAI (Legal AI)** is a German legal AI platform designed for **wind energy due diligence**. It processes legal documents (contracts, permits, regulations, court decisions) and provides risk assessments, document analysis, and question-answering capabilities using RAG (Retrieval-Augmented Generation) with locally-hosted LLMs.

**Domain Focus:** German wind energy law - BImSchG (emissions), EEG (renewable energy), land security, grid connection, contracts, environmental compliance.

**Core Philosophy:**
- **Refusal over hallucination** - refuse to answer rather than generate incorrect legal information
- **LLM as text formatter, not reasoner** - the LLM structures retrieved context, it does not independently reason about law
- **Citation verification** - every legal claim must be traceable to a source document
- **Locally-hosted models** - no external API dependency in production (privacy-sensitive legal data)

---

## 2. Directory Structure

```
/data/projects/lai/
|-- LAIV1/                    # V1: Modular risk assessment platform
|-- LAIV2/                    # V2: Fine-tuning pipeline + data processing
|-- LAIV3/                    # V3: Production RAG system
|-- LAIV4/                    # V4: Multi-agent agentic platform (LangGraph)
|-- LAI/                      # Original: data collection, embedding server, retrieval service, UI
|-- Docker/                   # Shared Docker configs (inference engine, embedding, database)
|-- backend/                  # Shared backend (inference_engine, retrieval_service)
|-- data_processing/          # Data extraction & processing scripts
|-- testing/                  # Test configurations (v1_qwen25_7b_lora)
|-- models/                   # Trained model checkpoints
|-- data/                     # Tokenized training data
|-- VDRs/                     # Virtual Data Rooms (wind park due diligence docs)
|-- Libary/                   # DataKuzu graph database experiments
|-- qdrant/                   # Qdrant vector DB storage (legacy)
|-- Flow Diagram/             # Architecture diagrams
|-- MD/                       # Markdown documentation
|-- KS/                       # KS related files
```

---

## 3. Version Evolution Summary

| Aspect | LAIV1 | LAIV2 | LAIV3 | LAIV4 |
|--------|-------|-------|-------|-------|
| **Approach** | Modular risk engine with 6 expert modules | Fine-tuning pipeline + LoRA training | Production RAG with hybrid search | Multi-agent platform (LangGraph) |
| **LLM** | GPT-4-turbo / Claude 3.5 / Gemini (API) | Leo-HessianAI-7B (local, LoRA) | Qwen2.5-7B fine-tuned (local, LoRA) | Qwen2.5-7B-Instruct (local, vLLM) |
| **Embedding** | text-embedding-3-large (OpenAI, 1536d) | BGE-M3 (local, 1024d) | BGE-M3 (local, 1024d) | BGE-M3 (local, 1024d) |
| **Vector DB** | PostgreSQL + pgvector | PostgreSQL + pgvector | PostgreSQL + pgvector + HNSW | PostgreSQL + pgvector + HNSW |
| **Search** | Vector similarity only | Dense search only | Hybrid (dense + BM25 + RRF) | Hybrid + CRAG + Self-RAG + Adaptive RAG |
| **Reranker** | None | ms-marco-MiniLM-L-12-v2 | ms-marco-MiniLM-L-12-v2 (TEI) | ms-marco-MiniLM-L-12-v2 (vLLM) |
| **Training** | None (API-based) | Unsloth LoRA (r=64, 6496 steps) | LLaMA-Factory LoRA (r=128, 4 epochs) | No new training (uses V3 model) |
| **Architecture** | Monolithic FastAPI | Scripts + FastAPI upload | Microservices (Celery workers) | Supervisor + 8 specialized agents |
| **Multi-tenancy** | No | No | No | Yes (per-user PostgreSQL schemas) |
| **Monitoring** | Basic logging | Basic logging | Prometheus + Grafana | Structured logging + per-node metrics |
| **Feedback** | None | None | None | Self-learning feedback loop |
| **Web Search** | None | None | None | Brave Search API fallback |

---

## 4. LAIV1 - Modular Risk Assessment Platform

### 4.1 Approach

LAIV1 is a **hierarchical AI-driven wind energy due diligence platform** with a conductor/brain model orchestrating 6 specialized analysis modules. Each module assesses risk in a specific domain, producing a weighted overall score displayed as a traffic light system (GREEN/YELLOW/RED).

### 4.2 Models

| Component | Model | Details |
|-----------|-------|---------|
| **LLM (default)** | `gpt-4-turbo-preview` | OpenAI API, 2000 max tokens |
| **LLM (alt 1)** | `claude-3-5-sonnet-20241022` | Anthropic API, 2000 max tokens |
| **LLM (alt 2)** | `gemini-2.0-flash-exp` | Google API, 2000 max tokens |
| **Embedding** | `text-embedding-3-large` | OpenAI, 1536 dimensions |
| **Embedding (Google)** | `models/text-embedding-004` | Google alternative |

### 4.3 Key Parameters

| Parameter | Value |
|-----------|-------|
| Max chunk size | 512 words |
| Chunk overlap | 50 words |
| Embedding dimensions | 1536 |
| Max tokens per request | 2000 |
| Temperature (general) | 0.7 |
| Temperature (classification) | 0.1 |
| Risk threshold GREEN | >= 90 |
| Risk threshold YELLOW | >= 70 |
| Risk threshold RED | < 70 |
| Max parallel tasks | 6 |
| Max upload size | 500 MB |
| OCR languages | de, en |
| OCR batch size | 10 |
| DB pool size | 20 |
| DB max overflow | 10 |

### 4.4 Six Analysis Modules

| Module | Weight | Key Checks |
|--------|--------|-----------|
| **Land Security** (Flachensicherung) | 25% | Plot coverage, contract quality, landowner cooperation |
| **BImSchG Compliance** | 20% | Permit validity, noise (40dB night/55dB day), shadow flicker (30h/yr), 1000m distance |
| **Contract Analysis** | 15% | Termination rights, notice periods, price clauses, liability |
| **Economic Assessment** | 25% | NPV, IRR (6-8%), CAPEX/OPEX, EEG tariffs, payback (max 15yr), LCOE (max 7c/kWh) |
| **Grid Connection** | 15% | Distance (max 5km), voltage (min 110kV), cost (max EUR 150k/MW), timeline (max 24mo) |
| **Legal Compliance** | 15% | EEG, BImSchG, BauGB, BNatSchG, LuftVG, WindSeeG |

### 4.5 Risk Scoring

```
Overall Score = SUM(Module Score * Module Weight)
Severity deductions: Critical=-20, High=-10, Medium=-5, Low=-2
Base score per module: 100
```

### 4.6 Infrastructure

- **PostgreSQL + pgvector** (port 5433) - Vector storage + metadata
- **Redis** (port 6379) - Celery task queue
- **Neo4j** (port 7474/7687) - Optional graph relationships
- **DuckDB** - Analytics database

### 4.7 Key Dependencies

- FastAPI, SQLAlchemy, asyncpg, pgvector
- OpenAI, Anthropic, Google-GenerativeAI SDKs
- LlamaIndex (RAG framework), LangChain
- Docling, EasyOCR, PyPDF2
- Celery + Redis
- GeoPandas, Shapely, Folium (GIS)

### 4.8 Key Files

- `LAIV1/src/lai/core/config.py` - Pydantic settings
- `LAIV1/src/lai/core/ai_client.py` - Multi-provider AI client factory
- `LAIV1/src/lai/core/risk_engine.py` - Parallel module orchestrator
- `LAIV1/src/lai/core/document_processor.py` - OCR + chunking + embedding
- `LAIV1/src/lai/modules/` - 6 domain-specific modules

---

## 5. LAIV2 - Fine-Tuning & Data Pipeline

### 5.1 Approach

LAIV2 shifts from API-based LLMs to **locally fine-tuned models**. It introduces a comprehensive data processing pipeline (640GB raw -> 407GB clean) and LoRA fine-tuning with Unsloth optimization on Leo-HessianAI-7B.

### 5.2 Models

| Component | Model | Details |
|-----------|-------|---------|
| **Base LLM** | `leo-hessianai-7b` | German-optimized, 7B params, HessianAI |
| **Embedding** | `BAAI/bge-m3` | 1024 dimensions, multilingual |
| **Reranker** | `ms-marco-MiniLM-L-12-v2` | Cross-encoder reranking |

### 5.3 Training Configuration

| Parameter | Value |
|-----------|-------|
| Fine-tuning method | LoRA with Unsloth |
| LoRA rank (r) | 64 |
| LoRA alpha | 16 |
| LoRA dropout | 0 |
| Quantization | 4-bit (load_in_4bit=True) |
| Target modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Max sequence length | 4096 tokens |
| Batch size per device | 64 |
| Learning rate | 2e-4 |
| Epochs | 1 |
| Optimizer | 8-bit AdamW |
| Total training steps | 6,496 |
| Final perplexity | 3.79 |
| Save frequency | Every 500 steps |
| Hardware | NVIDIA A800 (80GB VRAM) |

### 5.4 Data Processing Pipeline

**Stage 1 - Quality Filtering** (640GB -> 509GB, 45.4M -> filtered):
- Remove encoding errors, boilerplate
- Legal term density scoring (words: SS, Art., BGB, StGB, Gericht, Urteil)
- Language detection (DE/EN)
- Text length validation (100-50,000 chars)
- Workers: 120 (multiprocessing), ~8 hours runtime

**Stage 2 - Deduplication** (509GB -> 407GB, 40.6M -> 36.6M):
- Phase 1: MD5 hash exact duplicate removal
- Phase 2: MinHash LSH (64 permutations, 85% threshold, 5-gram shingling)
- Batch size: 5M records, ~10.73 hours runtime

**Stage 3 - Domain Separation** (36.6M records -> 7 domains):
- 3-tier keyword classification (anchor weight 5-8, domain weight 2-4)
- Threshold: Score >= 8 AND min 2 distinct keywords
- Speed: ~6,790 records/sec

**Domain Distribution:**
| Domain | Records | Percentage |
|--------|---------|-----------|
| Wind Energy | ~154K | 0.42% |
| Land/Property | ~3.5M | 9.7% |
| BImSchG/Environmental | ~565K | 1.5% |
| Grid/Connection | ~88K | 0.24% |
| Economic/Financial | ~1.15M | 3.1% |
| Contract Law | ~4.2M | 11.5% |
| Unclassified | ~29.1M | 79.4% |

### 5.5 Chunking Parameters

| Parameter | Value |
|-----------|-------|
| Chunk max size | 1200 characters |
| Chunk min size | 400 characters |
| Chunk overlap | 200 characters |
| Embedding batch size | 32 |
| Embedding dimension | 1024 |
| Max file size | 50 MB |
| Max concurrent jobs | 3 |
| Job timeout | 600 seconds |

### 5.6 Retrieval Configuration (Planned for V3)

| Parameter | Value |
|-----------|-------|
| Dense weight | 0.6 |
| Sparse (BM25) weight | 0.4 |
| Initial retrieval candidates | 100 |
| Final reranked output | 5-7 chunks |
| Generation temperature | 0.2 |
| Min chunks required | 2 |
| Min relevance score | 0.65 |

### 5.7 Checkpoints

| Checkpoint | Steps | Notes |
|------------|-------|-------|
| `leo_7b_finetune/checkpoint-500` | 500 | Early V1 attempt |
| `leo_7b_finetune_unsloth/checkpoint-5500` | 5500 | V2 intermediate |
| `leo_7b_finetune_unsloth/checkpoint-6000` | 6000 | V2 intermediate |
| `leo_7b_finetune_unsloth/checkpoint-6496` | 6496 | V2 final (perplexity 3.79) |

### 5.8 Key Files

- `LAIV2/training_v2/train.py` - Unsloth training script
- `LAIV2/training_v2/tokenize_dataset.py` - Pre-tokenization with sharding
- `LAIV2/training_v2/evaluate_simple.py` - Perplexity evaluation
- `LAIV2/user_data_processing/processing/chunker.py` - Legal-aware chunker
- `LAIV2/user_data_processing/processing/embedder.py` - BGE-M3 embedder
- `LAIV2/finetuning_pipeline/run_pipeline.py` - Full preprocessing coordinator

---

## 6. LAIV3 - Production-Grade RAG System

### 6.1 Approach

LAIV3 is a **production-grade German Legal RAG system** with an 8-step pipeline, microservice architecture, Celery workers, hybrid search (dense + BM25 + RRF), citation verification, and monitoring via Prometheus/Grafana.

**Core Design Principle:** "LLM as Text Formatter, Not Reasoner"

### 6.2 8-Step RAG Pipeline

```
1. Query Analysis      -> Extract legal refs, intent, temporal context
2. Metadata Filter     -> Build search filters from analyzed query
3. Hybrid Retrieval    -> Dense (0.6) + BM25 (0.4) + RRF fusion
4. Reranking           -> Cross-encoder rescoring (100 -> 5-7 chunks)
5. Quality Check       -> Validate min chunks (2), min similarity (0.65)
6. Generation          -> vLLM with context + query prompt (temp=0.2)
7. Citation Verify     -> Regex + exact match against source chunks
8. Response Format     -> Structured output with citations + metadata
```

### 6.3 Models

| Component | Model | Details |
|-----------|-------|---------|
| **LLM** | `Qwen2.5-7B` (fine-tuned) | LoRA r=128, alpha=256, via vLLM |
| **Embedding** | `BAAI/bge-m3` | 1024d, via HF Text Embeddings Inference |
| **Reranker** | `ms-marco-MiniLM-L-12-v2` | Cross-encoder, via TEI |
| **Planned Embedding** | `gte-Qwen2-1.5B-instruct` | 1536d, 32K context (migration planned) |

### 6.4 Training Configuration (Qwen2.5-7B Fine-tuning)

| Parameter | Value |
|-----------|-------|
| Framework | LLaMA-Factory |
| Base model | Qwen/Qwen2.5-7B-Instruct |
| LoRA rank | 128 |
| LoRA alpha | 256 |
| LoRA dropout | 0.05 |
| Epochs | 4 |
| Batch size (effective) | 64 (4 per device * 8 gradient accum) |
| Learning rate | 1e-4 (cosine scheduler) |
| Warmup ratio | 0.03 |
| Weight decay | 0.01 |
| Max seq length | 4096 |
| Precision | bf16 |
| Training samples | 51,300 |
| Validation samples | 2,811 |
| Train loss (final) | 0.6486 |
| Eval loss (final) | 1.0459 |
| Training duration | ~5.3 hours |

**Training Data Composition:**
| Source | Percentage |
|--------|-----------|
| Synthetic German legal QA (teacher: Qwen2.5-72B-AWQ) | 60% |
| LawInstruct dataset | 30% |
| GermanQuAD + Legal-SQuAD | 10% |

**Synthetic Generation Config:**
- Teacher model: `Qwen/Qwen2.5-72B-Instruct-AWQ`
- Temperature: 0.4, top_p: 0.9
- 4 QA pairs per chunk
- Task types: factual_qa, scenario_qa, legal_analysis, summarization, contract_analysis

### 6.5 Retrieval Parameters

| Parameter | Value |
|-----------|-------|
| Initial K (before reranking) | 100 |
| Final K (after reranking) | 5 |
| Max final K | 7 |
| Min final K | 2 |
| Min similarity threshold | 0.65 |
| Dense weight (RRF) | 0.6 |
| Sparse/BM25 weight (RRF) | 0.4 |
| RRF K parameter | 60 |

### 6.6 Chunking Parameters

| Parameter | Value |
|-----------|-------|
| Max chars per chunk | 3,000 |
| Min chars per chunk | 400 |
| Overlap chars | 200 |
| Max chunk tokens (finetuning) | 3,584 |
| Min chunk tokens | 768 |
| Chunk overlap tokens | 512 |

### 6.7 LLM Generation

| Parameter | Value |
|-----------|-------|
| Max tokens | 2,048 |
| Temperature | 0.2 |
| Top-P | 0.95 |
| Timeout | 120 seconds |

### 6.8 Batch Processing (600GB Corpus)

| Parameter | Value |
|-----------|-------|
| Batch size | 10,000 chunks |
| Max concurrent jobs | 3 |
| Checkpoint interval | Every 5 batches |
| MinHash dedup threshold | 0.85 |
| MinHash permutations | 128 |

### 6.9 Monitoring SLOs

| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| Refusal rate | 15-25% | <5% or >40% |
| Citation verification | 98%+ | <95% |
| P95 latency | <3,000ms | >5,000ms |
| Empty retrieval rate | <5% | >10% |

### 6.10 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/rag` | POST | Full RAG pipeline |
| `/api/v1/search` | POST | Hybrid search |
| `/api/v1/documents` | POST/GET/DELETE | Document CRUD |
| `/api/v1/documents/{id}/status` | GET | Processing status |
| `/api/v1/auth/login` | POST | JWT authentication |
| `/api/v1/auth/refresh` | POST | Token refresh |
| `/api/v1/health` | GET | System health |
| `/api/v1/health/ready` | GET | K8s readiness |
| `/api/v1/health/live` | GET | K8s liveness |

### 6.11 Database Schema

**Core Tables:**
- `users` - Authentication (UUID, email, hashed_password, JWT)
- `documents` - File metadata, processing status, temporal fields, court info, MinHash signature
- `chunks` - Dual text (clean + NER tagged), legal references, embedding (1024d HNSW), BM25 tsvector
- `audit_logs` - Action tracking (user, resource, changes, IP)
- `batch_checkpoints` - Resumable batch processing
- `dedup_records` - Duplicate tracking
- `metrics_hourly` - Time-series metrics

**Indexes:**
- HNSW on chunks.embedding (m=16, ef_construction=64)
- GiST on chunks.search_vector (BM25)
- B-tree on documents.file_hash, status, court_level

### 6.12 Microservices (Docker Compose)

| Service | Image | Port | Resources |
|---------|-------|------|-----------|
| PostgreSQL | pgvector:pg16 | 5432 | - |
| Redis | redis:7-alpine | 6379 | - |
| MinIO | minio:latest | 9000/9001 | - |
| Embedding | TEI cpu-1.5 | 8001 | 8GB mem |
| Reranker | TEI cpu-1.5 | 8002 | 4GB mem |
| LLM | vllm-openai:v0.5.4 | 8003 | GPU |
| API | Dockerfile.api | 8300 | 2GB mem |
| Worker | Dockerfile.worker | - | 4GB mem |
| Beat | Dockerfile.worker | - | - |
| Prometheus | prom/prometheus | 9090 | - |
| Grafana | grafana/grafana | 3000 | - |

### 6.13 Key Files

- `LAIV3/laiv3/main.py` - FastAPI application
- `LAIV3/laiv3/config/settings.py` - Nested Pydantic settings
- `LAIV3/laiv3/config/constants.py` - Legal constants, NER tags, prompts
- `LAIV3/laiv3/pipeline/orchestrator.py` - 8-step pipeline
- `LAIV3/laiv3/pipeline/retrieval/hybrid_search.py` - Dense + BM25 + RRF
- `LAIV3/laiv3/pipeline/generation/citation_verifier.py` - Citation verification
- `LAIV3/laiv3/monitoring/metrics.py` - Prometheus metrics
- `LAIV3/finetuning_next_steps/config.yaml` - Training config
- `LAIV3/finetuning_next_steps/step*.py` - Training pipeline steps

---

## 7. LAIV4 - Multi-Agent Agentic Platform

### 7.1 Approach

LAIV4 is a **multi-agent platform built on LangGraph** with a Supervisor routing to 8 specialized agents. It introduces CRAG (Corrective RAG), Self-RAG, Adaptive RAG, multi-tenancy, self-learning feedback loops, web search fallback, and fine-tuning data generation.

### 7.2 Agent Architecture

```
Supervisor (classify_task)
    |
    +-- RAG Agent (14-node pipeline with CRAG + Self-RAG)
    +-- Web Search Agent (Brave Search fallback)
    +-- Document Processing Agent (upload -> parse -> chunk -> embed)
    +-- Document Analysis Agent (6 analysis types)
    +-- Contract Comparison Agent (clause-by-clause diff)
    +-- Timeline Extraction Agent (date/deadline extraction)
    +-- Feedback Agent (self-learning corrections)
    +-- Fine-Tuning Data Agent (instruction generation)
```

### 7.3 RAG Agent - 14 Nodes

```
1.  analyze_query         -> Rule-based legal ref extraction (no LLM)
2.  route_query           -> simple / complex / clarification
3.  decompose_query       -> Complex -> 2-3 sub-queries (LLM)
4.  retrieve_sub          -> Per sub-query retrieval
5.  advance_sub_query     -> Sub-query loop index
6.  synthesize            -> Merge sub-query results
7.  retrieve              -> Hybrid search (dense + BM25 + RRF + rerank)
8.  grade_documents       -> CRAG: LLM relevance check (max 2 loops)
9.  rewrite_query         -> CRAG: query reformulation
10. generate              -> LLM answer generation
11. check_faithfulness    -> Self-RAG: grounded in sources?
12. check_relevance       -> Self-RAG: answers the question? (max 1 retry)
13. verify_citations      -> Regex matching against source chunks
14. build_response        -> Assemble final RAGResponse
```

### 7.4 Models

| Component | Model | Details |
|-----------|-------|---------|
| **LLM** | `Qwen/Qwen2.5-7B-Instruct` | vLLM, GPU 1, temp=0.2, max 2048 tokens |
| **Embedding** | `BAAI/bge-m3` | vLLM, GPU 0, 1024d, cached in Redis |
| **Reranker** | `ms-marco-MiniLM-L-12-v2` | vLLM, GPU 0, top-20 -> top-5 |

### 7.5 Key Parameters

| Parameter | Value |
|-----------|-------|
| Chunk size | 512 tokens |
| Chunk overlap | 50 tokens |
| Embedding dimension | 1024 |
| Dense weight (RRF) | 0.6 |
| Sparse weight (RRF) | 0.4 |
| RRF K | 60 |
| Initial K | 100 |
| Final K (after rerank) | 5 |
| Rerank input | Top-20 candidates |
| HNSW m | 16 |
| HNSW ef_construction | 200 |
| HNSW ef_search | 100 |
| Min relevant chunks (CRAG) | 2 |
| Max CRAG loops | 2 |
| Max Self-RAG retries | 1 |
| Min score threshold | 0.3 |
| LLM temperature | 0.2 |
| LLM max tokens | 2,048 |
| Grading temperature | 0.0 (deterministic) |
| Grading max tokens | 16 |
| Redis TTL | 3,600 seconds |
| DB pool (min/max) | 2 / 10 |
| Max file upload | 50 MB |
| Embedding batch size | 32 |
| Brave Search max results | 5 |
| Reranker max text length | 400 chars |

### 7.6 Multi-Tenancy

- Each user gets isolated PostgreSQL schema: `user_{uuid}`
- Retriever searches both `public` schema (600GB legal corpus) and user's personal schema
- Results merged via RRF across schemas
- User documents never mixed with public data

### 7.7 Self-Learning Feedback Loop

```
User submits correction (wrong_answer | wrong_citation | irrelevant_chunks | hallucination | outdated_info)
    |
    v
Feedback Agent traces root cause (LLM analysis)
    |
    v
Apply corrections:
  - Downweight chunk quality_score (-= 0.2)
  - Flag outdated documents
  - Log for future fine-tuning
    |
    v
Immediate retrieval impact (lower quality = lower ranking)
```

### 7.8 API Endpoints (19 routes)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/query` | POST | RAG query |
| `/query/stream` | POST | SSE streaming |
| `/documents/upload` | POST | Document upload |
| `/documents/{id}` | GET/DELETE | Document CRUD |
| `/documents/{id}/analyze` | POST | 6 analysis types |
| `/documents/compare` | POST | Contract comparison |
| `/documents/{id}/timeline` | GET | Timeline extraction |
| `/feedback` | POST | Submit correction |
| `/feedback/stats` | GET | Feedback statistics |
| `/finetuning/generate` | POST | Generate FT data |
| `/finetuning/datasets` | GET | List FT datasets |
| `/users/{id}/initialize` | POST | Create user schema |
| `/health` | GET | Health check |

### 7.9 Services Architecture

| Service | Purpose |
|---------|---------|
| `database.py` | asyncpg pool, hybrid_search, multi-schema search |
| `embedding.py` | vLLM BGE-M3 client, batch embed, retry logic |
| `reranker.py` | vLLM cross-encoder, truncate to 400 chars |
| `llm.py` | vLLM Qwen generation, streaming support |
| `cache.py` | Redis embedding cache (SHA256 key, TTL 3600s) |
| `schema_manager.py` | Multi-tenancy schema CRUD |
| `document_parser.py` | Docling PDF/DOCX parsing + chunking |
| `file_storage.py` | MinIO async file operations |
| `web_search.py` | Brave Search API (German legal domains) |
| `feedback_store.py` | Feedback CRUD, chunk quality updates |
| `finetuning_store.py` | FT data generation (public DB only) |

### 7.10 Key Files

- `LAIV4/agentic_approach/main.py` - Entry point (serve / query CLI)
- `LAIV4/agentic_approach/api.py` - FastAPI with 19 routes
- `LAIV4/agentic_approach/graph.py` - StateGraph compilation + service lifecycle
- `LAIV4/agentic_approach/config.py` - 10 Pydantic settings groups
- `LAIV4/agentic_approach/agents/supervisor.py` - Top-level routing
- `LAIV4/agentic_approach/agents/rag_agent.py` - 14-node RAG pipeline
- `LAIV4/agentic_approach/nodes/` - 13 individual RAG nodes
- `LAIV4/agentic_approach/services/` - 11 service modules
- `LAIV4/ARCHITECTURE.md` - Mermaid diagrams (6 architecture views)

---

## 8. Models Comparison Across Versions

### 8.1 LLM Models

| Version | Model | Type | Parameters | Hosting | Quantization |
|---------|-------|------|-----------|---------|-------------|
| V1 | GPT-4-turbo-preview | Proprietary | Unknown | OpenAI API | N/A |
| V1 | Claude-3.5-sonnet | Proprietary | Unknown | Anthropic API | N/A |
| V1 | Gemini-2.0-flash-exp | Proprietary | Unknown | Google API | N/A |
| V2 | Leo-HessianAI-7B | Open | 7B | Local (GPU) | 4-bit (Unsloth) |
| V3 | Qwen2.5-7B (fine-tuned) | Open | 7B | Local (vLLM) | bf16 |
| V4 | Qwen2.5-7B-Instruct | Open | 7B | Local (vLLM) | float16 |

### 8.2 Embedding Models

| Version | Model | Dimensions | Max Tokens | Hosting |
|---------|-------|-----------|------------|---------|
| V1 | text-embedding-3-large | 1536 | - | OpenAI API |
| V2 | BAAI/bge-m3 | 1024 | 8,192 | Local |
| V3 | BAAI/bge-m3 | 1024 | 8,192 | Local (TEI) |
| V4 | BAAI/bge-m3 | 1024 | 8,192 | Local (vLLM) |
| V3 (planned) | gte-Qwen2-1.5B-instruct | 1536 | 32,768 | Local |

### 8.3 Reranker Models

| Version | Model | Notes |
|---------|-------|-------|
| V1 | None | - |
| V2 | ms-marco-MiniLM-L-12-v2 | Cross-encoder |
| V3 | ms-marco-MiniLM-L-12-v2 | Via TEI |
| V4 | ms-marco-MiniLM-L-12-v2 | Via vLLM |

---

## 9. Numerical Parameters Comparison

### 9.1 Chunking

| Parameter | V1 | V2 | V3 | V4 |
|-----------|----|----|----|----|
| Unit | Words | Characters | Characters | Tokens |
| Max size | 512 words | 1,200 chars | 3,000 chars | 512 tokens |
| Min size | - | 400 chars | 400 chars | - |
| Overlap | 50 words | 200 chars | 200 chars | 50 tokens |

### 9.2 Retrieval

| Parameter | V1 | V2 | V3 | V4 |
|-----------|----|----|----|----|
| Search type | Dense only | Dense only | Hybrid (RRF) | Hybrid (RRF) + CRAG |
| Initial K | - | 100 | 100 | 100 |
| Final K | - | 5-7 | 5 | 5 |
| Dense weight | 1.0 | 0.6 | 0.6 | 0.6 |
| BM25 weight | 0.0 | 0.4 | 0.4 | 0.4 |
| RRF K | - | - | 60 | 60 |
| Min similarity | - | 0.65 | 0.65 | 0.3 |
| Min chunks | - | 2 | 2 | 2 |
| HNSW m | - | - | 16 | 16 |
| HNSW ef_construction | - | - | 64 | 200 |
| HNSW ef_search | - | - | - | 100 |

### 9.3 Generation

| Parameter | V1 | V2 | V3 | V4 |
|-----------|----|----|----|----|
| Temperature | 0.7 | - | 0.2 | 0.2 |
| Max tokens | 2,000 | 200 | 2,048 | 2,048 |
| Top-P | - | - | 0.95 | - |
| Timeout | - | - | 120s | 60s |

### 9.4 Training

| Parameter | V2 (Leo-7B) | V3 (Qwen-7B) |
|-----------|-------------|---------------|
| LoRA rank | 64 | 128 |
| LoRA alpha | 16 | 256 |
| LoRA dropout | 0.0 | 0.05 |
| Epochs | 1 | 4 |
| Batch size (effective) | 64 | 64 |
| Learning rate | 2e-4 | 1e-4 |
| Training steps | 6,496 | - |
| Training samples | 25% of 36.6M | 51,300 |
| Quantization | 4-bit | bf16 |
| Framework | Unsloth | LLaMA-Factory |
| Final metric | Perplexity: 3.79 | Eval loss: 1.0459 |

---

## 10. Infrastructure & Docker Services

### 10.1 Database Stack

| Component | Image | Port | Purpose |
|-----------|-------|------|---------|
| PostgreSQL + pgvector | pgvector/pgvector:pg16 | 5432/5433 | Vector store + metadata + BM25 |
| Redis | redis:7-alpine | 6379/6380 | Cache, task queue, rate limiting |
| MinIO | minio/minio:latest | 9000-9002 | Object storage (documents, datasets) |
| Qdrant | qdrant/qdrant | 6333 | Legacy vector DB (deprecated) |
| Neo4j | neo4j:5.15-community | 7474/7687 | Optional graph DB (V1 only) |

### 10.2 ML Services

| Component | Image | Port | GPU | Model |
|-----------|-------|------|-----|-------|
| Embedding | TEI cpu-1.5 / vLLM | 8001/8003 | 0 | BAAI/bge-m3 |
| Reranker | TEI cpu-1.5 / vLLM | 8002/8004 | 0 | ms-marco-MiniLM-L-12-v2 |
| LLM | vllm-openai | 8001/8003 | 1 | Qwen2.5-7B-Instruct |

### 10.3 Application Services

| Component | Port | Purpose |
|-----------|------|---------|
| FastAPI API | 8000/8200/8300 | REST API |
| Celery Worker | - | Async document processing |
| Celery Beat | - | Scheduled tasks |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3000/3001 | Dashboards |

### 10.4 MinIO Buckets

| Bucket | Purpose |
|--------|---------|
| `lai-raw` / `lai-raw-docs` | Raw uploaded documents |
| `lai-processed` / `lai-processed-docs` | Processed/chunked documents |
| `user-documents` | User-uploaded files (multi-tenant) |
| `user-processed` | User processed output |

---

## 11. Data Processing Pipeline

### 11.1 Document Sources

- **5 German Legal Datasets** (~640GB raw):
  - Court decisions, legislation, commentaries
  - Federal and state law databases
  - Legal journals and publications

- **Virtual Data Rooms (VDRs):** Wind park specific documents across 10+ projects:
  - WP 33:34, WP Altmark, WP Beppener Bruch, WP Butterberg
  - WP Hudehatten, WP Lamstedt, WP Sebbenhausen, WP Tostedt, WP Zodel

### 11.2 Processing Stages

```
Raw PDFs/Text (640GB)
    |
    v [Quality Filtering - 8 hours, 120 workers]
Filtered (509GB, 45.4M records)
    |
    v [Exact Dedup (MD5) + Near Dedup (MinHash LSH 85%) - 10.7 hours]
Deduplicated (407GB, 36.6M records)
    |
    v [Domain Separation - 3-tier keyword classification - 90 min]
7 Domain Datasets
    |
    v [Chunking - section-aware, legal reference extraction]
Chunks (stored in PostgreSQL + pgvector)
    |
    v [Embedding - BGE-M3, batch size 32]
Embedded Chunks (1024d vectors with HNSW index)
```

### 11.3 Key Processing Tools

| Tool | Purpose |
|------|---------|
| Docling | PDF/DOCX extraction with OCR |
| EasyOCR | Scanned document OCR (de, en) |
| PyPDF2 / PyMuPDF | PDF manipulation |
| python-docx | DOCX processing |
| datasketch | MinHash LSH deduplication |
| sentencepiece | Tokenization |

---

## 12. Training Architecture & Datasets

### 12.1 Phase 1 Architecture (from LAI_TRAINING_ARCHITECTURE.md)

**Hierarchical System:**
- **LAI Brain (Conductor):** Routes queries, orchestrates specialists
- **6 Specialist Models:** Domain-specific LoRA adapters
- **Base:** Llama 3 70B (planned), later switched to 7B models

### 12.2 Phase 2 Architecture (MoE - Mixture of Experts)

**Router/Gating Mechanism:**
1. Manual specialist selection (Phase 1)
2. Automatic domain classifier (Phase 2)

**Universal Output Schema:**
- domain, overall_risk_score (0-100), risk_category (GREEN/AMBER/RED)
- issues[] with severity, category, explanation, reference_clauses
- extracted_fields{}, summary

### 12.3 Training Data Evolution

| Version | Source | Quantity | Method |
|---------|--------|----------|--------|
| V2 | 5 German legal datasets | 25% of 36.6M deduplicated records | Continued pretraining |
| V3 | Synthetic QA (60%) + LawInstruct (30%) + GermanQuAD (10%) | 51,300 training samples | Supervised fine-tuning |

### 12.4 Model Checkpoints

| Location | Model | Steps/Epochs | Status |
|----------|-------|-------------|--------|
| `models/leo-hessianai-7b/` | Base Leo 7B | - | Base model |
| `models/leo-hessianai-70b/` | Base Leo 70B | - | Base model |
| `models/Saul-7B-Instruct-v1/` | Saul Legal 7B | - | Evaluation |
| `models/leo_7b_finetune_unsloth/` | Leo 7B LoRA | 6496 steps | V2 production |
| `models/qwen25-7b-legal-lora/` | Qwen 7B LoRA | 1608 steps, 4 epochs | V3 adapter |
| `models/qwen25-7b-legal-merged/` | Qwen 7B merged | - | V3/V4 production |
| `models/checkpoint-6496/` | Leo 7B final | 6496 | V2 backup |

---

## 13. Supporting Directories

### 13.1 Docker/ (Shared Infrastructure)

- `Docker/database/` - PostgreSQL init scripts, MinIO normalization/chunking
- `Docker/embedding_server/` - TEI/vLLM embedding service configs
- `Docker/inference_engine/` - Full inference engine with:
  - Smart RAG engine, query classifier, response validator
  - System prompts (5 styles: expert, assistant, researcher, simple, compact)
  - Few-shot examples (German environmental law)
  - Session memory (max 10 turns, 24h expiry)
  - User document integration (40% context ratio)
- `Docker/laiv4/` - V4-specific docker-compose

### 13.2 backend/ (Shared Backend)

- `backend/inference_engine/` - Inference API
- `backend/retrieval_service/` - Retrieval with hybrid search, reranking, query expansion

### 13.3 LAI/ (Original Platform)

- `LAI/embedding_server/` - Standalone embedding service
- `LAI/retrieval_service/` - Retrieval with analyzers, reranking, search
- `LAI/legal_data/` - Legal data collection (BMJ XML, court decisions, Gerdalir)
- `LAI/processed/` - 5-step processing pipeline output
- `LAI/ui/` - Frontend (lai_frontend_only)
- `LAI/lai/` - Data collection scripts (BGBL PDFs, RSS feeds, literature)

### 13.4 data_processing/

- Dataset handlers, format converters
- Instrument-specific processing
- Exception handling for edge cases

### 13.5 testing/

- `testing/v1_qwen25_7b_lora/` - Test deployment config for Qwen 7B LoRA model
  - Docker compose with vLLM serving the fine-tuned model
  - Backend test configuration

### 13.6 Libary/

- `Libary/DataKuzu/` - Graph database experiments with Kuzu

---

## 14. Architecture Decision Records

### ADR-1: Local vs API-Based LLMs
- **V1:** API-based (OpenAI, Anthropic, Google) for rapid prototyping
- **V2+:** Local models for data privacy (German legal data sensitivity)
- **Decision:** All production deployments use local vLLM

### ADR-2: Base Model Selection
- **V2:** Leo-HessianAI-7B (German-optimized)
- **V3/V4:** Qwen2.5-7B-Instruct (better multilingual, superior performance)
- **Evaluated:** Saul-7B (legal-specific), Leo-70B (too resource-heavy)

### ADR-3: Vector Database
- **Considered:** Qdrant (standalone), pgvector (PostgreSQL extension)
- **Decision:** pgvector - reduces infrastructure complexity, supports hybrid search with BM25 via tsvector

### ADR-4: Search Strategy Evolution
- **V1:** Dense-only vector search
- **V3:** Hybrid (dense + BM25 + RRF) - significantly improved recall
- **V4:** + CRAG (max 2 rewrite loops) + Self-RAG (faithfulness/relevance checks)

### ADR-5: Agent Framework (V4)
- **Evaluated:** LangGraph, CrewAI, AutoGen, OpenAI Assistants, custom
- **Decision:** LangGraph - explicit state graphs, no hidden prompt injection, deterministic routing

### ADR-6: Embedding Model
- **V1:** OpenAI text-embedding-3-large (1536d, API)
- **V2+:** BGE-M3 (1024d, local) - multilingual, efficient
- **Planned:** gte-Qwen2-1.5B (1536d, 32K context) - eliminates truncation issues

### ADR-7: Training Strategy
- **V2:** Continued pretraining on legal corpus (Unsloth, 4-bit)
- **V3:** Supervised fine-tuning on synthetic QA data (LLaMA-Factory, bf16)
- **Rationale:** SFT on task-specific data produces better instruction-following than domain pretraining alone

---

## 15. Key File Reference

### Configuration Files

| File | Purpose |
|------|---------|
| `LAIV1/.env` / `.env.example` | V1 environment config |
| `LAIV1/pyproject.toml` | V1 dependencies |
| `LAIV2/user_data_processing/config.py` | V2 processing config |
| `LAIV3/.env` | V3 environment config |
| `LAIV3/laiv3/config/settings.py` | V3 Pydantic settings (comprehensive) |
| `LAIV3/finetuning_next_steps/config.yaml` | V3 training config |
| `LAIV4/agentic_approach/config.py` | V4 Pydantic settings (10 groups) |
| `Docker/inference_engine/config.py` | Shared inference config |
| `Docker/inference_engine/.env` | Inference environment |

### Architecture Documentation

| File | Purpose |
|------|---------|
| `LAI_Phase1_Architecture_Workflow.md` | Phase 1 foundations |
| `LAI Phase2 MOE Architecture Wokflow.md` | MoE specialist design |
| `LAI_TRAINING_ARCHITECTURE.md` | Brain + 6 specialist training (62KB) |
| `LAIV3/PRODUCTION_PLAN.md` | V3 deployment roadmap |
| `LAIV3/LEGAL_LLM_DEPLOYMENT.md` | Fine-tuned model serving |
| `LAIV3/docs/EMBEDDING_MODEL_MIGRATION.md` | BGE-M3 -> gte-Qwen2 plan |
| `LAIV4/ARCHITECTURE.md` | V4 Mermaid diagrams (6 views) |

### Core Application Code

| File | Purpose |
|------|---------|
| `LAIV1/src/lai/core/risk_engine.py` | V1 module orchestrator |
| `LAIV1/src/lai/modules/*.py` | V1 six analysis modules |
| `LAIV2/training_v2/train.py` | V2 Unsloth training script |
| `LAIV2/finetuning_pipeline/run_pipeline.py` | V2 data preprocessing |
| `LAIV3/laiv3/pipeline/orchestrator.py` | V3 8-step RAG pipeline |
| `LAIV3/laiv3/pipeline/retrieval/hybrid_search.py` | V3 hybrid search |
| `LAIV4/agentic_approach/graph.py` | V4 LangGraph compilation |
| `LAIV4/agentic_approach/agents/supervisor.py` | V4 agent routing |
| `LAIV4/agentic_approach/nodes/*.py` | V4 14 RAG nodes |

### Docker Configurations

| File | Purpose |
|------|---------|
| `LAIV1/docker-compose.yml` | V1 infra (Postgres, Redis, Neo4j) |
| `LAIV3/docker-compose.yml` | V3 full stack (11 services) |
| `Docker/laiv4/docker-compose.yml` | V4 infra (Postgres, Redis, MinIO, vLLM) |
| `Docker/inference_engine/docker-compose.yml` | Shared inference engine |
| `Docker/embedding_server/docker-compose.yml` | Embedding service |
| `testing/v1_qwen25_7b_lora/docker-compose.yml` | Test deployment |

---

## Appendix A: Technology Stack Summary

| Category | Technologies |
|----------|-------------|
| **Language** | Python 3.11-3.13 |
| **Web Framework** | FastAPI + Uvicorn |
| **Agent Framework** | LangGraph (V4) |
| **LLM Serving** | vLLM (OpenAI-compatible API) |
| **Embedding Serving** | HuggingFace TEI / vLLM |
| **Vector Database** | PostgreSQL + pgvector (HNSW) |
| **Object Storage** | MinIO (S3-compatible) |
| **Task Queue** | Celery + Redis |
| **Caching** | Redis |
| **Training** | Unsloth (V2), LLaMA-Factory (V3), PyTorch, PEFT, TRL |
| **Document Processing** | Docling, EasyOCR, PyPDF2, PyMuPDF |
| **Monitoring** | Prometheus, Grafana, structlog |
| **Database Migrations** | Alembic |
| **Configuration** | Pydantic Settings, python-dotenv |
| **Authentication** | JWT (HS256), passlib/bcrypt |
| **HTTP Client** | httpx (async) |
| **Deduplication** | datasketch (MinHash LSH) |
| **Testing** | pytest, pytest-asyncio |
| **Linting** | ruff, mypy, black |

## Appendix B: Glossary

| Term | Meaning |
|------|---------|
| BImSchG | Bundesimmissionsschutzgesetz (Federal Immission Protection Act) |
| EEG | Erneuerbare-Energien-Gesetz (Renewable Energy Sources Act) |
| BauGB | Baugesetzbuch (Building Code) |
| BNatSchG | Bundesnaturschutzgesetz (Federal Nature Conservation Act) |
| CRAG | Corrective RAG - rewrite query if retrieval is insufficient |
| Self-RAG | Self-Reflective RAG - verify faithfulness and relevance of generated answers |
| RRF | Reciprocal Rank Fusion - combines dense and sparse search rankings |
| LoRA | Low-Rank Adaptation - parameter-efficient fine-tuning |
| TEI | Text Embeddings Inference (HuggingFace) |
| vLLM | Fast LLM serving engine |
| HNSW | Hierarchical Navigable Small World (approximate nearest neighbor index) |
| VDR | Virtual Data Room (due diligence document repository) |
| MoE | Mixture of Experts |
