#!/usr/bin/env bash
# =============================================================================
# Resume Step 5 (synthetic training data generation) — uses container vLLM
#
# What it does:
#   1. Ensures the lai-teacher-llm-gpu0 Docker container is running
#   2. Waits until the vLLM endpoint (http://localhost:8005) is ready
#   3. Starts Step 5 in --local mode (reads/writes pipeline_local.db SQLite)
#   4. Exits — both container and Step 5 continue in the background
#
# Pipeline data stays in SQLite — completely independent of PostgreSQL.
# Only the LLM container is used; no other Docker dependency.
#
# Usage:
#   ./scripts/resume_step5.sh          # start (or resume) vLLM + Step 5
#   ./scripts/resume_step5.sh --status # show current status
#   ./scripts/resume_step5.sh --stop   # stop Step 5 (container keeps running)
#   ./scripts/resume_step5.sh --stop-all  # stop Step 5 AND the container
# =============================================================================

set -u

# ---- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAI_DIR="$(dirname "$SCRIPT_DIR")"
cd "$LAI_DIR"

VENV_PY="$LAI_DIR/.venv/bin/python"
PIPELINE_LOG_DIR="$LAI_DIR/logs/pipeline"
SQLITE_DB="$LAI_DIR/processed/pipeline_local.db"

# ---- container config -------------------------------------------------------
# synth-generator runs Qwen2.5-72B-AWQ on BOTH GPUs (tensor-parallel-size 2)
# Older fallback: lai-teacher-llm-gpu0 (1 GPU only)
CONTAINER_NAME="lai_synth_generator"
COMPOSE_FILE="/data/projects/lai/Docker/synth-generator/docker-compose.yml"
COMPOSE_DIR="$(dirname "$COMPOSE_FILE")"
LLM_URL="http://localhost:8005"

mkdir -p "$PIPELINE_LOG_DIR"

# ---- helpers ----------------------------------------------------------------
log()  { echo "[$(date '+%F %T')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

is_llm_up() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$LLM_URL/v1/models" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"
}

# Find any container publishing port 8005 (name may vary: lai-teacher-llm-gpu0,
# lai_synth_generator, etc.)
container_on_port_8005() {
    docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
        | awk -F'\t' '$2 ~ /:8005->/ {print $1; exit}'
}

step5_pid() { pgrep -f "lai.pipeline.cli step5" | head -1; }

sample_count() {
    [ -f "$SQLITE_DB" ] || { echo "0"; return; }
    "$VENV_PY" -c "import sqlite3; print(sqlite3.connect('$SQLITE_DB').execute('SELECT COUNT(*) FROM training_samples').fetchone()[0])" 2>/dev/null || echo "?"
}

status() {
    local active_container
    active_container=$(container_on_port_8005)
    echo "================ STATUS ================"
    echo "Container on port 8005: ${active_container:-none}"
    if [ -n "$active_container" ] && [ "$active_container" != "$CONTAINER_NAME" ]; then
        echo "  (note: expected '$CONTAINER_NAME', found '$active_container' — OK, same endpoint)"
    fi
    echo "vLLM endpoint $LLM_URL: $(is_llm_up && echo 'READY (200)' || echo 'down')"
    local pid
    pid=$(step5_pid)
    echo "Step 5 process: ${pid:-not running}"
    echo "SQLite samples: $(sample_count) / 200000"
    echo
    echo "GPU usage:"
    nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv 2>/dev/null
    echo "========================================"
}

stop_step5() {
    local p
    p=$(step5_pid)
    if [ -n "$p" ]; then
        log "Stopping Step 5 (PID $p)..."
        kill "$p" 2>/dev/null
        sleep 2
        if kill -0 "$p" 2>/dev/null; then
            log "  still alive, sending SIGKILL"
            kill -9 "$p" 2>/dev/null
        fi
        log "  stopped"
    else
        log "Step 5 is not running"
    fi
}

stop_container() {
    if container_running; then
        log "Stopping container '$CONTAINER_NAME'..."
        (cd "$COMPOSE_DIR" && docker compose -f "$(basename "$COMPOSE_FILE")" down) \
            || fail "Failed to stop container"
        log "  stopped"
    else
        log "Container is not running"
    fi
}

start_container() {
    # If anything is already serving port 8005, we're good — don't start another
    local active
    active=$(container_on_port_8005)
    if [ -n "$active" ]; then
        log "Container '$active' already serving port 8005 — reusing it"
        return 0
    fi
    log "No container on port 8005, starting '$CONTAINER_NAME' via docker compose..."
    (cd "$COMPOSE_DIR" && docker compose -f "$(basename "$COMPOSE_FILE")" up -d) \
        || fail "Failed to start container (check $COMPOSE_FILE)"
    log "  container started"
}

wait_for_llm() {
    log "Waiting for vLLM endpoint $LLM_URL to become ready..."
    for i in $(seq 1 60); do
        if is_llm_up; then
            log "vLLM is ready"
            return 0
        fi
        # If container crashed, fail early
        if ! container_running; then
            fail "Container '$CONTAINER_NAME' is not running. Check: docker logs $CONTAINER_NAME"
        fi
        sleep 10
        echo -n "."
    done
    echo
    fail "vLLM did not become ready within 10 minutes. Check: docker logs $CONTAINER_NAME"
}

start_step5() {
    if step5_pid > /dev/null 2>&1 && [ -n "$(step5_pid)" ]; then
        log "Step 5 is already running (PID $(step5_pid))"
        return 0
    fi

    local log_file="$PIPELINE_LOG_DIR/step5_resume_$(date +%F_%H%M%S).log"
    log "Starting Step 5 in --local mode..."
    log "  log file: $log_file"

    nohup "$VENV_PY" -m lai.pipeline.cli step5 --local \
        > "$log_file" 2>&1 &
    disown
    local pid=$!
    log "  Step 5 PID: $pid"

    sleep 5
    if ! kill -0 "$pid" 2>/dev/null; then
        log "Step 5 died immediately. Last lines of log:"
        tail -20 "$log_file" | sed 's/^/    /'
        fail "Step 5 failed to start"
    fi
    log "  Step 5 is running"
    echo
    log "Tail the log with:  tail -f $log_file"
}

# ---- parse args -------------------------------------------------------------
ACTION="start"
while [ $# -gt 0 ]; do
    case "$1" in
        --status)    ACTION="status"; shift ;;
        --stop)      ACTION="stop"; shift ;;
        --stop-all)  ACTION="stop_all"; shift ;;
        -h|--help)
            head -22 "$0" | tail -20
            exit 0
            ;;
        *) fail "Unknown arg: $1. Use --help." ;;
    esac
done

# ---- main -------------------------------------------------------------------
case "$ACTION" in
    status)
        status; exit 0 ;;
    stop)
        stop_step5
        status
        exit 0 ;;
    stop_all)
        stop_step5
        stop_container
        status
        exit 0 ;;
esac

log "================ Resume Step 5 (container vLLM) ================"
start_container
wait_for_llm
start_step5
echo
log "Done. You can close this terminal."
log "Status:    $0 --status"
log "Stop Step 5 only (keep LLM up):  $0 --stop"
log "Stop everything:  $0 --stop-all"
echo
status
