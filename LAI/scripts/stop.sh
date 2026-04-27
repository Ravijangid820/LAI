#!/usr/bin/env bash
# Tear down the full LAI runtime — host processes first (so they don't
# error on closing connections), then Docker services.
#
# Usage:
#   bash scripts/stop.sh             # full stop
#   bash scripts/stop.sh --keep-docker   # only stop host processes
#   bash scripts/stop.sh --keep-host     # only stop Docker services
set -euo pipefail

LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
    pkill -f "scripts/serve_rag.py --port 18000" 2>/dev/null && echo "[stop]   serve_rag.py killed" || echo "[stop]   serve_rag.py not running"
    # Match LAI vite specifically — don't kill unrelated vite servers on the box
    ps -ef | awk '$0 ~ /web_ui\/LAI.*vite/ && $0 !~ /awk/ {print $2}' | xargs -r kill 2>/dev/null \
        && echo "[stop]   Vite killed" || echo "[stop]   Vite not running"
fi

if [ "$KEEP_DOCKER" = "0" ]; then
    echo "[stop] stopping Docker services..."
    cd "$LAI_DIR"
    docker compose down
fi

echo "[stop] done."
