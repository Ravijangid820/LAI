# LAI ingest scripts

Standalone fetch / normalise scripts that pull external sources onto disk in
a shape Phase B's chunker + embedder can pick up later. Read-only against the
running stack — none of these touch the corpus tables or the live retrieval
indexes.

---

## `fetch_gesetze.py` — one law from gesetze-im-internet.de

Idempotent, single-law fetcher for the German federal statute portal. Reuses
the production `GesetzeImInternetClient` (httpx + tenacity retry, metrics,
defusedxml-hardened parse) shipped in `lai.common.connectors`. Writes a
reviewable, re-fetchable tree under `LAI/data/statutes/<slug>/`:

```
data/statutes/bimschg/
    meta.json              # jurabk, long_title, xml_sha256, sections index
    sections/
        0000_eingangsformel.json
        0001_1.json        # § 1
        0002_2.json        # § 2
        ...
```

Each section JSON: `{seq, law_slug, jurabk, enbez, titel, text, sha256, fetched_at}`.
`xml_sha256` in `meta.json` is the fast-path skip key — re-running with the
same upstream XML is a no-op.

```bash
# Default (BImSchG):
.venv/bin/python scripts/ingest/fetch_gesetze.py

# Custom output root:
.venv/bin/python scripts/ingest/fetch_gesetze.py --out /tmp/statutes

# Re-write even if xml.zip hasn't changed:
.venv/bin/python scripts/ingest/fetch_gesetze.py --force
```

### Extending to BauGB, EEG, BNatSchG, …

The script takes any slug gesetze-im-internet.de uses. To discover slugs, run
the dry-run TOC tool that already exists (Phase 4.3 A — by rj):

```bash
.venv/bin/python -m lai.pipeline.statute_feed --fetch-sections
```

That prints every mapped slug with its categorised domain. Then:

```bash
.venv/bin/python scripts/ingest/fetch_gesetze.py --slug baugb
.venv/bin/python scripts/ingest/fetch_gesetze.py --slug eeg_2023
.venv/bin/python scripts/ingest/fetch_gesetze.py --slug bnatschg
```

Outputs land beside `bimschg/` under `data/statutes/`. No code change needed
per law.

### What this is NOT

* It's not a daily cron — Phase C's `corpus_*` write path + scheduled run
  lives in `lai.pipeline.statute_feed` (Phase B/C). This script is the
  on-disk staging step.
* It's not authoritative for ingestion versions yet — Phase B's
  `statute_feed_state` table (migration 007) is the source of truth for
  what's in the corpus. The on-disk SHA here is only a re-fetch shortcut.

### Heads-up: don't modify

* `data/_legacy_segments/` — the old per-§ corpus from the legacy pipeline.
  Reference only.
* `training/fine_tuning/` — the prior LoRA training set. Reference only.
