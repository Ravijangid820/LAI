#!/usr/bin/env bash
# =============================================================================
# Corpus pgvector migration — SSH-disconnect-proof launcher
# =============================================================================
# Wraps ``migrate_corpus.py`` so it survives an SSH disconnect, the same way
# ``resume_step6.sh`` keeps Step 6 alive. Mirrors that script's umask + nohup
# + disown discipline so file ownership inside ``LAI/logs/migration/`` plays
# nicely with the ``group:lai:rwx`` ACL.
#
# Subcommands:
#   ./resume_migration.sh init             — create schema (one-shot, fast)
#   ./resume_migration.sh migrate-parents  — copy parent_chunks (one-shot, ~20 min)
#   ./resume_migration.sh migrate-children — bulk-copy embedded children (resumable)
#   ./resume_migration.sh build-index      — CREATE INDEX CONCURRENTLY (long, background)
#   ./resume_migration.sh topup            — daemon: stream new rows from Step 6
#   ./resume_migration.sh status           — print progress + state (foreground)
#   ./resume_migration.sh stop             — kill background workers
#   ./resume_migration.sh stop topup       — kill the topup daemon only
#   ./resume_migration.sh start-all        — chain parents → children → build-index → topup
#                                            (one detached process; survives SSH drop)
#   ./resume_migration.sh logs             — tail -f the most recent log
#
# Environment (forwarded to the Python script):
#   DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
#       — Postgres reach. DB_PASSWORD is required; the script exits 2 if unset.
#         Sahid's setup uses lai-backend's compose env (DB_PASSWORD=…).
#   LAI_MIGRATION_BATCH_SIZE         — default 2000
#   LAI_MIGRATION_TOPUP_INTERVAL_S   — default 30
#   LAI_MIGRATION_LOG_LEVEL          — default INFO (DEBUG / INFO / WARNING)
# =============================================================================

set -u

# ---- file-creation mode ------------------------------------------------------
# Same rationale as resume_step6.sh: the logs/ dir lives under LAI/ which has a
# group:lai:rwx default ACL, but umask 022 clips the ACL mask to r-- on new
# files. umask 002 keeps new files group-writable so any user in the `lai`
# group can rotate / inspect the migration logs.
umask 002

# ---- paths -------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAI_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$LAI_DIR"

PY_SCRIPT="$LAI_DIR/scripts/ops/migrate_corpus.py"
# Project venv default; override via LAI_VENV_PY when running as a user who
# can't execute the project venv (e.g. it's owned by a different teammate).
VENV_PY="${LAI_VENV_PY:-$LAI_DIR/.venv/bin/python}"
LOG_DIR="$LAI_DIR/logs/migration"
mkdir -p "$LOG_DIR"

