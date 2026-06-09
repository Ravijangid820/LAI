# DR runbook for `lai_db` — split-strategy backup

**Date:** 2026-06-10 · **Owner:** rj · **Phase:** 4.5.3 (pre-pilot
hygiene gap from `FULL_TESTING_GUIDE.md §11`) · **Status:** PLAN —
implementation in progress

## TL;DR

Today the honest answer to "what happens if your DB dies" is "we
don't know — there are zero backups." This blueprint closes that
with a deliberately **asymmetric** strategy that matches the actual
data shape: dump the ~400 MB of irreplaceable user content nightly,
do NOT dump the 901 GB of derived corpus (it's reproducible from
immutable MinIO raw), document the regen path honestly.

## Current state (verified 2026-06-10)

| Check | Result |
|---|---|
| Existing backup mechanism | **None.** No `pg_dump` / `pgbackrest` / `wal-*` / snapshot job anywhere in scripts, cron, or systemd timers. |
| `/data/backups/{daily,weekly,monthly,manual_emergency}/` | Tree exists (created Nov 2025, owned `root:root`), **all empty** — aspirational only. Not writable by `rj`. |
| `LAI/processed/backups/post-recovery-2026-04-24/` | One-off post-incident dump from April. Not part of an ongoing rotation. |
| `lai_db` size | **902 GB** (`SELECT pg_database_size(...)`) |
| Active Postgres | `lai_postgres_main` (pgvector/pgvector:pg16, up 2 weeks, healthy) — bind-mount `/data/projects/lai/LAI/data/postgres` ↔ `/var/lib/postgresql/data` |
| Free disk on `/data` | 776 GB of 7 TB (89 % used) |

## Why a naive nightly `pg_dump` is wrong

A full `pg_dump | gzip` of 902 GB:

- Output file: ~200-400 GB (embeddings compress poorly — fp16 floats
  are near-random bytes).
- Wall-time: 1-2 hours, IO-saturating the same disk that's serving
  live queries.
- Retention (14 daily + 4 weekly + ∞ monthly): 5-15 TB → **exceeds
  free disk** by a wide margin.
- Recovery time: same ~1-2 hours to restore.
- **Most of the bulk is derived data.** It's like backing up the
  output of your build cache instead of the source.

## Per-table breakdown (the real picture)

```
 corpus_child_chunks     | 865 GB   ← derived from MinIO + Step 6
 corpus_parent_chunks    |  36 GB   ← derived from MinIO + Step 1-4
                         |          ─────────  total: 901 GB
 matter_chunks           | 337 MB   ← user-uploaded VDR docs (irreplaceable)
 ddiq_doc_chunks         |  49 MB   ← user-uploaded contract source
 ddiq_reports            | 3.5 MB
 statute_feed_state      | 1.9 MB   ← reproducible from gesetze-im-internet.de
 ddiq_documents          | 1.4 MB
 audit_log               | 328 KB   ← LEGAL, EU AI Act Art. 12, MUST preserve
 users                   | 216 KB
 refresh_tokens          | 176 KB
 ddiq_project_areas      | 176 KB
 ddiq_contracts          | 120 KB
 org_invitations         |  96 KB
 password_reset_tokens   |  64 KB
 ddiq_*_shares           |  64 KB
 ddiq_contract_parcels   |  64 KB
 organizations, projects,| <100 KB each
 conversations, messages |
```

Irreplaceable-user-content sum: **~400 MB uncompressed**, ~120 MB
gzipped. Trivial to dump, trivial to keep forever.

## Decision: split strategy

### Category A — Irreplaceable user content (dump nightly)

`audit_log` · `users` · `refresh_tokens` · `password_reset_tokens` ·
`organizations` · `org_invitations` · `projects` · `project_members` ·
`conversations` · `messages` · `matter_chunks` · `ddiq_*` (every
ddiq_-prefixed table).

- **Tool:** `pg_dump -Fc -t <table> -t <table> …` (custom format,
  selectable restore).
- **Schedule:** nightly at 02:30 (off-peak — statute_feed runs 03:00).
- **Retention:** 14 daily + 4 weekly (Sundays) + monthly indefinite.
- **Expected size per dump:** ~120 MB gzipped.
- **Lifetime disk:** ~5-10 GB total.
- **Backup target:** `/data/projects/lai/LAI/data/postgres-backups/`
  (new dir, mode `0750`, owned `rj:ks_admin`).

### Category B — Reproducible corpus (do NOT dump; document regen)

`corpus_child_chunks` · `corpus_parent_chunks` ·
`corpus_migration_state` · `statute_feed_state`.

- **Source of truth:** immutable raw under MinIO `lai-raw` bucket
  (672 GB) + `gesetze-im-internet.de` (re-fetchable).
- **Regen procedure:** `lai.pipeline.cli step1..step6` for VDR/library
  corpus + `scripts/ops/statute_feed.sh --full` for statutes.
- **Regen wall-time:** ~46 h (Step 6 embedding is the slow step) +
  ~4 h statute sweep = ~50 h cold-start.
