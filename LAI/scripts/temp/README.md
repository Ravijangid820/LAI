# Per-format data processors

Experimental / format-specific corpus processors. Unlike the generic
`lai.pipeline.convert` step, each script here understands the structure
of one file format and extracts richer metadata + chunking.

## Why per-format?

A German **Urteil** has legally-meaningful sections — `Tenor`, `Tatbestand`,
`Entscheidungsgründe`, sometimes `Leitsatz`. Throwing the whole file into a
generic 3072-char-parent chunker loses this structure; a legal assistant
answering "What did the court hold?" should retrieve the *Tenor*, not the
*Tatbestand*.

A Due-Diligence PDF report, by contrast, is hierarchical but not standardized
— "1 Executive Summary", "2 Legal Framework", etc. Needs different handling.

## Layout

```
scripts/temp/
  inspect_<source>.py         structure audit — run first, understand the data
  process_<source>.py         produces V5-compatible segments JSONL
  README.md                   this file
```

Each `process_*` writes to the same output format as Step 1 of the main
pipeline (`<bucket>/<path>/<file>.segments.jsonl`) so the downstream
steps (2–6) can consume them without changes.

## Source priorities (2026-04)

| Source | Size | Files | Priority | Status |
|---|---|---|---|---|
| `hf_cases` | 13 GB | 251K | **HIGH** — court decisions | `inspect_hf_cases.py` → `process_hf_cases.py` |
| `openlegaldata_api_dump` | 1.5 GB | 4,174 pages × 10 cases ≈ 41K | **HIGH** — likely overlaps w/ hf_cases, dedupe by `slug`/`ecli` | TBD |
| `Libary` | 5.4 GB | 2,326 | MED — legal reference PDFs | TBD |
| `multilegalpile` | 643 GB | 132K | LOW — 96% non-German | deferred |

## Output schema (matches Step 1)

```json
{
  "doc_id": "<hash16>",
  "source_file": "legal_data/hf_cases/case_000000.json",
  "language": "de",
  "doc_type": "urteil",            // or "beschluss"
  "segments": [
    {
      "text": "...",
      "section": "Tenor",          // structured: Tenor, Tatbestand, ...
      "page_start": null,
      "page_end": null,
      "type": "text"
    }
  ],
  "metadata": {
    "court_name":       "Landgericht Köln",
    "court_level":      "Landgericht",
    "jurisdiction":     "Ordentliche Gerichtsbarkeit",
    "file_number":      "84 O 249/18",
    "decision_date":    "2029-11-13",
    "ecli":             "ECLI:DE:LGK:2029:1113.84O249.18.00",
    "slug":             "lg-koln-2029-11-13-84-o-24918"
  }
}
```

The metadata block is what enables **metadata filtering at query time**
(filter by court, date range, level, jurisdiction) — the single biggest
retrieval-quality lever we haven't yet pulled.
