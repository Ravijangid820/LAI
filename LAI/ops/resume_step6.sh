#!/usr/bin/env bash
# =============================================================================
# Resume Step 6 (embeddings → child_embeddings) — uses the embedding container
#
# What it does:
#   1. Ensures the lai_embedding Docker container is running (Qwen3-Embedding-8B
#      on port 8003)
#   2. Waits until the embedding endpoint is ready
#   3. Starts Step 6 in --local mode (writes to SQLite child_embeddings table)
#   4. Exits — both container and Step 6 continue in the background
#
# Resume is automatic: the SQL filter
#     WHERE NOT EXISTS (SELECT 1 FROM child_embeddings e WHERE e.child_id = c.id)
# skips child chunks that already have embeddings, so re-running picks up
# wherever the previous run left off.
#
# Pipeline data stays in SQLite — completely independent of PostgreSQL.
# Only the embedding container is used; no other Docker dependency.
#
# Usage:
#   ./ops/resume_step6.sh          # start (or resume) embedding + Step 6
#   ./ops/resume_step6.sh --status # show current status
#   ./ops/resume_step6.sh --stop   # stop Step 6 (container keeps running)
#   ./ops/resume_step6.sh --stop-all  # stop Step 6 AND the container
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
# Qwen3-Embedding-8B serves the OpenAI /v1/embeddings shape on port 8003.
CONTAINER_NAME="lai_embedding"
COMPOSE_FILE="/data/projects/lai/Docker/embedding/docker-compose.yml"
COMPOSE_DIR="$(dirname "$COMPOSE_FILE")"
EMBED_URL="http://localhost:8003"

mkdir -p "$PIPELINE_LOG_DIR"

# ---- helpers ----------------------------------------------------------------
log()  { echo "[$(date '+%F %T')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

is_embed_up() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$EMBED_URL/v1/models" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"
}

container_on_port_8003() {
    docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
        | awk -F'\t' '$2 ~ /:8003->/ {print $1; exit}'
}

step6_pid() { pgrep -f "lai.pipeline.cli step6" | head -1; }

embedding_count() {
    [ -f "$SQLITE_DB" ] || { echo "0"; return; }
    "$VENV_PY" -c "import sqlite3; print(sqlite3.connect('$SQLITE_DB').execute('SELECT COUNT(*) FROM child_embeddings').fetchone()[0])" 2>/dev/null || echo "?"
}

child_chunk_count() {
    [ -f "$SQLITE_DB" ] || { echo "0"; return; }
    "$VENV_PY" -c "import sqlite3; print(sqlite3.connect('$SQLITE_DB').execute('SELECT COUNT(*) FROM child_chunks').fetchone()[0])" 2>/dev/null || echo "?"
}

status() {
    local active_container
    active_container=$(container_on_port_8003)
    local total done_count
    total=$(child_chunk_count)
    done_count=$(embedding_count)
    local pct="?"
    if [ "$total" -gt 0 ] 2>/dev/null && [ "$done_count" != "?" ]; then
        pct=$(awk "BEGIN {printf \"%.2f\", ($done_count / $total) * 100}")
    fi
    echo "================ STATUS ================"
    echo "Container on port 8003: ${active_container:-none}"
    if [ -n "$active_container" ] && [ "$active_container" != "$CONTAINER_NAME" ]; then
        echo "  (note: expected '$CONTAINER_NAME', found '$active_container' — OK, same endpoint)"
    fi
    echo "Embedding endpoint $EMBED_URL: $(is_embed_up && echo 'READY (200)' || echo 'down')"
    local pid
    pid=$(step6_pid)
    echo "Step 6 process: ${pid:-not running}"
    echo "Embeddings: ${done_count} / ${total}  (${pct}% done)"
    echo "Remaining: $((total - done_count)) child chunks"
    echo
    echo "GPU usage:"
    nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv 2>/dev/null
    echo "========================================"
}

stop_step6() {
    local p
    p=$(step6_pid)
    if [ -n "$p" ]; then
        log "Stopping Step 6 (PID $p)..."
        kill "$p" 2>/dev/null
        sleep 2
        if kill -0 "$p" 2>/dev/null; then
            log "  still alive, sending SIGKILL"
            kill -9 "$p" 2>/dev/null
        fi
        log "  stopped"
    else
        log "Step 6 not running"
    fi
}

stop_container() {
    if container_running; then
        log "Stopping container '$CONTAINER_NAME'..."
        (cd "$COMPOSE_DIR" && docker compose -f "$(basename "$COMPOSE_FILE")" down) \
            || log "  (compose down returned non-zero, ignoring)"
    else
        log "Container '$CONTAINER_NAME' already stopped"
    fi
}

start_container() {
    local active
    active=$(container_on_port_8003)
    if [ -n "$active" ]; then
        log "Container '$active' already serving port 8003 — reusing it"
        return 0
    fi
    log "No container on port 8003, starting '$CONTAINER_NAME' via docker compose..."
    (cd "$COMPOSE_DIR" && docker compose -f "$(basename "$COMPOSE_FILE")" up -d) \
        || fail "Failed to start container (check $COMPOSE_FILE)"
    log "  container started"
}

wait_for_embed() {
    log "Waiting for embedding endpoint $EMBED_URL to become ready..."
    for i in $(seq 1 60); do
        if is_embed_up; then
            log "Embedding service is ready"
            return 0
        fi
        if ! container_running; then
            fail "Container '$CONTAINER_NAME' is not running. Check: docker logs $CONTAINER_NAME"
        fi
        sleep 10
        echo -n "."
    done
    echo
    fail "Embedding service did not become ready within 10 minutes. Check: docker logs $CONTAINER_NAME"
}

start_step6() {
    if step6_pid > /dev/null 2>&1 && [ -n "$(step6_pid)" ]; then
        log "Step 6 is already running (PID $(step6_pid))"
        return 0
    fi

    local log_file="$PIPELINE_LOG_DIR/step6_resume_$(date +%F_%H%M%S).log"
    log "Starting Step 6 in --local mode..."
    log "  log file: $log_file"
    log "  current progress: $(embedding_count) / $(child_chunk_count) embeddings"

    nohup "$VENV_PY" -m lai.pipeline.cli step6 --local \
        --batch-size 200 --embed-batch-size 32 \
        > "$log_file" 2>&1 &
    disown
    local pid=$!
    log "  Step 6 PID: $pid"

    sleep 5
    if ! kill -0 "$pid" 2>/dev/null; then
        log "Step 6 died immediately. Last lines of log:"
        tail -20 "$log_file" | sed 's/^/    /'
        fail "Step 6 failed to start"
    fi
    log "  Step 6 is running"
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
            head -23 "$0" | tail -21
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
        stop_step6
        status
        exit 0 ;;
    stop_all)
        stop_step6
        stop_container
        status
        exit 0 ;;
esac

log "================ Resume Step 6 (embedding container) ================"
start_container
wait_for_embed
start_step6
echo
log "Done. You can close this terminal."
log "Status:    $0 --status"
log "Stop Step 6 only (keep embedding up):  $0 --stop"
log "Stop everything:  $0 --stop-all"
echo
status
