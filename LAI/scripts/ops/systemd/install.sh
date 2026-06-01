#!/usr/bin/env bash
# ============================================================================
# Install the serve_rag systemd unit. RUN AS ROOT (`sudo`).
# ----------------------------------------------------------------------------
# What this does:
#   1. Copies serve_rag.service to /etc/systemd/system/.
#   2. systemctl daemon-reload.
#   3. systemctl enable serve_rag    (start on boot).
#   4. If serve_rag is currently up via the legacy nohup path, gracefully
#      stops it (SIGTERM, wait, escalate if needed) — then starts it via
#      systemd. If nothing is running, just starts via systemd.
#   5. Polls /health to confirm READY.
#
# Idempotent. Safe to re-run.
# ============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[error] this script must run as root (use sudo)" >&2
    exit 1
fi

SRC="/data/projects/lai/LAI/scripts/ops/systemd/serve_rag.service"
DEST="/etc/systemd/system/serve_rag.service"

[ -f "$SRC" ] || { echo "[error] unit file not found: $SRC" >&2; exit 1; }

echo "===== installing unit ====="
install -m 0644 "$SRC" "$DEST"
echo "  ${DEST}"

echo "===== daemon-reload + enable ====="
systemctl daemon-reload
systemctl enable serve_rag

echo "===== stop any legacy nohup-launched serve_rag (so we don't fight for :18000) ====="
LEGACY_PIDS="$(pgrep -f 'python -m lai.api.serve_rag' || true)"
if [ -n "$LEGACY_PIDS" ]; then
    echo "  legacy PIDs: $LEGACY_PIDS"
    for pid in $LEGACY_PIDS; do
        # Skip our soon-to-be-started systemd-managed instance (won't exist yet).
        kill -TERM "$pid" 2>/dev/null || true
    done
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        if [ -z "$(pgrep -f 'python -m lai.api.serve_rag' || true)" ]; then
            break
        fi
    done
    REMAINING="$(pgrep -f 'python -m lai.api.serve_rag' || true)"
    if [ -n "$REMAINING" ]; then
        echo "  SIGKILL holdouts: $REMAINING"
        for pid in $REMAINING; do kill -KILL "$pid" 2>/dev/null || true; done
        sleep 1
    fi
else
    echo "  no legacy process found"
fi

echo "===== start via systemd ====="
systemctl restart serve_rag

echo "===== waiting up to 120 s for /health ====="
for i in $(seq 1 60); do
    if curl -fs -m 3 http://localhost:18000/health >/dev/null 2>&1; then
        echo "  READY after ${i}×2 s"
        curl -s http://localhost:18000/health
        echo
        exit 0
    fi
    sleep 2
done

echo "[error] /health never returned 200; check  journalctl -u serve_rag -n 200" >&2
systemctl status serve_rag --no-pager || true
exit 1
