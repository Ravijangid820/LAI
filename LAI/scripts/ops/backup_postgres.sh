#!/usr/bin/env bash
# backup_postgres.sh — nightly logical dump of lai_db's irreplaceable tables.
#
# WHAT THIS BACKS UP:
#   - Schema-only (DDL) of the whole database.
#   - Per-table data of every "irreplaceable" user-content table
#     (user accounts, audit log, matters, DDiQ, conversations, ...).
#
# WHAT IT DOES NOT BACK UP — INTENTIONALLY:
#   - corpus_child_chunks (865 GB) — reproducible from MinIO via Step 1-6.
#   - corpus_parent_chunks (36 GB) — same.
#   - statute_feed_state — reproducible from gesetze-im-internet.de.
#   See rj/blueprint/2026-06-10-dr-runbook.md for the rationale.
#
# OUTPUT:
#   /data/projects/lai/LAI/data/postgres-backups/
#     daily/   schema_YYYY-MM-DD.sql.gz + user_data_YYYY-MM-DD.dump   (14-day rolling)
#     weekly/  same shape, copied every Sunday                        (35-day rolling)
#     monthly/ same shape, copied on the 1st                          (kept indefinitely)
#
# RESTORE: see "Disaster recovery" in scripts/ops/README.md.
#
# Cron line (already documented in scripts/ops/README.md):
#   30 2 * * *  bash /data/projects/lai/LAI/scripts/ops/backup_postgres.sh \
#       >> /data/projects/lai/LAI/logs/host/backup_postgres.log 2>&1

set -euo pipefail
umask 0027

# --- config ---------------------------------------------------------------
LAI_ROOT="/data/projects/lai/LAI"
BACKUP_ROOT="${LAI_ROOT}/data/postgres-backups"
LOG_DIR="${LAI_ROOT}/logs/host"
CONTAINER="lai_postgres_main"
DB="lai_db"
USER_="lai_user"

DAILY_RETAIN_DAYS=14
WEEKLY_RETAIN_DAYS=35

# Allowlist of irreplaceable user-content tables. New user-state tables
# MUST be added here when introduced; this is intentionally explicit so a
# new table doesn't silently fall off the backup. ddiq_* are listed
# individually for the same reason.
USER_TABLES=(
    audit_log
    users
    refresh_tokens
    password_reset_tokens
    organizations
    org_invitations
    projects
    project_members
    conversations
    messages
    matter_chunks
    ddiq_documents
    ddiq_doc_chunks
    ddiq_reports
    ddiq_contracts
    ddiq_contract_parcels
    ddiq_classified_parcels
    ddiq_project_areas
    ddiq_parcel_cache
    ddiq_geocode_cache
    ddiq_document_shares
    ddiq_report_shares
)

# --- helpers --------------------------------------------------------------
ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

# --- preflight ------------------------------------------------------------
mkdir -p "${BACKUP_ROOT}/daily" "${BACKUP_ROOT}/weekly" "${BACKUP_ROOT}/monthly" "${LOG_DIR}"

docker inspect "${CONTAINER}" >/dev/null 2>&1 || die "container ${CONTAINER} not found"
docker exec "${CONTAINER}" pg_isready -U "${USER_}" -d "${DB}" >/dev/null 2>&1 \
    || die "${CONTAINER} not ready for connections"

# Build the -t flag list. pg_dump treats -t as repeatable; that's how we
# select multiple tables without dumping the corpus.
TABLE_FLAGS=()
for t in "${USER_TABLES[@]}"; do
    TABLE_FLAGS+=( -t "public.${t}" )
done

TODAY="$(date +%Y-%m-%d)"
DOW="$(date +%u)"        # 1..7, Monday=1
DOM="$(date +%d)"        # 01..31

SCHEMA_GZ="${BACKUP_ROOT}/daily/schema_${TODAY}.sql.gz"
USER_DUMP="${BACKUP_ROOT}/daily/user_data_${TODAY}.dump"

log "backup start: ${CONTAINER}/${DB} → ${BACKUP_ROOT}"
log "  schema → ${SCHEMA_GZ}"
log "  user_data (${#USER_TABLES[@]} tables) → ${USER_DUMP}"

# --- 1. schema-only dump (plain SQL, gzipped) -----------------------------
# --no-owner / --no-privileges so the dump restores into any DB regardless
# of role names. Keeps it portable to a scratch container.
docker exec "${CONTAINER}" pg_dump \
        -U "${USER_}" -d "${DB}" \
        --schema-only --no-owner --no-privileges --no-tablespaces \
    | gzip -9 > "${SCHEMA_GZ}.tmp"
