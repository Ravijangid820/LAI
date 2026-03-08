#!/usr/bin/env bash
# =============================================================================
# LAIV4 RAG Test Runner
# =============================================================================
# Convenience script that sources the .env and runs test_rag.py
# with the correct environment variables for the compose stack.
#
# Usage:
#   bash run_test.sh                        # run all tests
#   bash run_test.sh --test db              # single test
#   bash run_test.sh --test hybrid --top-k 10
#   bash run_test.sh --query "Was ist § 823 BGB?"
#   bash run_test.sh --list-tests
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../LAIV4" && pwd)"

# Source the .env to override LAIV3/.env defaults
set -a
source "$SCRIPT_DIR/.env"
set +a

echo "──────────────────────────────────────────────────────"
echo "  LAIV4 RAG Test Runner"
echo "──────────────────────────────────────────────────────"
echo "  DB:        ${PGHOST:-localhost}:${PGPORT:-5433}/${PGDATABASE:-lai_db}"
echo "  Embedding: ${EMBEDDING_URL:-http://localhost:8002}"
echo "  LLM:       ${LLM_URL:-http://localhost:8001/v1}"
echo "  Model:     ${LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
echo "──────────────────────────────────────────────────────"
echo ""

cd "$PROJECT_DIR"

# Use the LAIV4 venv if it exists, otherwise system python
if [[ -f "$PROJECT_DIR/.venv/bin/python" ]]; then
    exec "$PROJECT_DIR/.venv/bin/python" -m rag.test_rag "$@"
else
    exec python3 -m rag.test_rag "$@"
fi
