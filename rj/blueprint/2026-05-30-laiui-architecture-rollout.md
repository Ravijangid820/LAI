# Plan — Bring LAI-UI under the same architecture as LAI

**Date:** 2026-05-30 · **Owner:** rj · **Status:** PROPOSED — awaiting sign-off
**Why sign-off:** changes the LAI-UI branching model + introduces a CI gate,
both of which affect everyone working in that repo. Also needs the active FE
WIP to land first (cannot impose mid-WIP).
**Context:** LAI now runs Git Flow + blueprint plan-docs (see
[`feedback_git_workflow`](../../../.claude/projects/-data-projects-lai/memory/feedback_git_workflow.md)
and the blueprints alongside this file). LAI-UI is the FE counterpart; the
team works in both. One mental model is cheaper than two.

## Goal
Mirror the LAI architecture in LAI-UI so the same conventions apply across
both repos:
- Two permanent branches (`master` = released, `develop` = integration).
- Semver tags on `master`.
- Forward-looking plan docs in `rj/blueprint/YYYY-MM-DD-<topic>.md`.
- A small CI gate (build + lint) that the FE must pass to land on develop.

## Current LAI-UI state (anchors)
- Repo: `/data/projects/lai/LAI-UI/` (its own git repo; gitignored from LAI).
- Active branch: **`fix/cross-account-isolation`** (de-facto integration line);
  ahead 3 of origin; **26 dirty files** of teammate upload-resumable WIP.
- `package.json` version: `0.0.0` — never tagged. Scripts: `build` (=
  `tsc -b && vite build`), `dev`, `lint` (eslint). No test runner.
- No `CONTRIBUTING.md`, no `.github/workflows/`, no `CODEOWNERS`.

## Approach (mirror what fits; adapt what doesn't)

| LAI today | LAI-UI mirror | Notes |
|---|---|---|
| `master` + `develop` permanent | same | Need to identify "released FE state" for the `master` baseline. |
| Semver tags (`v1.0.0`, …) | same | Baseline `v1.0.0` = currently-deployed FE; bump `package.json` to match. |
| Commit direct to `develop` for solo additive work | same | Keep each commit non-breaking; develop stays buildable. |
| `lai.common` strict gate (ruff + mypy --strict + ≥85 % cov + bandit) | **`npm run build` (= tsc + vite) + `npm run lint`** | Analogue. No test suite yet → no coverage gate from day one. Vitest + coverage floor on `src/react-app/lib/` is a follow-up. |
| `LAI/CONTRIBUTING.md` §4 (Git Flow rules) | `LAI-UI/CONTRIBUTING.md` (same §4 verbatim with TS-flavoured commands) | |
| `rj/blueprint/YYYY-MM-DD-<topic>.md` | same — top-level `rj/blueprint/` inside LAI-UI | Personal dirs (`rj/`, `harsh/`) at top-level if useful, otherwise just `rj/blueprint/`. |
| `.github/CODEOWNERS` | optional — the FE owner is well-known | Skip unless the team wants it. |

## Decisions / risks
- **Cannot impose mid-WIP.** 26 dirty files on `fix/cross-account-isolation`
  are a teammate's active work. Step 0 is to wait for them to commit + push.
- **Buy-in matters.** The FE owner gets the proposal first; if they prefer a
  different model (e.g. trunk-only without `develop`), the blueprint moves
  toward that. This doc is a **starting position**, not a fait accompli.
- **Master baseline.** Today there is no `master`; the released FE state has
  to be identified explicitly (probably the tip of `fix/cross-account-isolation`
  once the WIP lands).
- **CI gate scope.** Starting with `build + lint` is honest about the current
  state (no tests). Adding Vitest later is a separate blueprint.

## Steps
1. **Pre-flight — chat with the FE owner.** Share this blueprint; align on
   timing + the four open questions below. **Do not execute steps 2-7 without
   their go-ahead.**
2. **Wait for the active FE WIP** (`fix/cross-account-isolation`'s 26 dirty
   files) to be committed + pushed. Verify `git status` is clean.
3. **Identify the released-FE commit** = will become `master`. Likely the tip
   of `fix/cross-account-isolation` after WIP lands.
4. **Establish branches:**
   - `git checkout -b master <released-commit> && git push -u origin master`
   - `git checkout -b develop <released-commit> && git push -u origin develop`
   - (Optional, after a transition period) `git push origin --delete
     fix/cross-account-isolation`.
5. **Tag the baseline:** bump `package.json` version to `1.0.0`, commit
   `chore(release): 1.0.0`, then `git tag -a v1.0.0 -m "LAI-UI v1.0.0 —
   baseline"` on master; push tag.
6. **Add convention files:**
   - `CONTRIBUTING.md` — Git Flow §4 mirrored from LAI, with TS commands
     (`npm ci && npm run build && npm run lint` as the gate).
   - `rj/blueprint/` — seed with the **first real plan doc** (see step 7).
   - (Optional, recommended) `.github/workflows/ci.yml` running
     `npm ci && npm run build && npm run lint` on push/PR to develop + master.
   - (Optional) Branch protection on `master` in GitHub Settings → require
     PR + green CI before merge.
7. **First real LAI-UI blueprint = the audit-log view deploy.**
   `LAI/harsh/PROGRESS_V2.md` 2.3 notes the audit-log admin view at
   `/dashboard/admin/audit` was committed in LAI-UI but never deployed.
   It's small, concrete, and high-value — perfect first instance of the new
   plan-doc convention in LAI-UI. Write
   `LAI-UI/rj/blueprint/<date>-audit-log-view-deploy.md`; ship.

## Open questions for sign-off
1. **Timing** — start steps 2-7 as soon as the FE WIP lands, or wait for a
   natural "release boundary"?
2. **Baseline version** — `v1.0.0` (semver convention; recommended) or a more
   cautious `v0.1.0`?
3. **CI gate** — just `build + lint` from day one (recommended), or also add
   Vitest scaffolding and a coverage floor on `src/react-app/lib/` now?
4. **Branch protection on `master`** — enable now in GitHub Settings, or wait
   until the team is comfortable with the flow?

## Definition of done
- `master` + `develop` exist on `origin`; `v1.0.0` tag on `master`.
- `CONTRIBUTING.md` committed; the team has agreed to follow it.
- `LAI-UI/rj/blueprint/` exists and holds the audit-log view deploy plan.
- (If chosen) `.github/workflows/ci.yml` runs on every PR + push and the gate
  blocks merges on failure.
- A successful audit-log view deploy executed per the new flow (proves the
  end-to-end loop: blueprint → develop → release tag → deploy).