mv "${SCHEMA_GZ}.tmp" "${SCHEMA_GZ}"
chmod 0640 "${SCHEMA_GZ}"   # parent dir has default ACL that grants other::r-x; override.
SCHEMA_BYTES="$(stat -c %s "${SCHEMA_GZ}")"
log "  schema dump: ${SCHEMA_BYTES} bytes"

# Verify gzip integrity.
gzip -t "${SCHEMA_GZ}" || die "schema dump failed gzip integrity check"

# --- 2. user-data dump (custom format, internally compressed) -------------
# -Fc = custom format; pg_restore can list contents, do selective restore,
# and parallel-restore. Don't gzip — it's already compressed (level 6).
docker exec "${CONTAINER}" pg_dump \
        -U "${USER_}" -d "${DB}" \
        -Fc --no-owner --no-privileges --no-tablespaces \
        --data-only \
        "${TABLE_FLAGS[@]}" \
    > "${USER_DUMP}.tmp"
mv "${USER_DUMP}.tmp" "${USER_DUMP}"
chmod 0640 "${USER_DUMP}"   # contains PII (audit_log identity events, user emails) — no world-read.
USER_BYTES="$(stat -c %s "${USER_DUMP}")"
log "  user_data dump: ${USER_BYTES} bytes"

# Verify pg_restore can read the archive header + list contents.
# This catches corruption before we trust the dump.
ARCHIVE_ENTRIES="$(docker exec -i "${CONTAINER}" pg_restore --list < "${USER_DUMP}" 2>/dev/null | grep -c '^[0-9]' || true)"
[ "${ARCHIVE_ENTRIES}" -gt 0 ] || die "user_data dump archive list is empty — possible corruption"
log "  user_data archive: ${ARCHIVE_ENTRIES} restorable entries"

# --- 3. weekly / monthly promotion ----------------------------------------
# Sundays (DOW=7) → copy into weekly/
if [ "${DOW}" = "7" ]; then
    cp -p "${SCHEMA_GZ}" "${BACKUP_ROOT}/weekly/"
    cp -p "${USER_DUMP}" "${BACKUP_ROOT}/weekly/"
    chmod 0640 "${BACKUP_ROOT}/weekly/$(basename "${SCHEMA_GZ}")" "${BACKUP_ROOT}/weekly/$(basename "${USER_DUMP}")"
    log "  promoted to weekly/"
fi

# 1st of month → copy into monthly/
if [ "${DOM}" = "01" ]; then
    cp -p "${SCHEMA_GZ}" "${BACKUP_ROOT}/monthly/"
    cp -p "${USER_DUMP}" "${BACKUP_ROOT}/monthly/"
    chmod 0640 "${BACKUP_ROOT}/monthly/$(basename "${SCHEMA_GZ}")" "${BACKUP_ROOT}/monthly/$(basename "${USER_DUMP}")"
    log "  promoted to monthly/"
fi

# --- 4. rotation ----------------------------------------------------------
# Daily — keep last 14 days.
find "${BACKUP_ROOT}/daily" -maxdepth 1 -type f \
    \( -name 'schema_*.sql.gz' -o -name 'user_data_*.dump' \) \
    -mtime "+${DAILY_RETAIN_DAYS}" -delete -print | while read -r f; do
    log "  rotated out daily: $(basename "${f}")"
done

# Weekly — keep last 35 days (5 weeks).
find "${BACKUP_ROOT}/weekly" -maxdepth 1 -type f \
    \( -name 'schema_*.sql.gz' -o -name 'user_data_*.dump' \) \
    -mtime "+${WEEKLY_RETAIN_DAYS}" -delete -print | while read -r f; do
    log "  rotated out weekly: $(basename "${f}")"
done

# Monthly — never auto-rotated.

# --- 5. summary -----------------------------------------------------------
DAILY_COUNT="$(find "${BACKUP_ROOT}/daily" -maxdepth 1 -type f -name 'schema_*.sql.gz' | wc -l)"
WEEKLY_COUNT="$(find "${BACKUP_ROOT}/weekly" -maxdepth 1 -type f -name 'schema_*.sql.gz' | wc -l)"
MONTHLY_COUNT="$(find "${BACKUP_ROOT}/monthly" -maxdepth 1 -type f -name 'schema_*.sql.gz' | wc -l)"
TOTAL_BYTES="$(du -sb "${BACKUP_ROOT}" | cut -f1)"

log "backup OK: daily=${DAILY_COUNT} weekly=${WEEKLY_COUNT} monthly=${MONTHLY_COUNT} total=${TOTAL_BYTES}B"
