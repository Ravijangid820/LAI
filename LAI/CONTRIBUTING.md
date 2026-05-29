# Contributing to LAI

This document is the workflow contract for everyone touching the codebase. It
exists because the LAI v1 build is held to a single production-grade quality
bar — no compromise — and the only way that survives multiple contributors is
to write the rules down once.

If `make check` doesn't pass locally, your change is not done. If CI fails on
a PR, the PR doesn't merge. There is no "I'll fix it later" path.

---

## 1. Environment setup

```bash
# From the repo root.
cd LAI

# Install dev + runtime dependencies into a project-local .venv.
make install
```

`make install` runs `uv sync --extra dev` (or `pip install -e ".[dev]"` if `uv`
is not on `$PATH`) and installs the pre-commit hooks.

Python is pinned to **3.13** via `[project] requires-python` in
`pyproject.toml` and the project-local `.venv`.

---

## 2. The quality gate

One command is the source of truth, locally and in CI:

```bash
make check
```

That runs, in order:

| Step       | Tool      | What it gates                                                |
|------------|-----------|--------------------------------------------------------------|
| `lint`     | ruff      | Style + a curated lint set (E, F, I, N, W, UP, B, C4, SIM, RET, PIE, PT, RUF). Formatter must report no changes. |
| `type`     | mypy      | **Strict** on `src/lai/common` (production code). Tests are validated by running, not by mypy — `@pytest.mark.*` decorators produce noise that doesn't catch real bugs. Legacy paths stay permissive. |
| `cov`      | pytest    | Unit tests on `tests/unit/**`. Coverage on `src/lai/common` must be ≥ 85% (line + branch). |
| `security` | bandit    | Security scan on `src/lai/common`. |

CI (`.github/workflows/ci.yml`) runs the same targets — `make ci` is an alias
for `make check`. There are no checks that exist only in CI and no checks that
exist only locally.

### Sub-targets

- `make lint`       — just ruff lint + format check.
- `make format`     — apply ruff fixes + format. **The only target that mutates the tree.**
- `make type`       — just mypy strict on the gated paths.
- `make test`       — unit tests only (no coverage gate).
- `make test-all`   — includes `@pytest.mark.integration` (needs live services).
- `make cov`        — unit tests + coverage gate.
- `make security`   — bandit only.

---

## 3. What "strict-gated" means

LAI carries ~3,200 lines of legacy code that was never linted or typed. The
quality gate is therefore scoped: every new module we write or migrate enters
under **strict mypy + full ruff rule set + 85% coverage**; legacy modules keep
the permissive defaults until each is migrated.

The scope is declared in two places:

- `pyproject.toml` → `[[tool.mypy.overrides]] module = "lai.common.*"` and
  `[tool.coverage.run] source = ["src/lai/common"]`.
- `Makefile` → `STRICT_SRC := src/lai/common`.

When a legacy module migrates: add it to the mypy override `module` list, add
its source dir to coverage `source`, and update `STRICT_SRC` in the Makefile.
The CI workflow does not need editing.

---

## 4. Branching and releases

We run **Git Flow**: two permanent branches, releases marked by tags. A version
is a **tag, never a branch** — do not create long-lived branches named `v3`,
`v4`, etc. (that is how the old `v2-restructure` branch drifted 113 commits
behind master).

| Branch     | Role                                                              |
|------------|-------------------------------------------------------------------|
| `master`   | **Released** code only. Protected. Every commit here is shippable; release tags live here. |
| `develop`  | **Integration** line — the next release in progress. Feature work merges here first. |

### Everyday work

```bash
git checkout develop && git pull          # start from the integration line
git checkout -b feat/<short-name>         # short-lived branch for ONE change
# ...work, commit...
git push -u origin feat/<short-name>
# open a PR INTO develop → CI gate (make check) → review → merge → delete branch
```

Feature branches are short-lived: branch off `develop`, merge back into
`develop`. Never branch a feature off `master`.

### Cutting a release

