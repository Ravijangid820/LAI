# LAIV5 - Proposed Improvements & Roadmap

> **Base:** LAIV3 (production RAG system) as foundation
> **Cherry-picked from LAIV4:** Multi-tenancy, web search fallback, document analysis/comparison, CRAG
> **Dropped from LAIV4:** Timeline agent (scope creep), feedback self-learning (incomplete/non-functional), supervisor routing overhead

---

## Why LAIV3 as Foundation (Not LAIV4)

| Criteria | LAIV3 | LAIV4 |
|----------|-------|-------|
| Test coverage | Unit + integration + E2E | Only `test_rag.py` (basic sanity) |
| Monitoring | Prometheus + Grafana + SLOs | Structured logging only |
| Production maturity | Celery workers, health checks, rate limiting | No worker architecture |
| Auth & security | JWT, audit logging (partial), rate limiting | No auth layer |
| Database migrations | Alembic with proper versioning | Raw DDL in schema_manager |
| Code quality | Structured Pydantic settings, error hierarchy | Good but untested |

LAIV4's agentic approach adds complexity without proportional value for the core RAG use case. Its genuine contributions (multi-tenancy, CRAG, web search, document analysis) should be backported into V3's more mature infrastructure.

---

## PART 1: CRITICAL FIXES (Production Blockers)

These are not improvements - these are things currently broken that must be fixed first.

### 1.1 Complete Embedding Backfill

**Problem:** Only ~1% of 19.2M chunks have embeddings. System cannot serve any search requests.

**Fix:**
- Build a resumable, checkpointed embedding backfill script
- Use batch embedding (batch_size=32) via TEI service
- Checkpoint every 5,000 chunks to PostgreSQL `batch_checkpoints` table
- At 270 chunks/sec throughput, full backfill = ~20 hours
- Run with `--resume` flag to continue from last checkpoint after crashes

**Estimated time:** 20 hours compute + 2 hours development

### 1.2 Populate BM25 search_vector Column

**Problem:** `search_vector` (tsvector) column is empty. Hybrid search (40% BM25 weight) returns nothing from sparse side.

**Fix:**
- SQL batch update: `UPDATE chunks SET search_vector = to_tsvector('german', text_clean) WHERE search_vector IS NULL`
- Process in batches of 10,000 to avoid lock contention
- Add GiST index after population (already in migration but not populated)

**Estimated time:** 2-4 hours compute

### 1.3 Fix NUL Byte Corruption in Batch Ingestion

**Problem:** ~50+ files from `openlegaldata_api_dump/` contain NUL (0x00) bytes, causing batch write failures and data loss.

**Fix:**
- Add input sanitization: `text.replace('\x00', '')` before any DB write
- Add binary content detection: skip files where >5% of content is non-UTF-8
- Re-process the ~4,000 remaining failed files

**Location:** `LAIV3/laiv3/pipeline/ingestion/batch_processor.py`

### 1.4 Implement Missing API Endpoints

**Problem:** Frontend expects endpoints that don't exist.

| Endpoint | Status | Fix |
|----------|--------|-----|
| `GET /api/v1/documents/{id}/download` | Missing | Stream original PDF from MinIO |
| `GET /api/v1/documents/{id}/analytics` | Stub only | Query chunk count, law refs, avg size |

### 1.5 Wire Up Audit Logging & Feedback Storage

**Problem:** TODO comments in `rag.py` lines 212 and 305 - audit and feedback never stored.

**Fix:** Connect existing `AuditRepository` and feedback table to the RAG endpoint handlers.

---

## PART 2: HIGH-IMPACT IMPROVEMENTS

### 2.1 Backport Multi-Tenancy from LAIV4

**What:** Per-user PostgreSQL schemas (`user_{uuid}`) for uploaded document isolation.

**Why:** Users need to upload and search their own documents (contracts, permits) alongside the public legal corpus. This is the single most requested feature.

