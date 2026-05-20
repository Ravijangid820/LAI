#!/usr/bin/env bash
# =============================================================================
# Restart serve_rag (the chat / RAG backend on :18000) — S-5
#
# Why this script exists:
#   serve_rag loads a heavy process state at boot (reranker on GPU, LLM
#   warmup, pgvector pool, auth pool). After a code change — most notably
#   the Track-B pgvector swap (S-1) — the running process is stale and the
#   new behaviour is invisible until a clean restart. This wrapper makes
#   that restart ONE coordinated, SSH-disconnect-proof event with a
#   readiness gate, instead of an ad-hoc "kill and hope".
#
# What it does:
#   1. Finds the current serve_rag PID (executable-name match, not argv).
#   2. Sends SIGTERM and waits for a graceful exit (lifespan shutdown
#      closes the auth + retrieval pools). Escalates to SIGKILL if it
#      overstays.
#   3. Relaunches detached (setsid + nohup + disown) so it survives this
#      shell closing — same discipline as resume_step6.sh / resume_migration.sh.
#   4. Polls GET /health until the new process reports ready (retrieval
#      backend reachable + LLM loaded), or times out loudly.
#
# Usage:
#   ./scripts/ops/restart_serve_rag.sh             # restart + wait for ready
#   ./scripts/ops/restart_serve_rag.sh --status    # show PID + /health
#   ./scripts/ops/restart_serve_rag.sh --stop      # stop only (no relaunch)
#   ./scripts/ops/restart_serve_rag.sh --no-wait   # restart, skip the health gate
#
# Env overrides:
#   LAI_VENV_PY      python interpreter (default: $LAI_DIR/.venv/bin/python)
#   SERVE_RAG_PORT   port (default: 18000)
#   HEALTH_TIMEOUT_S readiness wait budget in seconds (default: 600 —
#                    serve_rag's cold start is ~5 min: reranker + LLM warmup)
# =============================================================================

set -u

# ---- file-creation mode (mirror resume_step6.sh) ----------------------------
# Keeps any log/WAL files serve_rag may touch group-writable so a teammate
# in the `lai` group can manage the process regardless of who started it.
umask 002

# ---- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAI_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$LAI_DIR"

VENV_PY="${LAI_VENV_PY:-$LAI_DIR/.venv/bin/python}"
SERVE_RAG_PORT="${SERVE_RAG_PORT:-18000}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-600}"
LOG_DIR="$LAI_DIR/logs/host"
LOG_FILE="$LOG_DIR/serve_rag.log"
HEALTH_URL="http://localhost:${SERVE_RAG_PORT}/health"

mkdir -p "$LOG_DIR"

