# Plan — Statute feed Phase C (backfill + operationalize)

**Date:** 2026-05-30 · **Owner:** rj · **Status:** PROPOSED — awaiting sign-off
**Why sign-off:** this phase touches the live `corpus_*` tables at **bulk
scale** (up to 6,123 laws), runs **unattended** on a schedule, and adds
**pruning** logic that DELETEs corpus rows when a law disappears from the TOC.
**Context:** Phase B (one-law write path) is DONE — see
[`2026-05-29-statute-feed-phase-b.md`](./2026-05-29-statute-feed-phase-b.md).

## Goal
Move from "one law ingested by hand" to "**every** federal law ingested and
kept current automatically." Cover the 29 wind-relevant laws daily, sweep the
full TOC weekly, prune the dead, and operate it from a single ops command.

## Approach (incremental, each level is shippable on its own)
1. **Backfill the 29 mapped laws** — sequential loop over `mapped_slugs()`,
   per-law try/except, summary at the end. ~29 × 25 s ≈ **12 minutes**.
   Idempotent: re-runs `[skip]` unchanged laws cheaply.
2. **Full backfill of all 6,123 laws** — same loop over the live TOC. At
   ~25 s/law that's ~**43 hours**, so:
   * Resumable via the `statute_feed_state.content_hash` check (already
     present from Phase B; in-progress re-runs pick up where they stopped).
   * Backgrounded via `setsid + nohup` like `resume_step6.sh`.
   * `--limit N` for incremental partial sweeps.
3. **Pruning removed laws** — after a full pass, any
   `statute_feed_state.slug` whose `last_seen` is older than the pruning
   window AND is absent from the current TOC gets `DELETE`d from
   `corpus_parent_chunks` (cascades to children) + `statute_feed_state`.
4. **Daily + weekly schedule** —
   * **Daily 03:00 (`--mapped`)**: the 29 wind-relevant laws. Quick (~12 min),
     covers what matters most, runs in the quiet window.
   * **Weekly Sunday 22:00 (`--full`)**: full TOC sweep + prune. Background
     job; ~2 days of runtime, finishes by Tuesday.
5. **Ops wrapper + docs** — `scripts/ops/statute_feed.sh` mirroring
   `resume_step6.sh` conventions; documented in `scripts/ops/README.md`.

## CLI surface to add to `lai.pipeline.statute_feed`
```
--ingest <slug>          # (exists) one law
--backfill mapped        # the 29 registry-mapped laws
--backfill all [--limit N]   # the full TOC, resumable
--prune-removed [--missing-days 7]  # DELETE laws gone from the TOC for N days
--status                 # counts: laws in state, by domain; last_seen distribution
```

## Schema additions
None. The Phase B schema (`statute_feed_state` + `corpus_feed_id_seq`) is
sufficient. The `last_seen` column already supports the pruning window.

## Key decisions / risks
- **Cadence — daily mapped + weekly full** (recommended): keeps the
  high-value 29 laws current daily (cheap), full sweep weekly catches the
  long tail. Alternative is rolling 500/day for ~13-day cycle, but bursty
  weekly is simpler to reason about.
- **Pruning window — 7 days of consecutive misses.** A single transient TOC
  fetch error must NOT delete corpus rows. `statute_feed_state.last_seen` is
  updated on every visit (even no-op skips); if it's older than 7 days AND
  the slug is missing from the current TOC, the law is genuinely gone.
- **Embed-server contention — `:8003` is shared with `serve_rag` /query.**
  Schedule the heavy full pass overnight (Sunday 22:00 onward). Daily mapped
  is small enough (12 min, 03:00) to not matter. Consider raising
  `GesetzeConfig.request_interval_seconds` during business hours if needed.
- **Live retrieval consistency** — each per-law transaction is the same
  pattern as Phase B (DELETE-then-INSERT); queries see the old or the new
  version, never a half-applied one.
- **Error containment** — per-law try/except. One bad XML (corrupt zip, parse
  error, transient 5xx after retries) must not abort the batch. Log the
  failed slug and continue.
- **Concurrency** — sequential only. Parallelising across the same shared
  `:8003` server gains nothing and risks tail latency.

## Steps
1. Add `--backfill mapped` to `statute_feed.py` — loop + per-law try/except +
   summary print.
2. Run `--backfill mapped` live. Verify 29 laws in `statute_feed_state`,
   sample 3-4 via `serve_rag`-style direct query.
3. Add `--backfill all [--limit N]` + `--prune-removed [--missing-days N]` +
   `--status`.
4. Smoke-test full backfill with `--limit 50` (a ~20-min sample), then commit
   to a full background run.
5. Write `scripts/ops/statute_feed.sh` (modes: `--mapped`, `--full`,
   `--status`, `--stop`); document in `scripts/ops/README.md`; add the two
   cron lines.
6. Update `LAI/docs/statute_feed.md` with the operational sections.

## Open questions for sign-off
1. **Schedule cadence — daily-mapped + weekly-full, or different?**
2. **Pruning window — 7 days, or longer/never?** (Conservatism vs. corpus
   freshness.)
3. **OK to run the full background sweep over a weekend** — Sunday 22:00 →
   Tuesday ~17:00 — given `:8003` is shared with chat? Or wait for off-hours?
4. **Cron host — same box?** (No separate ops/automation host today.)

## Definition of done
- All 29 mapped laws live in the corpus + retrievable via `serve_rag`.
- A successful full TOC sweep on record (counts + duration in
  `statute_feed_state`).
- Daily + weekly cron lines installed, last successful run recorded.
- `statute_feed.sh` ops wrapper + ops README entry.
- `harsh/PROGRESS_V2.md` 4.3 marked Phase C DONE.
