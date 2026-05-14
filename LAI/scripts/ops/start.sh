#!/usr/bin/env bash
# Start the full LAI runtime: Docker services (analyzer LLM, embedding,
# reranker, pgvector, redis) + host processes (serve_rag.py, Vite UI).
#
# Designed to be idempotent — re-running won't double-start anything.
# Each Docker service is its own container under Docker/<svc>/ for solo
# tinkering; this script is just the "all up" entry point.
#
# Usage:
#   bash scripts/ops/start.sh                # default: VPN-trusted bindings
#   LAI_BIND_HOST=127.0.0.1 bash scripts/ops/start.sh   # loopback-only mode
#
# The Vite dev server's --host flag is decided by VPN_MODE (default 1).
# Set VPN_MODE=0 to bind Vite to localhost only (matches loopback mode).
set -euo pipefail

# ── paths ────────────────────────────────────────────────────────────────
# scripts/ops/start.sh → ../.. is the LAI/ project root.
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Frontend lives in its own repo (LAI-UI) as of v1.0.0. By convention
# it's cloned next to LAI/ at /data/projects/lai/LAI-UI/. Override
# LAI_UI_DIR if you keep it elsewhere.
LAI_UI_DIR="${LAI_UI_DIR:-$(cd "$LAI_DIR/.." && pwd)/LAI-UI}"
LOG_DIR="$LAI_DIR/logs/tmp"
mkdir -p "$LOG_DIR"

# ── env defaults (VPN-trusted; override on the command line) ────────────
export LAI_BIND_HOST="${LAI_BIND_HOST:-0.0.0.0}"
export ANALYZER_BIND_HOST="${ANALYZER_BIND_HOST:-0.0.0.0}"
export ANALYZER_VERSION_DEFAULT="${ANALYZER_VERSION_DEFAULT:-2}"
export LLM_API_URL="${LLM_API_URL:-http://localhost:8005}"
export LLM_MODEL="${LLM_MODEL:-qwen3.6-27b}"
export ANALYZER_LLM_API_URL="${ANALYZER_LLM_API_URL:-http://localhost:8005}"
export ANALYZER_LLM_MODEL="${ANALYZER_LLM_MODEL:-qwen3.6-27b}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
VPN_MODE="${VPN_MODE:-1}"

# ── Docker network sanity ────────────────────────────────────────────────
docker network inspect lai_network >/dev/null 2>&1 || {
    echo "[start] creating Docker network lai_network"
    docker network create lai_network
}

# ── Docker services ──────────────────────────────────────────────────────
echo "[start] bringing up Docker services (analyzer, embedding, reranker, pgvector, redis)..."
cd "$LAI_DIR"
ANALYZER_BIND_HOST="$ANALYZER_BIND_HOST" docker compose up -d

# Helpers — filter on executable name (comm) so bash wrappers' argv
# don't get matched as if they were the actual processes.
serve_rag_pid() {
    ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && /lai\.api\.serve_rag/ && /--port 18000/ {print $1; exit}'
}
vite_pid() {
    ps -eo pid=,comm=,args= \
        | awk '$2 == "node" && /LAI-UI\/.*\.bin\/vite/ {print $1; exit}'
}

# ── host process: serve_rag.py ──────────────────────────────────────────
if [ -n "$(serve_rag_pid)" ]; then
    echo "[start] serve_rag already running (PID $(serve_rag_pid)) — leaving it"
else
    echo "[start] launching serve_rag on :18000 (logs → $LOG_DIR/serve_rag.log)"
    cd "$LAI_DIR"
    nohup .venv/bin/python -m lai.api.serve_rag --port 18000 \
        > "$LOG_DIR/serve_rag.log" 2>&1 &
    echo "[start]   PID $!"
fi

# ── host process: Vite UI ───────────────────────────────────────────────
if [ -n "$(vite_pid)" ]; then
    echo "[start] Vite already running (PID $(vite_pid)) — leaving it"
else
    if [ "$VPN_MODE" = "1" ]; then
        VITE_HOST_FLAG="--host 0.0.0.0"
    else
        VITE_HOST_FLAG=""
    fi
    if [ ! -d "$LAI_UI_DIR" ]; then
        echo "[start] ERROR: LAI-UI not found at $LAI_UI_DIR"
        echo "[start]   clone it next to LAI:  git clone git@github.com:Ravijangid820/LAI-UI.git $LAI_UI_DIR"
        echo "[start]   or set LAI_UI_DIR=/path/to/LAI-UI"
        exit 1
    fi
    if [ ! -d "$LAI_UI_DIR/node_modules" ]; then
        echo "[start] installing LAI-UI npm dependencies (one-time)..."
        (cd "$LAI_UI_DIR" && npm install) > "$LOG_DIR/npm-install.log" 2>&1 \
            || { echo "[start] npm install failed — see $LOG_DIR/npm-install.log"; exit 1; }
    fi
    echo "[start] launching Vite UI on :5173 (logs → $LOG_DIR/vite.log)"
    cd "$LAI_UI_DIR"
    nohup npm run dev -- $VITE_HOST_FLAG \
        > "$LOG_DIR/vite.log" 2>&1 &
    echo "[start]   PID $!"
fi

echo
echo "[start] done. Watch logs:"
echo "    tail -f $LOG_DIR/serve_rag.log"
echo "    tail -f $LOG_DIR/vite.log"
echo "    docker compose -f $LAI_DIR/docker-compose.yml logs -f analyzer-llm"
echo
echo "[start] backends take ~3-5 min to be ready (model loads + embeddings into RAM)."
echo "[start] check status with:  bash scripts/ops/status.sh"
