#!/usr/bin/env bash
# ============================================================================
# scripts/ops/statute_feed.sh — gesetze-im-internet.de feed (Phase 4.3) ops
# ----------------------------------------------------------------------------
# Mobile-friendly wrapper around ``python -m lai.pipeline.statute_feed``.
# Sources ``LAI/micro-services/.env`` for the DB password and writes logs to
# ``logs/pipeline/statute_feed_*.log``. Background runs go via setsid+nohup
# so they survive SSH disconnects; PID is tracked in ``processed/statute_feed.pid``.
# ============================================================================

set -euo pipefail

LAI_DIR="/data/projects/lai/LAI"
LOG_DIR="$LAI_DIR/logs/pipeline"
PID_DIR="$LAI_DIR/processed"
PID_FILE="$PID_DIR/statute_feed.pid"

mkdir -p "$LOG_DIR" "$PID_DIR"
cd "$LAI_DIR"

# uv reads HOME for its cache; default if not set (cron has no HOME).
: "${HOME:=/data/home/$(whoami)}"
export HOME

# Source DB env (PGPASSWORD lives here).
if [ -f "$LAI_DIR/micro-services/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$LAI_DIR/micro-services/.env"
    set +a
fi

usage() {
    cat <<EOF
Usage: $(basename "$0") <mode> [args]

Modes:
  --status              Print the current feed state.
  --mapped              Backfill the 29 registry-mapped wind-relevant laws
                        (foreground; ~12 min).
  --full [--limit N]    Full TOC sweep (background; ~43 h for all ~6,123 laws).
                        --limit caps the count for smoke-tests.
  --prune [--missing-days N]
                        DELETE corpus rows for laws missing from the TOC for
                        >= N days (default 7). Foreground.
  --tail                tail -f the latest statute_feed log.
  --stop                Kill the background --full job (if running).
  --help                Show this help.

Logs:   $LOG_DIR/statute_feed_*.log
PID:    $PID_FILE
Env:    sources $LAI_DIR/micro-services/.env automatically.
EOF
}

run_fg() {
    # Foreground modes pipe through tee so you see output AND it's logged.
    local LOG="$LOG_DIR/statute_feed_$(date +%Y%m%d_%H%M%S).log"
    echo "[ops] log: $LOG"
    uv run python -m lai.pipeline.statute_feed "$@" 2>&1 | tee "$LOG"
}

case "${1:-}" in
    --status)
        uv run python -m lai.pipeline.statute_feed --status
        ;;
    --mapped)
        run_fg --backfill mapped
        ;;
    --full)
        shift
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "[ops] a background --full is already running (pid=$(cat "$PID_FILE")); --stop first"
            exit 1
        fi
        LOG="$LOG_DIR/statute_feed_full_$(date +%Y%m%d_%H%M%S).log"
        echo "[ops] launching FULL backfill in background"
        echo "      log: $LOG"
        setsid nohup uv run python -m lai.pipeline.statute_feed \
            --backfill all "$@" > "$LOG" 2>&1 < /dev/null &
        echo $! > "$PID_FILE"
        echo "      pid=$(cat "$PID_FILE")  ; tail with  $0 --tail  ; stop with  $0 --stop"
        ;;
    --prune)
        shift
        run_fg --prune-removed "$@"
        ;;
    --stop)
        if [ -f "$PID_FILE" ]; then
            PID="$(cat "$PID_FILE")"
            if kill -0 "$PID" 2>/dev/null; then
                echo "[ops] stopping pid=$PID"
                kill "$PID"
                sleep 1
                if kill -0 "$PID" 2>/dev/null; then
                    echo "[ops] still running; SIGKILL"
                    kill -9 "$PID"
                fi
                rm -f "$PID_FILE"
            else
                echo "[ops] pid=$PID not running; cleaning stale pidfile"
                rm -f "$PID_FILE"
            fi
        else
            echo "[ops] no pidfile; nothing to stop"
        fi
        ;;
    --tail)
        LOG="$(ls -t "$LOG_DIR"/statute_feed_*.log 2>/dev/null | head -1 || true)"
        if [ -z "$LOG" ]; then
            echo "[ops] no statute feed logs found in $LOG_DIR"
            exit 1
        fi
        echo "[ops] tailing $LOG (Ctrl-C to stop)"
        exec tail -f "$LOG"
        ;;
    --help|-h|"")
        usage
        ;;
    *)
        echo "[error] unknown mode: ${1:-}" >&2
        echo
        usage
        exit 2
        ;;
esac
