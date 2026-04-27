#!/usr/bin/env bash
# Start the full LAI runtime: Docker services (analyzer LLM, embedding,
# reranker, pgvector, redis) + host processes (serve_rag.py, Vite UI).
#
# Designed to be idempotent — re-running won't double-start anything.
# Each Docker service is its own container under Docker/<svc>/ for solo
# tinkering; this script is just the "all up" entry point.
#
# Usage:
#   bash scripts/start.sh                # default: VPN-trusted bindings
#   LAI_BIND_HOST=127.0.0.1 bash scripts/start.sh   # loopback-only mode
#
# The Vite dev server's --host flag is decided by VPN_MODE (default 1).
# Set VPN_MODE=0 to bind Vite to localhost only (matches loopback mode).
set -euo pipefail

# ── paths ────────────────────────────────────────────────────────────────
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# ── host process: serve_rag.py ──────────────────────────────────────────
if pgrep -af "scripts/serve_rag.py --port 18000" >/dev/null; then
    echo "[start] serve_rag already running — leaving it"
else
    echo "[start] launching serve_rag.py on :18000 (logs → $LOG_DIR/serve_rag.log)"
    cd "$LAI_DIR"
    nohup .venv/bin/python scripts/serve_rag.py --port 18000 \
        > "$LOG_DIR/serve_rag.log" 2>&1 &
    echo "[start]   PID $!"
fi

# ── host process: Vite UI ───────────────────────────────────────────────
if pgrep -af "web_ui/LAI.*vite" >/dev/null; then
    echo "[start] Vite already running — leaving it"
else
    if [ "$VPN_MODE" = "1" ]; then
        VITE_HOST_FLAG="--host 0.0.0.0"
    else
        VITE_HOST_FLAG=""
    fi
    echo "[start] launching Vite UI on :5173 (logs → $LOG_DIR/vite.log)"
    cd "$LAI_DIR/web_ui/LAI"
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
echo "[start] check status with:  bash scripts/status.sh"
