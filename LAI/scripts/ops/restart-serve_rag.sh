#!/usr/bin/env bash
# =============================================================================
# Restart serve_rag (the :18000 chat backend) — safely.
#
# Why this exists: a naive kill + relaunch races the dying process for GPU
# memory and crashes the new one with CUDA OOM (the old serve_rag frees its
# port in ~2s but holds tens of GB of VRAM for longer). This script waits for
# the GPU to actually release before relaunching.
#
# It:
#   1. gracefully stops the running serve_rag (SIGTERM, then SIGKILL fallback)
#   2. waits for :18000 to free AND GPU memory to be released (OOM avoidance)
#   3. relaunches via scripts/ops/start.sh — which sources .env.auth and sets
#      the 27B-remote / LAN-bind / CUDA env (so config lives in ONE place)
#   4. waits for /health to return 200 and reports
#
# Leaves untouched: Docker services, the Vite UI, and the long-running
# pipeline (step6) / corpus-migration jobs.
#
# Usage:
#   bash scripts/ops/restart-serve_rag.sh
#
# Tunables (env overrides):
#   SERVE_RAG_PORT (18000) · SERVE_RAG_GPU (1) · MIN_FREE_MIB (20000)
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAI_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$LAI_DIR"

PORT="${SERVE_RAG_PORT:-18000}"
GPU="${SERVE_RAG_GPU:-1}"               # physical GPU serve_rag uses (CUDA_VISIBLE_DEVICES=1)
MIN_FREE_MIB="${MIN_FREE_MIB:-20000}"   # reranker needs ~16 GB; require headroom before relaunch
LOG="$LAI_DIR/logs/tmp/serve_rag.log"

log() { echo "[restart-serve_rag] $*"; }

serve_rag_pid() {
    # Match the python process running `-m lai.api.serve_rag --port <PORT>`.
    ps -eo pid=,comm=,args= \
        | awk -v p="--port $PORT" '$2 ~ /^python/ && /lai\.api\.serve_rag/ && index($0, p) {print $1; exit}'
}

gpu_free_mib() {  # free MiB on physical GPU $GPU
    nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits 2>/dev/null \
        | awk -F', *' -v g="$GPU" '$1==g {print $2; exit}'
}

port_listening() { ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; }

# ── 1. stop the running instance (if any) ────────────────────────────────
pid="$(serve_rag_pid)"
if [ -n "$pid" ]; then
    log "stopping serve_rag (PID $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done   # up to 60s
    if kill -0 "$pid" 2>/dev/null; then
        log "  still alive after 60s — SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
        sleep 2
    fi
    log "  stopped"
else
    log "serve_rag not currently running — will just start it"
fi

# ── 2. wait for port + GPU memory to free (the OOM-avoidance step) ───────
log "waiting for :$PORT to free and GPU $GPU to release >= ${MIN_FREE_MIB} MiB..."
ready=0
for i in $(seq 1 45); do   # up to 90s
    free="$(gpu_free_mib)"; [ -z "$free" ] && free=0
    if ! port_listening && [ "$free" -ge "$MIN_FREE_MIB" ]; then
        log "  ready after $((i*2))s (GPU $GPU free=${free} MiB)"
        ready=1
        break
    fi
    sleep 2
done
[ "$ready" -ne 1 ] && log "  WARNING: GPU $GPU free=${free:-?} MiB (< ${MIN_FREE_MIB}) or port busy after 90s — launching anyway"

# ── 3. relaunch via start.sh (single source of serve_rag env) ────────────
# start.sh sources .env.auth, sets LLM_API_URL/LLM_MODEL/LAI_BIND_HOST/
# CUDA_VISIBLE_DEVICES, skips Vite/Docker if already up, and launches serve_rag.
log "relaunching via scripts/ops/start.sh ..."
bash "$SCRIPT_DIR/start.sh" >/dev/null 2>&1 || true

# ── 4. wait for health ───────────────────────────────────────────────────
log "waiting for /health (reranker + retrieval wiring can take ~30-60s)..."
for i in $(seq 1 90); do   # up to ~3 min
    code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://localhost:$PORT/health" 2>/dev/null || echo 000)"
    if [ "$code" = "200" ]; then
        log "  ✓ serve_rag healthy — HTTP 200 on :$PORT (PID $(serve_rag_pid))"
        exit 0
    fi
    sleep 2
done

log "  ✗ not healthy after ~3 min. Last log lines:"
tail -15 "$LOG" 2>/dev/null | sed 's/^/    /'
exit 1
