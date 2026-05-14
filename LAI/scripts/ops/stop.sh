#!/usr/bin/env bash
# Tear down the full LAI runtime — host processes first (so they don't
# error on closing connections), then Docker services.
#
# Usage:
#   bash scripts/ops/stop.sh             # full stop
#   bash scripts/ops/stop.sh --keep-docker   # only stop host processes
#   bash scripts/ops/stop.sh --keep-host     # only stop Docker services
set -euo pipefail

# scripts/ops/stop.sh → ../.. is the LAI/ project root.
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

KEEP_DOCKER=0
KEEP_HOST=0
for arg in "$@"; do
    case "$arg" in
        --keep-docker) KEEP_DOCKER=1 ;;
        --keep-host)   KEEP_HOST=1   ;;
    esac
done

if [ "$KEEP_HOST" = "0" ]; then
    echo "[stop] stopping host processes..."
    # Resolve PIDs by exec-name + argv (avoids matching bash wrappers
    # whose argv string contains the same path as the real process).
    SERVE_RAG_PID=$(ps -eo pid=,comm=,args= \
        | awk '$2 ~ /^python/ && /lai\.api\.serve_rag/ && /--port 18000/ {print $1}')
    VITE_PID=$(ps -eo pid=,comm=,args= \
        | awk '$2 == "node" && /LAI-UI\/.*\.bin\/vite/ {print $1}')
    if [ -n "$SERVE_RAG_PID" ]; then
        kill $SERVE_RAG_PID 2>/dev/null && echo "[stop]   serve_rag.py killed (PID $SERVE_RAG_PID)"
    else
        echo "[stop]   serve_rag.py not running"
    fi
    if [ -n "$VITE_PID" ]; then
        kill $VITE_PID 2>/dev/null && echo "[stop]   Vite killed (PID $VITE_PID)"
    else
        echo "[stop]   Vite not running"
    fi
fi

if [ "$KEEP_DOCKER" = "0" ]; then
    echo "[stop] stopping Docker services..."
    cd "$LAI_DIR"
    docker compose down
fi

echo "[stop] done."
