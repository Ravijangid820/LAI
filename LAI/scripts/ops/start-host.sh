#!/usr/bin/env bash
# =============================================================================
# Start the full LAI stack WITHOUT Docker — every service runs as a host
# process. Use this when the Docker daemon/socket is unavailable (e.g. your
# user isn't in the `docker` group) or you just want a docker-free runtime.
#
# Mirrors scripts/start.sh, but instead of `docker compose up` it launches
# vLLM directly from .venv. Services:
#   1. analyzer  vLLM  Qwen3.6-27B            :8005  GPU 0
#   2. embedding vLLM  Qwen3-Embedding-8B     :8003  GPU 1
#   3. reranker  vLLM  ms-marco-MiniLM-L-12   :8004  GPU 0  (tiny, for DDiQ)
#   4. serve_rag.py    chat backend           :18000 GPU 1  (in-proc reranker)
#   5. DDiQ      uvicorn api:app              :18001 CPU
#   6. Vite UI                                :5173
#
# Idempotent: a service already listening on its port is left alone, so
# re-running only fills in what's missing.
#
# Postgres: DDiQ needs PostgreSQL + pgvector. This script runs its OWN
# user-local cluster — initdb'd into LAI/data/pg-host, postgres on :5435,
# all as your user (no root). pgvector works because its files are already
# installed system-wide; you're the superuser of your own cluster so
# CREATE DATABASE / CREATE EXTENSION need no sudo. First run does the
# initdb automatically.
#
# Usage:
#   bash scripts/ops/start-host.sh                 # bring everything up
#   bash scripts/ops/start-host.sh --no-ddiq       # skip the DDiQ backend
#   bash scripts/ops/start-host.sh --no-vite       # skip the Vite UI
#   LAI_BIND_HOST=127.0.0.1 bash scripts/ops/start-host.sh   # loopback-only
#
# Logs + pidfiles live under logs/host/<svc>.{log,pid}.
# Stop with:   bash scripts/ops/stop-host.sh
# Status with: bash scripts/ops/status-host.sh
# =============================================================================
set -uo pipefail

# ── paths ────────────────────────────────────────────────────────────────
# scripts/ops/start-host.sh → ../.. is the LAI/ project root.
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAI_UI_DIR="${LAI_UI_DIR:-$(cd "$LAI_DIR/.." && pwd)/LAI-UI}"
VENV="$LAI_DIR/.venv"
VENV_PY="$VENV/bin/python"
VLLM_BIN="$VENV/bin/vllm"
MS_DIR="$LAI_DIR/micro-services"
MS_VENV="$MS_DIR/.venv"
LOG_DIR="$LAI_DIR/logs/host"
HF_CACHE="$LAI_DIR/.runtime-cache/hf"
mkdir -p "$LOG_DIR"

# ── env defaults (override on the command line) ──────────────────────────
export LAI_BIND_HOST="${LAI_BIND_HOST:-0.0.0.0}"   # serve_rag / DDiQ / Vite
MODEL_BIND="${MODEL_BIND:-127.0.0.1}"              # vLLM endpoints: internal-only
VPN_MODE="${VPN_MODE:-1}"

# User-local PostgreSQL for DDiQ — our own cluster, no root. The Docker
# stack used pg16 on :5434; this runs pg on :5435 from data we own.
PG_DATA="${LAI_PG_DATA:-$LAI_DIR/data/pg-host}"
PG_PORT="${LAI_PGPORT:-5435}"
PG_BIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
DB_NAME="${DB_NAME:-lai_db}"
DB_USER="${DB_USER:-lai_user}"
DB_PASSWORD="${DB_PASSWORD:-lai_test_password_2024}"

SKIP_DDIQ=0
SKIP_VITE=0
for arg in "$@"; do
    case "$arg" in
        --no-ddiq) SKIP_DDIQ=1 ;;
        --no-vite) SKIP_VITE=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "[start-host] unknown arg: $arg (see --help)"; exit 1 ;;
    esac
done

# ── helpers ──────────────────────────────────────────────────────────────
log() { echo "[start-host] $*"; }

port_up() {  # port_up PORT  → 0 if something is LISTENing on it
    ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
}

