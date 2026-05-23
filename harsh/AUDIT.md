# LAI System Audit — Senior Auditor Report

**Date:** 2026-05-14
**Scope:** infrastructure, backend (RAG + DDiQ), frontend, data pipeline
**Method:** independent code review with `file:line` evidence; the pre-existing
`LAI_*.md` analysis docs were *not* trusted and were spot-checked instead.

---

## Verdict on "awful"

**Half right — and the half that's right matters.** The boss is correct about
*outcomes*: the system is not production-ready, authentication is fake
end-to-end, the live deployment **was** broken at the time of this audit (since
resolved — see C3 status update), and there are zero
automated tests anywhere. But "awful" as a judgment of *craftsmanship* is not
supported by the evidence. The code that is actually written is mostly
competent — parameterized SQL throughout (no injection found), honest inline
comments that document real bugs, working crash-recovery, an idempotent
pipeline, non-root containers, and secrets that never reached git.

The accurate one-liner is: **a competently-built prototype mislabeled as a
product, assembled into an incoherent system.** The problem is not bad code —
it's dead code, divergent deployment topologies, missing security, and no
test/observability discipline.

---

## Critical findings (block any real release)

| # | Finding | Evidence |
|---|---------|----------|
| C1 | **Authentication is fake or absent across all three layers.** Frontend accepts *any* email/password, mints an **unsigned base64 token** in the browser, and never sends it anywhere. Both backends have **no auth check on any endpoint**. | `LAI-UI/.../AuthContext.tsx:55-82`, `utils/jwt.ts:26-36`; `micro-services/ddiq_report.py:1655-2463` (no `Depends`); `LAI/src/lai/api/serve_rag.py:944-1460` |
| C2 | **No tenant isolation — every user sees everyone's data.** `/sessions`, `/reports`, `/documents`, `/report/{id}` return all rows globally. No `user_id` column exists on DDiQ tables. For a legal-DD product handling client contracts, this is a GDPR/confidentiality breach. | `ddiq_report.py:1656-1663, 2208-2257`; `serve_rag.py:1385-1414` |
| C3 | **The live system was broken at audit time.** `lai-backend` was "up" but crashed at startup — it pointed at DNS name `lai_postgres_main`, which didn't exist because Postgres ran as a *host process* on `:5435`. **Status (2026-05-14, later in session): RESOLVED.** `lai_postgres_main`, `lai_embedding`, `lai_analyzer_llm`, `lai_redis` are now up and healthy; `lai-backend` returns `/health` 200; `serve_rag` is running on `:18000`. Only `lai-user-doc-processor` remains unhealthy. The underlying *cause* (deployment/topology drift, H2) still applies. | `docker logs lai-backend`: `could not translate host name "lai_postgres_main"` -> `Application startup failed` (initial); current `docker ps` shows the full runtime stack healthy |
| C4 | **A real HuggingFace API token sits in plaintext** in `Docker/inference_engine/.env:11` (`hf_SdUN…`). Mitigant: it's gitignored and **never entered git history** — but it's on a shared multi-project host. **Rotate it.** | `Docker/inference_engine/.env:11`; `git log --all -S` -> empty |

---

## High-severity findings

| # | Finding | Evidence |
|---|---------|----------|
| H1 | **~3,200 LOC of dead code shipped alongside the live path.** *(Initial audit said ~6,000 — recounted directly in `RE_VERIFICATION.md` B2; actual total is 3,157 lines across `api/main.py` 119 + `api/pipeline.py` 128 + `auth/` 168 + `documents/` 634 + `extraction/` 597 + `generation/` 548 + `infra/` 338 + dead `search/*` 625.)* `LAI/src/lai/` has *two* RAG backends. The clean, domain-driven one (`api/main.py` + `search/` + `generation/` + `auth/` + `infra/`) is imported by **nothing** — ops scripts launch only `lai.api.serve_rag`. The README's "Quick Start" even tells you to run `lai.api.main`, which is dead. The working JWT validation code lives here — wired to the dead app. | `start-host.sh:204-207` runs `serve_rag`; `README.md:30` runs dead `main`; `grep lai.api.main` -> only README |
| H2 | **Deployment topology is fragmented and contradictory.** 4+ overlapping Compose stacks describe the same logical system with different models, container names and ports. The documented topology doesn't match the running one (half-Docker / half-host-process). This divergence *is* the direct cause of C3. | `LAI/docker-compose.yml`, `Docker/services/`, `Docker/database/*`, `micro-services/docker-compose.yml` |
| H3 | **Zero automated tests anywhere.** `tests/unit`, `tests/integration`, `tests/e2e` are empty directories — backend, pipeline, microservice, and frontend all have none. For a legal-AI product this is a serious gap. | `find LAI/tests -name '*.py'` -> 0 files |
| H4 | **God-files.** `ddiq_report.py` = 2,463 lines / ~58 functions / **12 router endpoints** / 9 inline table DDLs mixing DB, network clients, LLM orchestration, parsing, routing. *(Initial audit said 14; recounted directly — `grep -c '@router\.'` returns 12. See `RE_VERIFICATION.md` B3.)* `serve_rag.py` = 1,481 lines. `ReportDownloadPanel.tsx` = 2,245 lines. | file sizes |
| H5 | **No retries/backoff on any LLM or external HTTP call.** Pipeline steps 3/4/5 silently drop chunks on a transient vLLM hiccup; step 6 `break`s and *aborts a ~48h run* on the first error. ALKIS cadastral lookups (`alkis_query_parcels`) don't retry the documented HTTP 530 errors. | `classify.py:96`, `enrich.py:81`, `cli.py:1046`; `ddiq_report.py:617-676` |
| H6 | **Internal services bind `0.0.0.0` on a shared host.** vLLM (no auth), Neo4j, and the backends are reachable by anything on the host/VPN. The Compose *defaults* are correctly `127.0.0.1`; an `.env` override (`BACKEND_BIND_HOST=0.0.0.0`, `LAI_BIND_HOST=0.0.0.0`) defeats them. | `micro-services/.env`, `start-host.sh:52`; live `docker ps` |