# ---- helpers ----------------------------------------------------------------
log()  { echo "[$(date '+%F %T')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

# Match on executable name + argv the same way start.sh does, so a bash
# wrapper's argv ("restart_serve_rag.sh") is never mistaken for the python
# process itself.
serve_rag_pid() {
    ps -eo pid=,comm=,args= \
        | awk -v port="--port ${SERVE_RAG_PORT}" \
            '$2 ~ /^python/ && /lai\.api\.serve_rag/ && index($0, port) {print $1; exit}'
}

health_json() {
    curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null || true
}

# ---- stop -------------------------------------------------------------------
stop_serve_rag() {
    local pid
    pid="$(serve_rag_pid)"
    if [ -z "$pid" ]; then
        log "serve_rag not running — nothing to stop"
        return 0
    fi
    log "stopping serve_rag (PID $pid) with SIGTERM..."
    kill -TERM "$pid" 2>/dev/null || true

    # Wait up to 30s for a graceful exit (lifespan shutdown closes pools).
    local waited=0
    while [ "$waited" -lt 30 ]; do
        if [ -z "$(serve_rag_pid)" ]; then
            log "serve_rag stopped cleanly after ${waited}s"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    pid="$(serve_rag_pid)"
    if [ -n "$pid" ]; then
        log "serve_rag still alive after 30s — escalating to SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 2
    fi
    [ -z "$(serve_rag_pid)" ] || fail "could not stop serve_rag (PID still $(serve_rag_pid))"
    log "serve_rag killed"
}

# ---- start ------------------------------------------------------------------
start_serve_rag() {
    if [ -n "$(serve_rag_pid)" ]; then
        fail "serve_rag already running (PID $(serve_rag_pid)) — stop it first"
    fi
    [ -x "$VENV_PY" ] || fail "venv python not found/executable: $VENV_PY (set LAI_VENV_PY)"

    # Source the same env start.sh uses so a standalone restart still has
    # DB_*, CORS_ORIGINS, and the auth JWT secret.
    if [ -f "$LAI_DIR/.env.auth" ]; then
        set -a
        # shellcheck disable=SC1091
        . "$LAI_DIR/.env.auth"
        set +a
    fi
    export DB_HOST="${DB_HOST:-127.0.0.1}"
    export DB_PORT="${DB_PORT:-5434}"
    export DB_NAME="${DB_NAME:-lai_db}"
    export DB_USER="${DB_USER:-lai_user}"
    export DB_PASSWORD="${DB_PASSWORD:-lai_test_password_2024}"
    export CORS_ORIGINS="${CORS_ORIGINS:-http://192.168.178.82:5173,http://localhost:5173,http://localhost:3000}"

    log "launching serve_rag on :${SERVE_RAG_PORT} (logs → $LOG_FILE)"
    # setsid + nohup + disown: triple-belt SSH-survival, same as the
    # migration / step6 wrappers. </dev/null detaches stdin.
    setsid nohup "$VENV_PY" -m lai.api.serve_rag --port "$SERVE_RAG_PORT" \
        </dev/null >>"$LOG_FILE" 2>&1 &
    disown
    sleep 2
    local pid
    pid="$(serve_rag_pid)"
    [ -n "$pid" ] || fail "serve_rag failed to launch — check $LOG_FILE"
    log "serve_rag launched (PID $pid); this terminal can close"
}

# ---- readiness gate ---------------------------------------------------------
# Poll /health until it reports the new keys S-1 added: retrieval_ready=true
# AND loaded=true. serve_rag's cold path (reranker + LLM warmup) can take
# minutes, hence the generous default budget.
wait_for_ready() {
    log "waiting for /health to report ready (timeout ${HEALTH_TIMEOUT_S}s)..."
    local waited=0 step=5
    while [ "$waited" -lt "$HEALTH_TIMEOUT_S" ]; do
        local body
        body="$(health_json)"
        if echo "$body" | grep -q '"loaded"[[:space:]]*:[[:space:]]*true' \
           && echo "$body" | grep -q '"retrieval_ready"[[:space:]]*:[[:space:]]*true'; then
            log "READY — $body"
            return 0
        fi
        sleep "$step"
        waited=$((waited + step))
        # Heartbeat every 30s so a long warmup doesn't look hung.
        if [ $((waited % 30)) -eq 0 ]; then
            log "  still warming up (${waited}s)… last /health: ${body:-<no response>}"
        fi
    done
    fail "serve_rag did not become ready within ${HEALTH_TIMEOUT_S}s — check $LOG_FILE"
}

# ---- status -----------------------------------------------------------------
show_status() {
    local pid
    pid="$(serve_rag_pid)"
    if [ -n "$pid" ]; then
        log "serve_rag running (PID $pid) on :${SERVE_RAG_PORT}"
        log "/health: $(health_json)"
    else
        log "serve_rag NOT running"
    fi
}

# ---- main -------------------------------------------------------------------
case "${1:-}" in
    --status)
        show_status
        ;;
    --stop)
        stop_serve_rag
        ;;
    --no-wait)
        stop_serve_rag
        start_serve_rag
        log "restart issued (--no-wait): skipping health gate"
        show_status
        ;;
    ""|--restart)
        stop_serve_rag
        start_serve_rag
        wait_for_ready
        ;;
    *)
        echo "usage: $0 [--restart | --no-wait | --stop | --status]" >&2
        exit 2
        ;;
esac
