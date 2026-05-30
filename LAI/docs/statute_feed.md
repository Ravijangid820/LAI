# Statute ingestion feed (gesetze-im-internet.de) — Phase 4.3

Keeps the German federal-statute portion of the corpus current by fetching
[gesetze-im-internet.de](https://www.gesetze-im-internet.de), detecting
new/changed/removed laws, re-chunking + re-embedding only the changes, and
upserting them into the served `corpus_*` tables that `serve_rag` retrieves
from.

Roadmap: `harsh/ROADMAP_2026Q3.md` §4.3. Tracked in `harsh/PROGRESS_V2.md`.

## Why a feed

Fine-tuned weights and a one-shot corpus build both go stale the moment a law
is amended (BImSchG novellen, new EEG, …). The feed is the "what the law says
**today**" half of the architecture — RAG over current statute text alongside
the model's reasoning.

## Components

| Piece | Location | Role |
|---|---|---|
| `GesetzeImInternetClient` | `lai.common.connectors.gesetze` | Fetch `gii-toc.xml` (the law index) + download/unzip a law's `xml.zip`. httpx + tenacity + metrics + typed errors. |
| `parse_law_xml` / `parse_toc` | `lai.common.connectors._gii_parser` | Pure parsers: law XML (`<norm>` → sections) and the TOC (`<item>` → `LawRef`). |
| Category registry | `lai.common.connectors.statute_categories` | **Single source of truth** mapping law slug → legal domain. |
| Feed CLI | `lai.pipeline.statute_feed` | Orchestration. Today: read-only dry-run. Later: chunk → embed → upsert. |

## Category scheme (the "modular" partitioning)

Every federal law is covered, but each is tagged with a **legal-domain
category** so the corpus stays partitioned and filterable. Categories are the
**same taxonomy** `lai.pipeline.classify` assigns to the rest of the corpus
(the `parent_chunks.domain` field):

```
immissionsschutzrecht · energierecht · baurecht · umweltrecht · vertragsrecht
gesellschaftsrecht · grundstuecksrecht · arbeitsrecht · steuerrecht
verwaltungsrecht · prozessrecht
```

Wind-energy-relevant laws are mapped explicitly (~29 today); everything else
falls back to **`allgemein`** (classify's own catch-all) — so coverage is
total, the corpus is grouped, and Phase B can write `domain` directly without
re-running the LLM classifier on statute text.

### Adding a law or category

1. Find the law's slug from its gesetze-im-internet.de URL
   (`/<slug>/xml.zip` — e.g. `bimschg`, `eeg_2014`). GII often appends a
   consolidation year (`enwg_2005`, `rog_2008`); the dry-run's slug-validation
   tells you the exact value.
2. Add one line to `_DOMAIN_BY_SLUG` in
   `lai.common.connectors.statute_categories`.
3. A new **domain** must also be added to `lai.pipeline.classify`'s `DOMAINS`
   so the two taxonomies stay in sync (a unit test guards that every mapped
   domain is a known one).

## Running the dry-run (read-only)

```bash
# Summary: TOC size, per-domain counts, registry-slug validation. Writes nothing.
uv run python -m lai.pipeline.statute_feed

# Also download + parse a sample to validate the full fetch→unzip→parse chain.
uv run python -m lai.pipeline.statute_feed --fetch-sections --limit 5
```

Last live run (2026-05-29): TOC = **6,123 laws**, 29 explicitly categorised,
every registry slug resolved; sample parse OK (BauGB 298 sections, AktG 394, …).

## Status & remaining phases

- **Phase A — read-only — DONE 2026-05-29.** Connector + parsers + registry +
  dry-run, all validated against live data. Unit tests under mypy `--strict`
  + ≥85 % coverage.
- **Phase B — write path — DONE 2026-05-30.** Migration 007 applied
  (`statute_feed_state` + `corpus_feed_id_seq` ≥ 9 × 10⁹). `--ingest <slug>`
  chunks via `lai.pipeline.chunk.process_document`, embeds via
  `lai.pipeline.embed.embed_batch` (Qwen3-Embedding-8B, fp16 → first 4000
  dims to match `corpus_child_chunks.embedding halfvec(4000)`), transactional
  per-law upsert. Verified live: bimschg → 120 parents + 245 children in 23.9 s;
  re-run skipped in 1.5 s.
- **Phase C — operationalize — in progress.**
  - **Step 1 — mapped backfill — DONE 2026-05-30.** 29/29 wind-relevant laws
    ingested in 12.1 min → 5,762 parents + 9,133 children across all 11
    `classify.py` domains.
  - **Step 2 — extra CLI — DONE 2026-05-30.** Refactored `_ingest_one` so all
    backfill modes share one HTTP client (no per-law TOC re-fetch). Added
    `--backfill all [--limit N]`, `--prune-removed [--missing-days N]`,
    `--status`.
  - **Step 3 — ops wrapper + cron — DONE 2026-05-30.**
    `scripts/ops/statute_feed.sh` (modes: `--status`, `--mapped`, `--full`,
    `--prune`, `--tail`, `--stop`) + documented daily-mapped / weekly-full /
    weekly-prune cron lines in `scripts/ops/README.md`.
  - **Step 4 — weekend full sweep — pending.** Scheduled run (~43 h) to be
    triggered in the agreed Sunday-22:00 window.