---

## Medium-severity (selected)

- **No image/dependency pinning** — `vllm/vllm-openai:latest`, `prometheus:latest`,
  `grafana:latest`, `minio:latest`; the *deployed* microservice uses unpinned `>=`
  requirements with no lockfile (the core `LAI/` package does ship `uv.lock`).
  `vllm:latest` has already broken a CLI flag once.
- **Monitoring is configured but not deployed** — `Docker/monitoring/` has a valid
  `prometheus.yml`, but no Prometheus/Grafana container runs and scrape targets
  don't match container names. Observability is implied, not real.
- **Hardcoded default credentials** — `lai_test_password_2024`,
  `superStrongPassword123!`, `CHANGE-ME-IN-PRODUCTION` as fallback defaults in
  ~9 Compose files and `core/config.py:38,84,273`. If `.env` is ever missing, the
  app runs on known creds instead of failing.
- **DDiQ async pipeline runs ~20+ writes outside a transaction**; the dedup check
  has a TOCTOU race (the `request_fingerprint` index is not `UNIQUE`) — two
  identical concurrent requests both queue full 30–60 min pipelines.
- **Synthetic training data: 15.8% of cited statutes/clauses are fabricated** by
  the 72B teacher. *Credit where due:* the team measured this, disclosed it
  honestly, and shelved fine-tuning — the correct call. The gap is that the
  audit is a manual archived script, not a gate inside generation.
- **`data_processing/` (top-level) is dead legacy code** — not git-tracked,
  imported by nothing, superseded by `LAI/src/lai/pipeline/`. Confusion
  liability; should be archived.
- **CORS `allow_origins=["*"]`** on both backends, paired with no auth.

---

## What's actually good (the honest counterweight)

This is not a codebase written by people who don't know what they're doing:

- **No SQL injection** — every query across both backends is parameterized,
  including dynamically-built `IN (...)` clauses.
- **Honest engineering culture in the code** — inline comments document *why*,
  and repeatedly document real bugs that were hit and fixed (OCR umlaut
  corruption, dropped-window clause bug, VDR-contract conflation). A genuinely
  awful codebase doesn't do this.
- **Real crash-recovery** — DDiQ checkpoints report state after every phase;
  `reap_orphans()` cleans dead jobs on startup. The pipeline is genuinely
  idempotent and resumable (keyset pagination, dual-write embedding backups).
- **The pipeline's README claims check out** — `--dry-run`, two-stage SIGINT
  handling, idempotency are all real, not aspirational.
- **Frontend craftsmanship is above average** — `strict` TypeScript, **zero
  `any`** across ~14k LOC, no XSS sinks, defensive API clients that distinguish
  "not-found" from "unreachable", correct polling cleanup.
- **Secrets never reached git**; `.gitignore` coverage is thorough; the
  microservice Dockerfile runs non-root and fails closed on a missing DB
  password.

---

## On the existing `LAI_*.md` analysis docs

They are **directionally correct but imprecise and somewhat inflated in tone**
(consultant-style "business impact", confident time estimates). Spot-checks:
the "max_tokens 4096->1024 at line 504" claim — line 504 is actually `llm_call`
with default `2048`; the `4096` is at lines 517/521. "pool_max_size at
config.py line 37" — it's line 40. Treat them as a starting point, not ground
truth.

---

## Recommended priority order

1. **Real auth + tenant scoping** (C1, C2) — the single thing blocking any
   external use. The team has it scoped in `TODO.md`.
2. **Fix the live deployment** (C3) + **pick one deployment model** (H2) —
   decide all-Docker or all-host and make it authoritative.
3. **Rotate the HF token** (C4).
4. **Delete the dead RAG backend** (H1) — or migrate `serve_rag` onto it;
   shipping both misleads everyone.
5. **Lock down network binds** (H6), **pin images/deps** (M).
6. **Add tests + retries** (H3, H5) — start with the pure functions (chunkers,
   splitters, citation verifier) where it's cheap.
7. Then the structural debt: split the god-files.

None of this is a rewrite. The bones are sound; the system needs **security,
coherence, and discipline** — not replacement.
