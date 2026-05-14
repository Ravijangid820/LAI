#!/usr/bin/env bash
# =============================================================================
# Tear down the no-Docker LAI stack started by scripts/ops/start-host.sh.
#
# Stops services in reverse dependency order (app layer first, then the
# vLLM model servers) using the pidfiles under logs/host/. Each service is
# SIGTERM'd, given a moment to exit, then SIGKILL'd if still alive; child
# processes (uvicorn workers, npm→node) are swept too.
#
# Usage:
#   bash scripts/ops/stop-host.sh                # stop everything
#   bash scripts/ops/stop-host.sh --keep-models  # stop app layer only, leave
#                                                # vLLM up (they take min to reload)
#   bash scripts/ops/stop-host.sh --models-only  # stop ONLY the 3 vLLM servers
# =============================================================================
set -uo pipefail

# scripts/ops/stop-host.sh → ../.. is the LAI/ project root.
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$LAI_DIR/logs/host"

KEEP_MODELS=0
MODELS_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --keep-models) KEEP_MODELS=1 ;;
        --models-only) MODELS_ONLY=1 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "[stop-host] unknown arg: $arg (see --help)"; exit 1 ;;
    esac
done

log() { echo "[stop-host] $*"; }

# stop the user-local PostgreSQL cluster (pg_ctl manages its own pidfile)
stop_local_pg() {
    local pg_data="${LAI_PG_DATA:-$LAI_DIR/data/pg-host}"
    local pg_bin
    pg_bin="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
    if [ -n "$pg_bin" ] && "$pg_bin/pg_ctl" -D "$pg_data" status >/dev/null 2>&1; then
        log "postgres — stopping user-local cluster"
        if "$pg_bin/pg_ctl" -D "$pg_data" -m fast -w stop >/dev/null 2>&1; then
            log "  ✓ postgres stopped"
        else
            log "  ⚠ pg_ctl stop returned non-zero"
        fi
    else
        log "postgres — user-local cluster not running"
    fi
}

# stop_svc NAME — kill the process recorded in logs/host/NAME.pid, plus any
# children, TERM then KILL.
stop_svc() {
    local name="$1" pidf="$LOG_DIR/$name.pid"
    if [ ! -f "$pidf" ]; then
        log "$name — no pidfile, not running"
        return 0
    fi
    local pid
    pid="$(cat "$pidf" 2>/dev/null)"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        log "$name — stale pidfile (PID ${pid:-?} gone)"
        rm -f "$pidf"
        return 0
    fi
    log "$name — stopping PID $pid"
    # children first (uvicorn workers, npm→node), then the parent
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        log "  still alive — SIGKILL"
        pkill -KILL -P "$pid" 2>/dev/null || true
        kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$pidf"
    log "  ✓ $name stopped"
}

APP_SVCS="vite ddiq serve_rag"
MODEL_SVCS="reranker embedding analyzer"

if [ "$MODELS_ONLY" = "1" ]; then
    for s in $MODEL_SVCS; do stop_svc "$s"; done
    log "done (models only)."
    exit 0
fi

# app layer always stops first (DDiQ before its PostgreSQL)
for s in $APP_SVCS; do stop_svc "$s"; done
stop_local_pg

if [ "$KEEP_MODELS" = "1" ]; then
    log "done. vLLM model servers left running (--keep-models)."
else
    for s in $MODEL_SVCS; do stop_svc "$s"; done
    log "done. full stack stopped."
fi
