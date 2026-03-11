"""
lai.pipeline — Data processing pipeline for RAG and fine-tuning.

Steps:
    1. convert  — Raw files (MinIO) → normalized text segments
    2. chunk    — Segments → parent-child chunks (PostgreSQL)
    3. classify — Domain classification via LLM (parent chunks)
    4. enrich   — Contextual retrieval prefix via LLM (child chunks)
    5. generate — Synthetic fine-tuning data via LLM (parent chunks)
    6. embed    — Vector embeddings → pgvector (child chunks)
"""
