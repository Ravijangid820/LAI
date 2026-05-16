# LAI — Local development quality gate
#
# Single source of truth for the checks that gate every commit. CI runs the
# same targets, so `make check` passing locally is what the pipeline will
# evaluate. No silently-skipped checks; every target is fail-fast.
#
# Run `make help` for the menu.

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Use uv if available (project's declared package manager); fall back to the
# project's .venv python so the targets work in both flows.
UV         := $(shell command -v uv 2>/dev/null)
VENV_PY    := .venv/bin/python
PYTHON     ?= $(if $(UV),uv run python,$(VENV_PY))
PYTEST     ?= $(if $(UV),uv run pytest,$(VENV_PY) -m pytest)
RUFF       ?= $(if $(UV),uv run ruff,$(VENV_PY) -m ruff)
MYPY       ?= $(if $(UV),uv run mypy,$(VENV_PY) -m mypy)
BANDIT     ?= $(if $(UV),uv run bandit,$(VENV_PY) -m bandit)
COVERAGE   ?= $(if $(UV),uv run coverage,$(VENV_PY) -m coverage)
PRECOMMIT  ?= $(if $(UV),uv run pre-commit,$(VENV_PY) -m pre_commit)

# Scope of the strict gate. Expand as more code migrates to clean status.
STRICT_SRC  := src/lai/common
STRICT_TEST := tests/unit/common

# ─────────────────────────────────────────────────────────────────────────────
# Targets
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help install lint format type test cov security check ci clean

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install dev + runtime dependencies (uv sync --dev).
	@if [ -n "$(UV)" ]; then \
		uv sync --extra dev; \
	else \
		$(VENV_PY) -m pip install -e ".[dev]"; \
	fi
	@$(PRECOMMIT) install --install-hooks

lint:  ## Ruff lint (no fixes).
	$(RUFF) check src tests
	$(RUFF) format --check src tests

format:  ## Apply ruff format + safe fixes.
	$(RUFF) check --fix src tests
	$(RUFF) format src tests

type:  ## Mypy strict on the strict-gated paths.
	$(MYPY) $(STRICT_SRC) $(STRICT_TEST)

test:  ## Run unit tests (fast).
	$(PYTEST) -m "unit and not slow" tests/unit

test-all:  ## Run all tests including integration (requires running services).
	$(PYTEST) tests

cov:  ## Run unit tests with coverage and enforce the 85% gate on lai.common.
	$(PYTEST) -m "unit and not slow" \
	          --cov=src/lai/common \
	          --cov-branch \
	          --cov-report=term-missing \
	          --cov-report=html:.coverage_html \
	          --cov-fail-under=85 \
	          tests/unit

security:  ## Bandit security scan on lai.common.
	$(BANDIT) -c pyproject.toml -r $(STRICT_SRC)

check: lint type cov security  ## Run every gate. Match CI.
	@echo
	@echo "✓ all checks passed."

ci: check  ## Alias used by the CI workflow.

clean:  ## Remove caches.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage .coverage_html htmlcov build dist
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