- **Honest claim:** RPO = 0 on the inputs (raw is immutable +
  MinIO has its own backup at `/data/projects/lai/minio-backup`).
  RTO = ~50 h for a from-scratch rebuild.

### Schema (dump every night, separately)

`pg_dump --schema-only` of the whole DB → tiny file (~few hundred KB),
lets us re-create the structure even if the data dump is corrupt.

## RPO / RTO commitments — honest numbers

| Data category | RPO | RTO | How |
|---|---|---|---|
| User accounts, audit log, matters, DDiQ reports | **24 h** | **~10 min** | Nightly logical dumps → `pg_restore -t <table> …` |
| Schema / DDL only | 24 h | ~5 min | `psql < schema.sql` on a fresh empty DB |
| Legal corpus (902 GB) | **0 on raw** in MinIO | **~50 h** | Re-run `lai.pipeline.cli step1..step6` + `statute_feed.sh --full` |
| Combined cold-start | 24 h user + 0 corpus | ~50 h | Schema → user restore → corpus regen in parallel |

## What this does NOT cover (honest gaps)

1. **Same-filesystem risk.** Backups live on `/data/nvme1n1p1`
   alongside the live DB. A filesystem-level failure (RAID loss,
   accidental `rm -rf /data`) takes both. **Mitigation deferred:**
   would need either (a) an off-host copy to a second machine, or
   (b) rsync to S3-compatible object storage. Flag for post-pilot.
2. **No PITR.** We get daily snapshots, not point-in-time recovery.
   Up to 24 h of user content can be lost. WAL archiving + a
   replica is the right tool; out of scope for a 1-day closure.
3. **Backup integrity is not verified on every run.** We rely on
   `gzip -t` only; no periodic restore-rehearsal cron. Add a
   weekly rehearsal job if a pilot firm asks.
4. **No encryption at rest for the backup files.** They're on a
   project-private filesystem (`drwxrwsr-x ks_admin`), but a pilot
   firm handling regulated client data would expect dumps to be
   GPG-encrypted before shipping anywhere off-host. Add when
   off-host shipping lands.

## Implementation plan (today)

1. Create `/data/projects/lai/LAI/data/postgres-backups/{daily,weekly,monthly}/`.
2. Write `LAI/scripts/ops/backup_postgres.sh`:
   - `docker exec lai_postgres_main pg_dump …` (so we don't need
     pg_dump on the host).
   - Schema-only dump → `schema_YYYY-MM-DD.sql`.
   - Per-irreplaceable-table dump using `-Fc -t …` → single custom
     archive file `user_data_YYYY-MM-DD.dump`.
   - Both gzipped (custom-format archives compress poorly but
     gzip-of-custom is still fine for transport).
   - Verify with `gzip -t` + `pg_restore -l` (list contents without
     restoring).
   - Rotate: Sunday → also copy to `weekly/`. 1st of month → also
     copy to `monthly/`. Delete daily older than 14 days. Delete
     weekly older than 35 days. Never delete monthly.
3. Add cron line: `30 2 * * * bash LAI/scripts/ops/backup_postgres.sh
   >> LAI/logs/host/backup_postgres.log 2>&1`.
4. Run it once manually; verify dump file present + `pg_restore -l`
   lists expected tables.
5. **Restore rehearsal:** spin up scratch container on port 5499,
   restore dump, `SELECT count(*) FROM audit_log` matches source,
   tear down.
6. Append "Disaster recovery" section to `LAI/scripts/ops/README.md`
   with backup schedule, restore command (single copy-paste block),
   honest RPO/RTO table, corpus-regen pointer.

## Rollback

If the backup approach turns out wrong:
```bash
# Remove the cron line:
crontab -l | grep -v backup_postgres.sh | crontab -

# Remove backup directory (only if certain — destroys all dumps):
rm -rf /data/projects/lai/LAI/data/postgres-backups

# Remove the script (and the README section):
rm /data/projects/lai/LAI/scripts/ops/backup_postgres.sh
# revert README via git
```

## What a pilot firm gets to hear

> "We separate the question of *what's irreplaceable* from *what's
> reproducible*. Your matter documents, reports, user accounts, and
> audit log get nightly dumps with 14-day rolling + monthly
> retention; restore is a single command and we've test-restored it
> against a scratch DB. The German legal corpus is 902 GB but
> entirely reproducible from immutable source documents in our
> object store — we don't waste backup capacity on it. The honest
> RPO/RTO numbers are 24 h / 10 min for your content, 0 / ~50 h for
> the corpus rebuild. A future revision will add off-host backup
> shipping; today everything lives on the same machine, which is a
> known limitation we'll close before contract sign."

## Related

- [`feedback_blueprint_docs.md`](../memory-not-applicable) — blueprint convention
- `harsh/PROGRESS_V2.md` row 4.5.3 — work item this closes
- `LAI/scripts/ops/README.md` — DR section target (append)
- `Docker/database/pgvector/docker-compose.yml` — the container we
  back up
- `/data/projects/lai/minio-backup` — MinIO's own backup (separate
  concern, already exists)