# ---- helpers ----------------------------------------------------------------
log()  { echo "[$(date '+%F %T')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

# Bracket-trick: prevents pgrep from self-matching this script's command line.
# Same defensive shape as resume_step6.sh step6_pid().
worker_pids() {
    pgrep -af '[m]igrate_corpus\.py' || true
}
topup_pid() {
    pgrep -af '[m]igrate_corpus\.py topup' | awk '{print $1}' | head -1
}
migrate_pid() {
    # Any worker that's NOT the topup daemon (so: init / migrate-* /
    # build-index that's still running).
    pgrep -af '[m]igrate_corpus\.py' | grep -v ' topup\b' | awk '{print $1}'
}

ensure_python() {
    if [ ! -x "$VENV_PY" ]; then
        fail "venv python not found at $VENV_PY — activate the lai venv first"
    fi
    if [ ! -f "$PY_SCRIPT" ]; then
        fail "migrate_corpus.py not found at $PY_SCRIPT"
    fi
    if [ -z "${DB_PASSWORD:-}" ]; then
        fail "DB_PASSWORD not set. Export the same value lai-backend uses."
    fi
}

start_detached() {
    # $1 = subcommand (e.g. "migrate-children"); rest = extra log-name suffix
    local subcmd="$1"
    local log_file="$LOG_DIR/${subcmd}_$(date +%F_%H%M%S).log"
    # Symlink "latest" → this log so `logs` works without globbing
    ln -sfn "$log_file" "$LOG_DIR/${subcmd}.latest.log"

    log "starting '$subcmd' in the background"
    log "  log: $log_file"
    log "  (this terminal can close; nohup+disown keep it running)"

    # nohup + setsid + disown: triple-belt-and-suspenders SSH-survival.
    # </dev/null detaches stdin; >$log_file 2>&1 redirects everything else.
    setsid nohup "$VENV_PY" "$PY_SCRIPT" "$subcmd" \
        > "$log_file" 2>&1 </dev/null &
    local pid=$!
    disown
    sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        log "process $pid exited within 2s — check the log:"
        tail -20 "$log_file"
        exit 1
    fi
    log "running as PID $pid"
}

run_foreground() {
    # For init / status — short-lived, output back to operator.
    local subcmd="$1"
    "$VENV_PY" "$PY_SCRIPT" "$subcmd"
}

stop_pids() {
    local label="$1"; shift
    if [ "$#" -eq 0 ]; then
        log "no $label process to stop"
        return 0
    fi
    log "stopping $label PIDs: $*"
    kill "$@" 2>/dev/null || true
    sleep 3
    # SIGKILL the survivors
    for p in "$@"; do
        if kill -0 "$p" 2>/dev/null; then
            log "  PID $p still alive after SIGTERM; sending SIGKILL"
            kill -9 "$p" 2>/dev/null || true
        fi
    done
}

tail_latest() {
    local latest
    latest=$(ls -t "$LOG_DIR"/*.latest.log 2>/dev/null | head -1)
    if [ -z "$latest" ]; then
        # No symlink yet; fall back to newest .log file
        latest=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -1)
    fi
    if [ -z "$latest" ]; then
        fail "no migration logs in $LOG_DIR"
    fi
    log "tailing $latest  (Ctrl-C to stop tailing; doesn't affect the worker)"
    tail -f "$latest"
}

usage() {
    head -34 "$0" | tail -33
}

# ---- main -------------------------------------------------------------------

ACTION="${1:-help}"
shift || true

case "$ACTION" in
    init|status)
        ensure_python
        run_foreground "$ACTION"
        ;;
    migrate-parents|migrate-children|build-index|topup)
        ensure_python
        start_detached "$ACTION"
        ;;
    start-all)
        ensure_python
        log_file="$LOG_DIR/start_all_$(date +%F_%H%M%S).log"
        ln -sfn "$log_file" "$LOG_DIR/start_all.latest.log"
        log "starting full migration chain (parents → children → build-index → topup)"
        log "  log: $log_file"
        log "  (one detached process; survives SSH drop. Topup runs forever at the end.)"
        # Detach the orchestration loop itself: setsid + nohup + </dev/null
        # ensure the chain keeps going after this terminal closes. Inside,
        # each step runs in the foreground of the detached shell, so each
        # step's exit code can gate the next.
        setsid nohup bash -c '
            set -u
            VENV_PY="'"$VENV_PY"'"
            PY_SCRIPT="'"$PY_SCRIPT"'"
            ts() { date "+%F %T"; }
            echo "[$(ts)] === migration chain starting ==="
            echo "[$(ts)] step 1/4: migrate-parents"
            "$VENV_PY" "$PY_SCRIPT" migrate-parents || {
                echo "[$(ts)] FATAL: migrate-parents failed; chain aborted"
                exit 1
            }
            echo "[$(ts)] step 2/4: migrate-children"
            "$VENV_PY" "$PY_SCRIPT" migrate-children || {
                echo "[$(ts)] FATAL: migrate-children failed; chain aborted"
                exit 2
            }
            echo "[$(ts)] step 3/4: build-index (CREATE INDEX CONCURRENTLY; long-running)"
            "$VENV_PY" "$PY_SCRIPT" build-index || {
                echo "[$(ts)] WARN: build-index failed; continuing to topup anyway"
                echo "[$(ts)]       (retry it manually: ./resume_migration.sh build-index)"
            }
            echo "[$(ts)] step 4/4: topup daemon (runs forever — Ctrl-C / SIGTERM to stop)"
            exec "$VENV_PY" "$PY_SCRIPT" topup
        ' > "$log_file" 2>&1 </dev/null &
        disown
        sleep 2
        log "started; PID tree:"
        pgrep -af '[m]igrate_corpus\.py\|start_all\.sh' | head -3 || true
        log "tail it with:  $0 logs"
        ;;
    stop)
        TARGET="${1:-all}"
        case "$TARGET" in
            topup)
                # shellcheck disable=SC2046
                stop_pids "topup" $(topup_pid)
                ;;
            all|"")
                # shellcheck disable=SC2046
                stop_pids "migration workers" $(worker_pids | awk '{print $1}')
                ;;
            *)
                fail "unknown stop target: $TARGET (use: all | topup)"
                ;;
        esac
        ;;
    logs|tail)
        tail_latest
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        echo "unknown subcommand: $ACTION"
        echo
        usage
        exit 1
        ;;
esac
