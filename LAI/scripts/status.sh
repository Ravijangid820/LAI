#!/usr/bin/env bash
# Quick status check — what's up, what's loading, what's down.
set -uo pipefail

ok()    { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn()  { printf "  \033[33m⏳\033[0m %s\n" "$1"; }
fail()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }

# ── containers ────────────────────────────────────────────────────────────
echo "Docker services:"
for name in lai_analyzer_llm lai_embedding lai_reranker lai_postgres_main lai_redis; do
    state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}({{.State.Health.Status}}){{end}}' "$name" 2>/dev/null)
    if [ -z "$state" ]; then
        fail "$name (not created)"
    elif echo "$state" | grep -q "running"; then
        if echo "$state" | grep -q "healthy"; then
            ok "$name — $state"
        elif echo "$state" | grep -q "starting"; then
            warn "$name — $state"
        else
            ok "$name — $state"
        fi
    else
        fail "$name — $state"
    fi
done

# ── HTTP endpoints ────────────────────────────────────────────────────────
echo
echo "Endpoints (localhost):"
check() {
    local label="$1" url="$2"
    local code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    if [ "$code" = "200" ]; then ok "$label  $url"
    elif [ "$code" = "000" ]; then fail "$label  $url (no response)"
    else warn "$label  $url  HTTP $code"
    fi
}
check "serve_rag /health     " "http://localhost:18000/health"
check "analyzer /v1/models   " "http://localhost:8005/v1/models"
check "embedding /health     " "http://localhost:8003/health"
check "Vite UI               " "http://localhost:5173/"

# ── host processes ────────────────────────────────────────────────────────
echo
echo "Host processes:"
if pgrep -af "scripts/serve_rag.py --port 18000" >/dev/null; then
    ok "serve_rag.py — PID $(pgrep -f "scripts/serve_rag.py --port 18000" | head -1)"
else
    fail "serve_rag.py — not running"
fi
if pgrep -af "web_ui/LAI.*vite" >/dev/null; then
    ok "Vite — PID $(pgrep -af "web_ui/LAI.*vite" | head -1 | awk '{print $1}')"
else
    fail "Vite — not running"
fi

# ── persistence ───────────────────────────────────────────────────────────
echo
echo "Persistence:"
LAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$LAI_DIR/processed/sessions.db"
if [ -f "$DB" ]; then
    nsess=$(curl -fs http://localhost:18000/sessions?limit=999 2>/dev/null \
        | python3 -c "import json,sys; print(len(json.load(sys.stdin)['sessions']))" 2>/dev/null \
        || echo "?")
    ok "sessions.db ($(du -h "$DB" | cut -f1))  sessions=$nsess"
else
    fail "sessions.db not found"
fi
nuploads=$(ls -1 "$LAI_DIR/processed/uploads" 2>/dev/null | wc -l)
ok "uploaded files: $nuploads under processed/uploads/"