**How (from LAIV4's `schema_manager.py`):**
- `ensure_user_schema(user_id)` creates isolated schema with same chunks table structure
- HNSW index per schema for vector search
- `hybrid_search_multi_schema()` searches public + user schema, merges via RRF
- Soft-delete for user documents

**Additions needed for LAIV3:**
- Schema creation on first user document upload
- Cross-schema search in `hybrid_search.py` (merge public + user results)
- Cleanup cronjob for deleted user schemas (missing even in V4)
- Token budget split: 60% public corpus, 40% user documents (configurable)

### 2.2 Add CRAG (Corrective RAG) Loop

**What:** LLM grades each retrieved chunk for relevance. If <2 relevant chunks, rewrite query and re-retrieve. Max 2 loops.

**Why:** V3's rule-based quality checker uses only similarity score thresholds (0.65). This misses semantically irrelevant chunks that happen to have high vector similarity (e.g., same legal terms but different context). CRAG catches ~10% more irrelevant results.

**How (from LAIV4's `nodes/document_grader.py`):**
- After reranking, grade each chunk with LLM (temperature=0.0, max_tokens=16, binary yes/no)
- If relevant_chunks < `min_relevant_chunks` (2): rewrite query and re-retrieve
- Max 2 CRAG loops to bound latency (worst case +2.4s)
- Add `AgenticSettings` to V3's settings: `max_crag_loops=2`, `grading_temperature=0.0`

**Latency impact:** +300-600ms typical, +2.4s worst case. Acceptable for legal domain where accuracy > speed.

**Optimization over V4:** Skip CRAG entirely for queries classified as `SIMPLE` or `CONVERSATIONAL` by query classifier. Only apply to `LEGAL_COMPLEX` queries.

### 2.3 Add Web Search Fallback

**What:** When RAG returns REFUSED (insufficient context), fall back to Brave Search API filtered to German legal domains.

**Why:** Current system returns nothing for queries about very recent laws, niche topics, or domains not in the 600GB corpus. Web search provides a safety net.

**How (from LAIV4's `web_search_agent.py`):**
- Trigger only after RAG pipeline returns `generation_status: refused`
- Search Brave API (max 5 results, filtered to: gesetze-im-internet.de, dejure.org, juris.de, bundesgerichtshof.de)
- Generate answer from web snippets with clear disclaimer
- Mark response as `source: web_search` (not `source: rag`)

**Cost:** ~0.3-0.5 EUR per 100 Brave API queries. Expected trigger rate: 15-25% of queries (matching refusal SLO).

### 2.4 Add Document Analysis Capabilities

**What:** Users can upload a document and request analysis: summarize, extract clauses, extract parties, extract key terms, classify document type, flag unusual clauses.

**Why:** Due diligence users don't just ask questions - they need structured analysis of specific documents. This is the #2 most requested feature after multi-tenancy.

**How (from LAIV4's `document_analysis_agent.py`):**
- 6 analysis types as separate prompt templates
- Load user's document chunks, pass to LLM with analysis-specific system prompt
- Return structured output (JSON with extracted fields)

**Also include contract comparison** (from LAIV4's `contract_comparison_agent.py`):
- Upload 2 contracts, extract clauses from each, align by topic, generate clause-by-clause diff
- Critical for wind park acquisition due diligence

---

## PART 3: RETRIEVAL QUALITY IMPROVEMENTS

### 3.1 Raise Min Similarity Threshold

**Current:** 0.3 (too low, retrieves noise)
**Proposed:** 0.5 for general queries, 0.4 for keyword-heavy queries (SS citations)

**Why:** At 0.3, German legal chunks with shared vocabulary but different legal context score highly. Users report irrelevant results. A 0.5 threshold eliminates ~80% of noise with <5% loss of relevant results.

**Implementation:** Make threshold dynamic based on query type:
```
LEGAL_COMPLEX: 0.5
LEGAL_SIMPLE: 0.45
keyword-heavy (>2 SS refs): 0.4
```

### 3.2 Add Result Deduplication

**Problem:** Multiple chunks from the same document section appear in top-K results, wasting context tokens.

**Fix:**
- After reranking, deduplicate by `(document_id, section)` - keep highest-scoring chunk per section
- If deduplicated results < `min_final_k`, allow second-best chunk from same section
- This is a simple post-processing step, no model changes needed

### 3.3 Adaptive Hybrid Search Weights

**Problem:** Dense weight (0.6) and BM25 weight (0.4) are fixed. But keyword-heavy queries (SS 22 BImSchG) should prefer BM25, while conceptual queries ("noise limits for residential areas") should prefer dense.

**Fix:**
- If query contains >= 2 legal references (SS, Art.), increase BM25 weight to 0.6
- If query is a natural language question with no legal refs, increase dense weight to 0.7
- Rule-based, no model needed - leverage existing `query_analyzer.py` output

### 3.4 Increase Default Top-K and Context Window

**Current:** top_k=5, max_context=3000 tokens
**Proposed:** top_k=7, max_context=4096 tokens

**Why:** German legal text is verbose. BImSchG + TA Larm + implementing ordinances often need 7-10 chunks to cover a question fully. At 5 chunks and 3000 tokens (~750 German words), answers are often incomplete. Qwen2.5-7B handles 4096 context well (trained on this length).

### 3.5 Pre-filter Before Vector Search

**Problem:** Vector search retrieves all 19M+ chunks, then filters by metadata. This is wasteful.

**Fix:** Push metadata filters (doc_type, court_level, law_refs, effective_date) into the SQL WHERE clause BEFORE the vector similarity search. pgvector supports this natively with filtered HNSW scans.

**Impact:** 2-5x faster retrieval for filtered queries, no quality loss.

---

## PART 4: LLM & GENERATION IMPROVEMENTS

### 4.1 Expand Few-Shot Examples

**Problem:** Only 4 few-shot examples, all focused on environmental law (BImSchG, TA Larm).

**Fix:** Add domain-balanced examples:
- Contract law (lease termination, purchase agreement)
- Grid connection (Netzanschluss, EEG feed-in)
- Land security (Flachensicherung, Grundbuch)
- Economic assessment (EEG-Vergutung, Wirtschaftlichkeitsberechnung)
- Court decision analysis (BGH ruling format)

**Target:** 2 examples per domain = 12 total. Select 1-2 dynamically based on query domain classification.

### 4.2 Domain-Aware Prompt Selection

**Problem:** Same system prompt used for all query types. A question about BImSchG permits gets the same prompt as a question about contract termination rights.

**Fix:** Create 6 domain-specific prompt variants (one per LAI module domain). Route based on query_analyzer's detected law_refs and intent:
- `prompt_bimschg` - environmental compliance focus
- `prompt_contract` - clause analysis, risk identification
- `prompt_land` - property rights, cadastral terminology
- `prompt_grid` - technical grid connection terms
- `prompt_economic` - financial metrics, EEG tariff calculations
- `prompt_general` - default fallback

### 4.3 German-First Prompting

**Problem:** System prompts are written in English, with a late instruction to "match user language." This produces subtly anglicized German legal responses.

**Fix:** Write primary system prompts in German. The model generates more natural German legal text when the system prompt itself uses German legal conventions (Randnummer, Leitsatz, Tenor, etc.).

### 4.4 Improve Chain-of-Thought for Complex Queries

**Problem:** CoT is enabled for complex queries but the prompt doesn't guide legal reasoning structure.

**Fix:** Add structured German legal reasoning template:
```
1. Anwendbare Rechtsgrundlage (applicable legal basis)
2. Tatbestandsvoraussetzungen (constituent elements)
3. Subsumtion (application to facts)
4. Rechtsfolge (legal consequence)
```

This matches how German lawyers actually reason (Gutachtenstil) and produces more reliable answers.

### 4.5 Temperature Tuning Per Query Type

**Current:** Fixed temperature 0.3 for all queries.

**Proposed:**
| Query Type | Temperature | Reason |
|-----------|-------------|--------|
| Definition/fact lookup | 0.1 | Deterministic, one correct answer |
| Legal analysis | 0.2 | Slight creativity for reasoning |
| Summarization | 0.3 | More flexibility for wording |
| Comparison | 0.2 | Structured but needs synthesis |

---

## PART 5: DATA & EMBEDDING IMPROVEMENTS

### 5.1 Migrate to gte-Qwen2-1.5B-instruct Embeddings

**Why:** (Already planned in `LAIV3/docs/EMBEDDING_MODEL_MIGRATION.md`)
- BGE-M3: 1024 dims, 8K token context -> truncates long legal sections
- gte-Qwen2-1.5B: 1536 dims, 32K token context -> no truncation, better semantic capture

**Impact:** Eliminates truncation errors that currently corrupt embeddings for long legal sections. 32K context means entire sections (sometimes 10-15 pages) can be embedded without loss.

**Cost:** Full re-embedding of ~19M chunks. At 270 chunks/sec = ~20 hours. Plus Alembic migration to change `vector(1024)` -> `vector(1536)`.

**Recommendation:** Do this AFTER completing the initial BGE-M3 backfill. Run both models in parallel for A/B comparison first.

### 5.2 Improve Chunking Strategy

**Current problems:**
- Chunks based on character count (3000 chars max) not semantic boundaries
- Legal cross-references split across chunks lose context
- Shingle size for dedup hardcoded to 3 words - misses near-duplicates with formatting variations

**Proposed improvements:**
- **Semantic chunking:** Split on legal structure markers (SS, Absatz, Artikel, Kapitel) first, fall back to sentence boundaries
- **Reference-aware chunking:** When a chunk references another section (SS 22 Abs. 1 BImSchG), include a brief context note from the referenced section
- **Overlap increase:** From 200 chars to 400 chars for German legal text (German compound words and subordinate clauses need more overlap to preserve meaning)
- **Normalize legal citations before dedup:** SS 195 vs SS195 vs Paragraph 195 should match

### 5.3 Temporal Awareness in Retrieval

**Problem:** No distinction between current and superseded laws. A query about "current BImSchG requirements" may retrieve chunks from a 2015 version superseded by 2023 amendments.

**Fix:**
- Populate `effective_date` and `is_current` fields during ingestion (currently empty)
- Build a law version registry: map (law_code, section) -> latest effective_date
- Default retrieval to `is_current=true` unless query explicitly asks for historical context
- Add temporal boosting: more recent documents score higher

### 5.4 Entity-Enriched Embeddings

**Problem:** Standard embeddings treat "SS 22 BImSchG" as just text. They don't understand it's a specific legal reference.

**Fix:**
- During embedding, prepend entity tags: `[LAW:BImSchG] [SECTION:22] [TOPIC:noise_protection] Original text...`
- This creates embeddings that cluster by legal topic, not just word similarity
- Use existing NER tags from `text_tagged` column (already populated) to generate prefixes

---

## PART 6: INFRASTRUCTURE & RELIABILITY

### 6.1 Result Caching

**Problem:** Same question asked twice triggers full retrieval + generation pipeline.

**Fix:**
- Redis LRU cache keyed on normalized query hash
- TTL: 1 hour for RAG results, 24 hours for document analysis
- Cache hit rate expected: 15-20% (many users ask similar legal questions)
- Invalidate on new document ingestion

### 6.2 Circuit Breaker Pattern

**Problem:** If embedding service is slow/down, every request waits 30s then fails. No graceful degradation.

**Fix:**
- Add circuit breaker for each external service (embedding, reranker, LLM)
- States: CLOSED (normal) -> OPEN (service down, fail fast) -> HALF-OPEN (test recovery)
- When embedding is OPEN: fall back to BM25-only search (50% quality but instant)
- When LLM is OPEN: return retrieved chunks without generation ("here are relevant sources")

V3 has `circuit_breaker.py` already but it's not wired into the pipeline.

### 6.3 Input Validation & Security

**Problem:** No validation of user input. Query length unbounded. No prompt injection detection.

**Fix:**
- Max query length: 2000 characters
- Prompt injection detection: flag queries containing system prompt override patterns
- Rate limiting per user (already in V3 config but verify enforcement)
- Session ID validation (UUID format check)

### 6.4 Structured Error Responses

**Problem:** Errors are silently swallowed. User doc search failures are caught with bare `except Exception:`.

**Fix:**
- Define error response schema: `{error_code, message, details, retry_after}`
- Classify errors: transient (retry), permanent (fix input), service (degrade gracefully)
- Return partial results with degradation notice rather than empty results

### 6.5 Logging & Observability Improvements

**Current gaps:**
- No per-query latency breakdown (retrieval vs reranking vs generation)
- No tracking of which chunks are most/least useful
- No query success/failure trending

**Fix:**
- Add structured logging per pipeline stage with timing
- Track chunk usage: which chunks appear in final responses (helps identify high-value content)
- Weekly report: top 10 failed query patterns, slowest pipeline stages, most-retrieved documents

---

## PART 7: FEEDBACK & LEARNING (Done Right)

V4's feedback loop was non-functional (quality_score updates never affected retrieval). Here's how to build it properly.

### 7.1 Functional Feedback Loop

**Problem in V4:** `chunk_quality` was updated but never read during retrieval.

**Fix:**
- Add `quality_score` to retrieval scoring: `final_score = rrf_score * quality_score`
- Default quality_score = 1.0 for all chunks
- Negative feedback reduces quality_score by 0.1 (not 0.2 - too aggressive)
- Positive feedback increases by 0.05 (slower to boost than to penalize)
- Floor at 0.1 (never fully suppress a chunk, human reviewers may disagree)
- Add `quality_score` as a column in the chunks table, index it

### 7.2 Feedback Categories

**V4's categories were too vague.** Expand to actionable categories:

| Category | Action | Automation |
|----------|--------|-----------|
| `wrong_law_cited` | Flag chunk, reduce quality | Manual review queue |
| `outdated_information` | Mark chunk `is_current=false` | Immediate effect |
| `hallucinated_content` | Reduce quality, log for training | Alert if >3 reports on same chunk |
| `incomplete_answer` | Log query for prompt improvement | Weekly review |
| `wrong_translation` | Flag chunk language | Re-process chunk |
| `irrelevant_result` | Reduce quality, retrain embedding? | After 5+ reports |

### 7.3 Trending & Analytics

- Track error rate per law_code: "BImSchG queries have 30% negative feedback"
- Track error rate per document_source: "openlegaldata chunks have 2x error rate"
- Surface top 10 worst-performing chunks weekly for manual review
- Auto-suppress chunks with quality_score < 0.3 from retrieval

---

## PART 8: TRAINING & MODEL IMPROVEMENTS

### 8.1 Expand Training Data

**Current:** 51,300 samples (60% synthetic, 30% LawInstruct, 10% GermanQuAD)

**Proposed additions:**
- **Wind energy specific QA:** Generate from VDR documents (contracts, permits, reports)
- **Court decision QA:** Extract from BGH, OVG, VG rulings on wind energy
- **Negative examples:** Queries where the model should refuse (insufficient context)
- **Multi-turn conversations:** Current training is single-turn only

**Target:** 100,000+ training samples with better domain coverage

### 8.2 Evaluate Larger Models

**Current:** Qwen2.5-7B (7B params)

**Consider:**
- **Qwen2.5-14B:** 2x parameters, fits on single A800 with 4-bit quantization. Better German legal reasoning.
- **Llama 3.1-8B:** Updated architecture, may outperform Qwen-7B on specific tasks
- **Mistral-7B-v0.3:** Strong on European languages

**Evaluation criteria:** Run all three on a held-out German legal QA benchmark. Compare: accuracy, citation quality, hallucination rate, latency.

### 8.3 DPO (Direct Preference Optimization) Training

**Why:** Current SFT training teaches the model what to say but not what NOT to say. DPO trains on preference pairs (good answer vs bad answer) to reduce hallucination.

**How:**
- Collect feedback data (correct vs incorrect responses) from production
- Create preference pairs: (query, good_response, bad_response)
- Train with DPO after SFT (2-stage training)
- Requires ~5,000 preference pairs minimum

### 8.4 Retrieval-Aware Fine-Tuning

**Problem:** LLM was fine-tuned on clean QA pairs but never sees RAG-style context (multiple chunks with varying relevance). In production, the model gets messy retrieved context.

**Fix:** Fine-tune on examples that include retrieved context:
```
System: You are a German legal expert.
Context: [Chunk 1: relevant] [Chunk 2: partially relevant] [Chunk 3: noise]
Question: What are the noise limits under BImSchG?
Answer: Based on [Source 1]...
```

This teaches the model to handle noisy context, cite correctly, and ignore irrelevant chunks.

---

## PART 9: UX & RESPONSE QUALITY

### 9.1 Fix Response Validator Edge Cases

**Problem:** Validation patterns are too aggressive - sometimes remove legitimate legal disclaimers.

**Fix:**
- Context-aware validation: keep "consult an attorney" when user asks for personal legal advice
- Don't strip uncertainty language when the law itself is ambiguous (e.g., "herrschende Meinung" vs "Mindermeinung")
- Add whitelist for legal uncertainty terms: "strittig", "umstritten", "nach h.M."

### 9.2 Extend Session Memory

**Current:** 24 hour expiry, max 10 turns, simple truncation.

**Proposed:**
- 7 day expiry (legal research spans multiple days)
- Semantic compression: summarize old turns instead of dropping them
- Cross-session reference: "you asked about BImSchG yesterday" should work

### 9.3 Streaming Responses

**Problem:** Users wait for full generation (2-5 seconds) before seeing any output.

**Fix:** Implement SSE (Server-Sent Events) streaming from vLLM through FastAPI to frontend. V4 has `stream_generate()` in `llm.py` - backport this.

### 9.4 Source Document Preview

**Problem:** Citations say [Source 1] but user can't see the actual source text.

**Fix:** Include in response metadata:
```json
{
  "citations": [
    {
      "id": 1,
      "text_snippet": "SS 22 Abs. 1 BImSchG...",
      "document": "BImSchG",
      "section": "SS 22",
      "confidence": 0.87
    }
  ]
}
```

---

## PRIORITY MATRIX

### P0 - Production Blockers (Week 1-2)
1. Complete embedding backfill (20h compute)
2. Populate BM25 search_vector column (2-4h compute)
3. Fix NUL byte corruption in batch ingestion
4. Implement missing API endpoints (download, analytics)
5. Wire audit logging and feedback storage

### P1 - High Impact (Week 3-6)
6. Backport multi-tenancy from V4
7. Add CRAG loop (for LEGAL_COMPLEX queries only)
8. Add web search fallback
9. Raise min similarity threshold to 0.5
10. Add result deduplication
11. Increase top_k to 7 and context to 4096 tokens

### P2 - Quality Improvements (Week 7-12)
12. Expand few-shot examples (12 domain-balanced)
13. Domain-aware prompt selection
14. German-first system prompts
15. Adaptive hybrid search weights
16. Pre-filter before vector search
17. Functional feedback loop (quality_score in retrieval)
18. Document analysis and contract comparison (from V4)
19. Streaming responses

### P3 - Strategic (Month 3-6)
20. Migrate to gte-Qwen2 embeddings (1536d, 32K context)
21. Semantic chunking with reference awareness
22. Temporal awareness in retrieval
23. DPO training with production feedback data
24. Retrieval-aware fine-tuning
25. Evaluate 14B model
26. Circuit breaker pattern
27. Result caching (Redis)

---

## EXPECTED IMPACT

| Improvement | Quality Impact | Latency Impact | Effort |
|-------------|---------------|----------------|--------|
| Embedding backfill | System works at all | - | Low |
| BM25 population | +20% recall (sparse search) | +50ms | Low |
| Multi-tenancy | New capability | +100ms (cross-schema) | High |
| CRAG loop | +10% precision | +300-2400ms | Medium |
| Web search fallback | -15% refusal rate | +500ms (when triggered) | Medium |
| Raise similarity threshold | -80% noise chunks | None | Trivial |
| Result dedup | -30% wasted context | None | Low |
| Top-k 7 + context 4096 | +15% answer completeness | +100ms | Trivial |
| Domain-aware prompts | +10% answer relevance | None | Medium |
| gte-Qwen2 embeddings | +5-10% semantic quality | Similar | High |
| DPO training | -30% hallucination | None | High |
| Retrieval-aware FT | +15% citation accuracy | None | High |
