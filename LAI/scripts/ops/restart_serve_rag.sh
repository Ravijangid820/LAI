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
# Also handles the lai-backend Docker container (DDiQ service on :18001) so
# one command rebuilds + restarts the full LAI backend pair. The DDiQ service
# bakes its code into the image (no bind mount), so a source edit is invisible
# to the running container until the image is rebuilt and the container
# recreated. Bitten by this when the PATCH /ddiq/report/{id} rename endpoint
# was added and the running container kept returning 405 — the new method
# existed in source but not in the image. Doing both in one script means
# "did I edit serve_rag.py or ddiq_report.py? do I run one script or two?"
# stops being a recurring footgun.
#
# Usage:
#   ./scripts/ops/restart_serve_rag.sh             # restart BOTH + wait for ready
#   ./scripts/ops/restart_serve_rag.sh --status    # show PID + /health for both
#   ./scripts/ops/restart_serve_rag.sh --stop      # stop serve_rag only
#   ./scripts/ops/restart_serve_rag.sh --no-wait   # restart, skip the health gate
#   ./scripts/ops/restart_serve_rag.sh --skip-ddiq # restart serve_rag ONLY (fast path)
#
# Env overrides:
#   LAI_VENV_PY       python interpreter (default: $LAI_DIR/.venv/bin/python)
#   SERVE_RAG_PORT    port (default: 18000)
#   HEALTH_TIMEOUT_S  readiness wait budget in seconds (default: 600 —
#                     serve_rag's cold start is ~5 min: reranker + LLM warmup)
#   DDIQ_BACKEND_PORT lai-backend host port (default: 18001)
#   SKIP_DDIQ         if set to "1", skip the Docker rebuild even without the flag
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

DDIQ_BACKEND_PORT="${DDIQ_BACKEND_PORT:-18001}"
DDIQ_COMPOSE_DIR="$LAI_DIR/micro-services"
DDIQ_HEALTH_URL="http://localhost:${DDIQ_BACKEND_PORT}/health"
DDIQ_HEALTH_TIMEOUT_S=120

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

# ---- ddiq backend + worker (Docker containers) ------------------------------
# Rebuild BOTH the lai-backend (HTTP :18001) and lai-worker (Celery, no port)
# images with the current source, then force-recreate the containers so the
# new images actually run (compose otherwise reuses existing containers if
# config is unchanged). Brief downtime (~30s build, mostly cached + ~5s
# recreate). No-op if Docker isn't available.
#
# Why BOTH must rebuild: backend and worker share the same Dockerfile +
# build context, but compose tags them separately (micro-services-backend
# vs micro-services-worker), so building one does NOT update the other.
# Without this, fixes touching code the worker runs (e.g. report
# generation, the post-finish email notify in ``_notify_report_complete``,
# anything Celery executes) stay invisible — the worker keeps running
# whatever image was current the last time IT specifically was rebuilt.
# Symptom seen in the wild: "we never received any email" — the email
# function was committed and rebuilt for backend, but worker (which fires
# the email at the end of report generation) was running a stale image
# from before the function existed.
restart_ddiq_backend() {
    if ! command -v docker >/dev/null 2>&1; then
        log "ddiq: docker not on PATH — skipping container restart"
        return 0
    fi
    if ! docker info >/dev/null 2>&1; then
        log "ddiq: docker daemon unreachable — skipping container restart"
        return 0
    fi
    [ -f "$DDIQ_COMPOSE_DIR/docker-compose.yml" ] || {
        log "ddiq: $DDIQ_COMPOSE_DIR/docker-compose.yml not found — skipping"
        return 0
    }
    log "ddiq: rebuilding lai-backend + lai-worker images..."
    ( cd "$DDIQ_COMPOSE_DIR" && docker compose build backend worker ) \
        || { log "ddiq: build FAILED — see output above"; return 1; }
    log "ddiq: recreating lai-backend + lai-worker containers..."
    ( cd "$DDIQ_COMPOSE_DIR" && docker compose up -d --force-recreate backend worker ) \
        || { log "ddiq: recreate FAILED — see output above"; return 1; }
    log "ddiq: waiting for /health on :${DDIQ_BACKEND_PORT} (timeout ${DDIQ_HEALTH_TIMEOUT_S}s)..."
    local waited=0
    while [ "$waited" -lt "$DDIQ_HEALTH_TIMEOUT_S" ]; do
        local code
        code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$DDIQ_HEALTH_URL" 2>/dev/null || echo "000")"
        if [ "$code" = "200" ]; then
            log "ddiq: lai-backend READY"
            # Worker has no /health surface; check it's at least running
            # via ``docker ps``. A failed worker startup would show
            # "Exited" status here rather than "Up …".
            local worker_state
            worker_state="$(docker inspect --format '{{.State.Status}}' lai-worker 2>/dev/null || echo unknown)"
            if [ "$worker_state" = "running" ]; then
                log "ddiq: lai-worker READY (state=running)"
            else
                log "ddiq: WARNING lai-worker state=${worker_state} — inspect: docker logs lai-worker --tail 50"
            fi
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    log "ddiq: timeout — /health never returned 200. Inspect: docker logs lai-backend --tail 50"
    return 1
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
    if command -v docker >/dev/null 2>&1 && docker ps -q --filter "name=lai-backend" | grep -q .; then
        local ddiq_code
        ddiq_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$DDIQ_HEALTH_URL" 2>/dev/null || echo "000")"
        log "ddiq backend: container up on :${DDIQ_BACKEND_PORT}, /health=${ddiq_code}"
    else
        log "ddiq backend: container NOT running"
    fi
}

# ---- main -------------------------------------------------------------------
# ``do_ddiq`` decides whether to also rebuild the lai-backend container after
# serve_rag is up. Default ON — see the header for the rationale. Two ways to
# opt out: --skip-ddiq flag (one-off) or SKIP_DDIQ=1 env (durable).
do_ddiq=1
if [ "${SKIP_DDIQ:-0}" = "1" ]; then do_ddiq=0; fi
mode=""
for arg in "$@"; do
    case "$arg" in
        --skip-ddiq) do_ddiq=0 ;;
        --status|--stop|--no-wait|--restart) mode="$arg" ;;
        "") ;;
        *) echo "usage: $0 [--restart | --no-wait | --stop | --status] [--skip-ddiq]" >&2; exit 2 ;;
    esac
done

case "$mode" in
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
        [ "$do_ddiq" = "1" ] && restart_ddiq_backend || true
        ;;
    ""|--restart)
        stop_serve_rag
        start_serve_rag
        wait_for_ready
        [ "$do_ddiq" = "1" ] && restart_ddiq_backend || true
        ;;
esac