# launch NAME PORT PAYLOAD
#   PAYLOAD is a shell snippet run via `bash -c`. Use `exec env VAR=… cmd`
#   (or `cd … && exec env …`) so the recorded PID is the real process, not
#   a bash wrapper. Skips launch if PORT is already taken.
launch() {
    local name="$1" port="$2" payload="$3"
    local logf="$LOG_DIR/$name.log" pidf="$LOG_DIR/$name.pid"
    if port_up "$port"; then
        log "$name already up on :$port — leaving it"
        return 0
    fi
    log "launching $name on :$port  (log → $logf)"
    nohup bash -c "$payload" > "$logf" 2>&1 &
    local pid=$!
    echo "$pid" > "$pidf"
    disown 2>/dev/null || true
    sleep 5
    if ! kill -0 "$pid" 2>/dev/null; then
        log "  ✗ $name died on startup — last log lines:"
        tail -15 "$logf" | sed 's/^/      /'
        rm -f "$pidf"
        return 1
    fi
    log "  ✓ $name started (PID $pid)"
}

# ── preflight ────────────────────────────────────────────────────────────
[ -x "$VLLM_BIN" ] || { log "ERROR: vllm not found at $VLLM_BIN — is the .venv set up?"; exit 1; }
[ -x "$VENV_PY" ]  || { log "ERROR: python not found at $VENV_PY"; exit 1; }
[ -d "$HF_CACHE/hub" ] || log "WARNING: HF cache $HF_CACHE/hub not found — model loads may fail"

# ── DDiQ venv (created once) ─────────────────────────────────────────────
ensure_ddiq_venv() {
    if [ -x "$MS_VENV/bin/uvicorn" ]; then
        return 0
    fi
    log "DDiQ venv missing — creating $MS_VENV (one-time, ~1 min)..."
    "$VENV_PY" -m venv "$MS_VENV" || { log "  ✗ venv creation failed"; return 1; }
    "$MS_VENV/bin/pip" install -q --upgrade pip > "$LOG_DIR/ddiq-pip-install.log" 2>&1
    if ! "$MS_VENV/bin/pip" install -q -r "$MS_DIR/requirements.txt" \
            >> "$LOG_DIR/ddiq-pip-install.log" 2>&1; then
        log "  ✗ pip install failed — see $LOG_DIR/ddiq-pip-install.log"
        return 1
    fi
    log "  ✓ DDiQ venv ready"
}

# ── user-local PostgreSQL (DDiQ dependency, no root) ─────────────────────
# Runs our own cluster: initdb into PG_DATA on first run, postgres on
# :PG_PORT bound to localhost, trust-auth (single-user dev box). We're the
# cluster superuser, so CREATE DATABASE / CREATE EXTENSION vector just work.
# pg_ctl manages its own pidfile ($PG_DATA/postmaster.pid).
ensure_local_pg() {
    if [ -z "$PG_BIN" ] || [ ! -x "$PG_BIN/initdb" ]; then
        log "  ✗ PostgreSQL binaries not found under /usr/lib/postgresql/*/bin"
        return 1
    fi
    if [ ! -f "$PG_DATA/PG_VERSION" ]; then
        log "initdb — creating user-local PostgreSQL cluster at $PG_DATA (one-time)"
        mkdir -p "$PG_DATA"
        if ! "$PG_BIN/initdb" -D "$PG_DATA" -U "$DB_USER" \
                --auth-local=trust --auth-host=trust --encoding=UTF8 \
                > "$LOG_DIR/postgres-initdb.log" 2>&1; then
            log "  ✗ initdb failed — see $LOG_DIR/postgres-initdb.log"
            return 1
        fi
    fi
    if "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
        log "user-local PostgreSQL already running"
    else
        log "starting user-local PostgreSQL on :$PG_PORT"
        if ! "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$LOG_DIR/postgres.log" \
                -o "-p $PG_PORT -h 127.0.0.1 -k '$PG_DATA'" -w start \
                >/dev/null 2>&1; then
            log "  ✗ postgres failed to start — see $LOG_DIR/postgres.log"
            return 1
        fi
    fi
    if ! "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -tAc "SELECT 1" >/dev/null 2>&1; then
        log "creating database $DB_NAME"
        "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" "$DB_NAME" \
            >> "$LOG_DIR/postgres.log" 2>&1 || { log "  ✗ createdb failed"; return 1; }
    fi
    if ! "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -tAc "CREATE EXTENSION IF NOT EXISTS vector" >> "$LOG_DIR/postgres.log" 2>&1; then
        log "  ⚠ could not enable pgvector — DDiQ vector ops will fail"
    fi
    log "  ✓ PostgreSQL ready ($DB_NAME on 127.0.0.1:$PG_PORT, pgvector enabled)"
    return 0
}

# ── 1-3. vLLM model servers ──────────────────────────────────────────────
log "================ LAI host runtime (no Docker) ================"

