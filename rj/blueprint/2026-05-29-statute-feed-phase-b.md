# Plan — Statute feed Phase B (write path)

**Date:** 2026-05-29 · **Owner:** rj · **Status:** PROPOSED — awaiting sign-off
**Why sign-off:** this phase writes to the **live `corpus_*` tables** that
`serve_rag` retrieves from.
**Context:** Phase A (read-only) is done — see `LAI/docs/statute_feed.md`.

## Goal
Ingest a German federal law end-to-end — fetch → parse → chunk → embed →
**transactionally upsert** into the live corpus — and prove it on **one** law
before any bulk run.

## Approach (reuse Phase A + the existing pipeline, don't reinvent)
1. Fetch + parse — `GesetzeImInternetClient` + `parse_law_xml` (done).
2. Segment-build — map `ParsedLaw.sections` → the segment dict
   `process_document` expects (`text`, `section=enbez`), with
   `doc_type="gesetz"`, `domain=categorize(slug)`, `source_url`, `language="de"`.
3. Chunk — `lai.pipeline.chunk.process_document` (§/Absatz-aware). Reuse.
4. Embed — `lai.pipeline.embed.embed_batch` (Qwen3-Embedding-8B :8003) → fp16 →
   **first 4000 dims** (must match `migrate_corpus._blob_to_halfvec`). Reuse.
5. Upsert — transactional, per law (below).

## Schema — migration 007 (additive, reversible)
- `statute_feed_state(slug PK, source_url, content_hash, jurabk, doc_id,
  last_seen, last_changed)` — the diff key. `content_hash` = sha256 of the
  parsed law text → detects amendments the current `ON CONFLICT DO NOTHING`
  path silently skips.

## Upsert transaction (per changed / new law)
One transaction:
1. `DELETE FROM corpus_parent_chunks WHERE doc_id = <law doc_id>` (cascades to children).
2. `INSERT` parents + children (`embedding halfvec(4000)` + `search_vector tsvector('german')`).
3. `UPSERT statute_feed_state` (slug, new hash, timestamps).
Removed-from-TOC law → `DELETE` by `doc_id`.

## Key decisions / risks
- **ID allocation** — `corpus_*` PKs are supplied BIGINTs (no DEFAULT). Feed rows
  need a non-colliding range. Options: (a) dedicated high base ≥ 9e9 + a
  sequence, or (b) deterministic hash-based ids. **Decide before writing.**
- **Live retrieval** — per-law transaction keeps retrieval consistent; run on
  ONE law first and verify via a `serve_rag` query before any backfill.
- **Truncation parity** — reuse `migrate_corpus`'s exact fp16/first-4000 path.
- **`doc_id` stability** — `doc_id = hash(slug)` so re-ingest targets the same
  rows (citation stability across amendments).

## Steps
1. Write + apply migration 007 (`statute_feed_state`).
2. `statute_feed`: add segment-builder + chunk/embed/upsert behind
   `ingest --only <slug>` (dry-run stays the default — never auto-writes).
3. End-to-end on one law (`--only bimschg`): ingest → confirm `corpus_*` rows →
   `serve_rag` query returns the new §.
4. Idempotency: re-run → hash unchanged → no-op; amend → re-embed.
5. Unit-test the pure bits (segment builder, id allocation, hash diff).

## Open questions for sign-off
1. ID range — high-base sequence vs deterministic hash?
2. First backfill scope — the 29 mapped wind-relevant laws, or all 6,123?
3. OK to write to live `corpus_*` on `lai_db`, or stage to a copy first?

## Definition of done
One law fully ingested + retrievable via `serve_rag`; re-run is idempotent;
nothing else in the corpus disturbed.