When `develop` is release-ready, promote it to `master` and tag it with
[semver](https://semver.org/) (`vMAJOR.MINOR.PATCH`):

```bash
git checkout master && git merge --no-ff develop
# bump  [project] version  in pyproject.toml to match the tag, then commit
git tag -a v3.1.0 -m "LAI v3.1.0 — <one line>"
git push origin master develop v3.1.0
```

- **PATCH** (`v3.0.1`) — bug fixes only, no behaviour change.
- **MINOR** (`v3.1.0`) — backward-compatible features.
- **MAJOR** (`v4.0.0`) — breaking change or a major milestone.

Keep `[project] version` in `pyproject.toml` equal to the latest tag.

### Hotfixes (and why we back-merge)

A production bug in a released version must be fixed without shipping the
unfinished work sitting on `develop`. So branch off **`master`**, fix, tag a
patch — then **back-merge into `develop`** so the fix isn't lost:

```bash
git checkout -b hotfix/<name> master
# ...fix, commit...
git checkout master && git merge --no-ff hotfix/<name>
git tag -a v3.0.1 -m "LAI v3.0.1 — <fix>" && git push origin master v3.0.1
git checkout develop && git merge master   # ← BACK-MERGE: keeps the fix on both lines
git push origin develop
```

Skip the back-merge and the next `develop → master` release can silently
re-introduce the bug you just fixed (develop still holds the old, unfixed code).
The hotfix is the *only* commit that ever lands on `master` without first going
through `develop`.

### Commit hygiene

- Commit messages follow **Conventional Commits**
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`, `build:`,
  `perf:`, `style:`, `revert:`). The `commit-msg` pre-commit hook enforces it.
- One logical change per commit. Mixing refactor + feature in one commit
  invalidates `git bisect`.
- Production PRs should be ≤ 400 LOC of non-test code. Larger means split.

> **Recommended guardrail:** enable branch protection on `master` in GitHub
> (Settings → Branches): require a PR + green CI before merge. That enforces
> "master is always shippable" instead of relying on everyone remembering.

---

## 5. Writing tests

- Every new module in `src/lai/common/` has a sibling test module in
  `tests/unit/common/`.
- Tests are marked with `@pytest.mark.unit` (or `integration` / `e2e` /
  `slow`). Unmarked tests fail under `--strict-markers`.
- Property-based testing via Hypothesis is encouraged for parsers, sanitizers,
  and salvage logic. Use a `@settings(deadline=None)` if a property is
  inherently slow; mark it `slow` instead of bumping the deadline globally.
- Integration tests requiring live services (vLLM, Postgres, Redis) live in
  `tests/integration/` and are skipped by `make test`. They run in CI on a
  separate job once we wire in service containers; until then, run them
  locally before merging.

---

## 6. Documentation expectations

- Every public symbol in `lai.common` has a docstring.
- Non-obvious design choices live in `LAI/docs/adr/NNNN-title.md` (one per
  decision). The first three ADRs are:
  - `0001-llm-client-async-primary.md`
  - `0002-guided-json-schema-enforcement.md`
  - `0003-think-trace-server-side-stripping.md`

---

## 7. What "do not compromise" means in practice

- No `# type: ignore` without a specific code (e.g. `# type: ignore[attr-defined]`)
  and a one-line comment explaining why. `warn_unused_ignores = true` will
  catch ones that become stale.
- No `# noqa` without a specific rule code and a one-line comment.
- No commented-out code in committed files. If it might be needed, it lives
  in the git history.
- No `print()` in production code — use `structlog`.
- No `time.sleep` in production code without a comment justifying it.
- No `requests` in new code — use the project's async `httpx` client.
- No bare `except:` — catch typed exceptions, or `Exception` with a comment.

---

## 8. Reviewing a contribution

The reviewer's checklist:

- [ ] `make check` was run locally and is green.
- [ ] CI is green on the same commit.
- [ ] One logical change per commit; the commit message reads as a summary of
      the diff.
- [ ] Public-API changes have docstrings and (if non-obvious) an ADR.
- [ ] Tests cover the new behaviour; coverage on `lai.common` is still ≥ 85%.
- [ ] No new `# type: ignore` or `# noqa` without a specific code + comment.

If any box is unticked, request changes.