launch analyzer 8005 \
"exec env HF_HOME='$HF_CACHE' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CUDA_VISIBLE_DEVICES=0 \
'$VLLM_BIN' serve Qwen/Qwen3.6-27B \
  --served-model-name qwen3.6-27b --max-model-len 32768 --max-num-seqs 8 \
  --dtype bfloat16 --trust-remote-code --gpu-memory-utilization 0.75 \
  --reasoning-parser qwen3 --enable-prefix-caching \
  --host $MODEL_BIND --port 8005"

launch embedding 8003 \
"exec env HF_HOME='$HF_CACHE' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CUDA_VISIBLE_DEVICES=1 \
'$VLLM_BIN' serve Qwen/Qwen3-Embedding-8B \
  --dtype auto --trust-remote-code --max-model-len 32768 \
  --gpu-memory-utilization 0.45 --host $MODEL_BIND --port 8003"

# Reranker (MiniLM) is tiny and NOT in the offline cache — leave HF online
# so it self-downloads (~130 MB) on first run, then it's cached.
# `--runner pooling` is the vLLM 0.19 replacement for the removed
# `--task score`; vLLM auto-detects the ms-marco cross-encoder and exposes
# the /score + /rerank endpoints DDiQ calls.
launch reranker 8004 \
"exec env HF_HOME='$HF_CACHE' CUDA_VISIBLE_DEVICES=0 \
'$VLLM_BIN' serve cross-encoder/ms-marco-MiniLM-L-12-v2 \
  --runner pooling --dtype auto --trust-remote-code --max-model-len 512 \
  --gpu-memory-utilization 0.02 --host $MODEL_BIND --port 8004"

# ── 4. serve_rag (chat backend) ──────────────────────────────────────────
launch serve_rag 18000 \
"cd '$LAI_DIR' && exec env CUDA_VISIBLE_DEVICES=1 LAI_BIND_HOST='$LAI_BIND_HOST' \
LLM_API_URL=http://localhost:8005 LLM_MODEL=qwen3.6-27b \
'$VENV_PY' -m lai.api.serve_rag --port 18000"

# ── 5. DDiQ backend (uvicorn) ────────────────────────────────────────────
if [ "$SKIP_DDIQ" = "0" ]; then
    if ensure_ddiq_venv && ensure_local_pg; then
        launch ddiq 18001 \
"cd '$MS_DIR' && exec env LLM_URL=http://localhost:8005/v1 LLM_MODEL=qwen3.6-27b \
EMBEDDING_URL=http://localhost:8003 RERANKER_URL=http://localhost:8004 \
DB_HOST=127.0.0.1 DB_PORT='$PG_PORT' \
DB_NAME='$DB_NAME' DB_USER='$DB_USER' DB_PASSWORD='$DB_PASSWORD' \
'$MS_VENV/bin/uvicorn' api:app --host '$LAI_BIND_HOST' --port 18001"
    else
        log "skipping DDiQ — venv or PostgreSQL setup failed"
    fi
else
    log "skipping DDiQ (--no-ddiq)"
fi

# ── 6. Vite UI ───────────────────────────────────────────────────────────
if [ "$SKIP_VITE" = "0" ]; then
    if [ ! -d "$LAI_UI_DIR" ]; then
        log "skipping Vite — LAI-UI not found at $LAI_UI_DIR (set LAI_UI_DIR=…)"
    else
        if [ ! -d "$LAI_UI_DIR/node_modules" ]; then
            log "installing LAI-UI npm dependencies (one-time)..."
            (cd "$LAI_UI_DIR" && npm install) > "$LOG_DIR/npm-install.log" 2>&1 \
                || { log "  ✗ npm install failed — see $LOG_DIR/npm-install.log"; }
        fi
        VITE_HOST_FLAG=""
        [ "$VPN_MODE" = "1" ] && VITE_HOST_FLAG="--host 0.0.0.0"
        launch vite 5173 \
"cd '$LAI_UI_DIR' && exec npm run dev -- $VITE_HOST_FLAG"
    fi
else
    log "skipping Vite (--no-vite)"
fi

# ── summary ──────────────────────────────────────────────────────────────
echo
log "done. Model servers take ~3-6 min to finish loading weights."
log "  status:  bash scripts/ops/status-host.sh"
log "  logs:    tail -f $LOG_DIR/<svc>.log    (analyzer embedding reranker serve_rag ddiq vite)"
log "  stop:    bash scripts/ops/stop-host.sh"
