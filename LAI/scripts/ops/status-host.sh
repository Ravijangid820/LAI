#!/usr/bin/env bash
# =============================================================================
# Status check for the no-Docker LAI stack (scripts/ops/start-host.sh).
# Shows, per service: pidfile process liveness, port readiness, and HTTP
# health where applicable. Also checks the host PostgreSQL DDiQ depends on.
# =============================================================================
set -uo pipefail

# scripts/ops/status-host.sh → ../.. is the LAI/ project root.
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$LAI_DIR/logs/host"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m⏳\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }

pid_alive() {  # pid_alive NAME → echoes PID if its pidfile process is alive
    local pidf="$LOG_DIR/$1.pid" pid
    [ -f "$pidf" ] || return 1
    pid="$(cat "$pidf" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { echo "$pid"; return 0; }
    return 1
}

port_up()  { ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }
http_code() { curl -s -m 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null; }

# row NAME PORT HEALTH_URL("" to skip)
row() {
    local name="$1" port="$2" url="${3:-}" pid
    pid="$(pid_alive "$name" || true)"
    if [ -n "$url" ]; then
        local code; code="$(http_code "$url")"
        if [ "$code" = "200" ]; then
            ok   "$(printf '%-10s' "$name") :$port  PID ${pid:-?}  HTTP 200"
        elif [ -n "$pid" ]; then
            warn "$(printf '%-10s' "$name") :$port  PID $pid  loading (HTTP ${code:-000})"
        elif port_up "$port"; then
            warn "$(printf '%-10s' "$name") :$port  (port up, not from our pidfile)"
        else
            bad  "$(printf '%-10s' "$name") :$port  down"
        fi
    else
        if port_up "$port" && [ -n "$pid" ]; then
            ok   "$(printf '%-10s' "$name") :$port  PID $pid"
        elif [ -n "$pid" ]; then
            warn "$(printf '%-10s' "$name") :$port  PID $pid  (port not up yet)"
        elif port_up "$port"; then
            warn "$(printf '%-10s' "$name") :$port  (port up, not from our pidfile)"
        else
            bad  "$(printf '%-10s' "$name") :$port  down"
        fi
    fi
}

echo "LAI host services (no Docker):"
row analyzer  8005  "http://localhost:8005/v1/models"
row embedding 8003  "http://localhost:8003/health"
row reranker  8004  "http://localhost:8004/health"
row serve_rag 18000 "http://localhost:18000/health"
row ddiq      18001 "http://localhost:18001/health"
row vite      5173  "http://localhost:5173/"

# ── user-local PostgreSQL (DDiQ dependency) ──────────────────────────────
echo
echo "User-local PostgreSQL (DDiQ):"
PG_DATA="${LAI_PG_DATA:-$LAI_DIR/data/pg-host}"
PG_PORT="${LAI_PGPORT:-5435}"
DB_NAME="${DB_NAME:-lai_db}"
DB_USER="${DB_USER:-lai_user}"
PG_BIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
if [ -z "$PG_BIN" ]; then
    bad "PostgreSQL binaries not found under /usr/lib/postgresql/*/bin"
elif [ ! -f "$PG_DATA/PG_VERSION" ]; then
    bad "cluster not initialised ($PG_DATA) — start-host.sh will initdb on first run"
elif ! "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
    bad "cluster at $PG_DATA not running"
elif "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "SELECT 1" >/dev/null 2>&1; then
    has_vec="$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | tr -d ' ')"
    if [ "$has_vec" = "1" ]; then
        ok "$DB_NAME on 127.0.0.1:$PG_PORT  (pgvector enabled)"
    else
        warn "$DB_NAME on 127.0.0.1:$PG_PORT  but pgvector extension missing"
    fi
else
    bad "cluster running but $DB_NAME/$DB_USER not usable"
fi

# ── GPU ──────────────────────────────────────────────────────────────────
echo
echo "GPU:"
nvidia-smi --query-gpu=index,memory.used,memory.free,utilization.gpu \
    --format=csv,noheader 2>/dev/null | sed 's/^/  /' || echo "  nvidia-smi unavailable"

# ── logs hint ────────────────────────────────────────────────────────────
echo
echo "Logs: $LOG_DIR/<svc>.log"
