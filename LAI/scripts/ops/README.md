# LAI Ops — copy-paste command index

Mobile-friendly catalogue of operational commands for running, resuming, and
inspecting the LAI runtime + data pipeline. Each block is self-contained: copy
the whole block into a terminal (or paste into an SSH session from your phone)
and it runs.

The actual scripts live alongside this README in `LAI/scripts/ops/` (the v2
restructure consolidated the old top-level `LAI/ops/` and loose `LAI/scripts/`
entry points here). Referenced below by absolute path.

> Always run from the LAI repo root or use absolute paths — the scripts use
> relative paths internally to find `processed/`, `.venv/`, etc.

---

## Resume the long pipeline runs

These take hours to days; design is "kick off, close terminal, come back later".

### Step 6 — embeddings → child_embeddings (Qwen3-Embedding-8B)

```bash
# Resume from where it stopped (skips already-embedded child_chunks):
bash /data/projects/lai/LAI/scripts/ops/resume_step6.sh

# Show progress without starting anything:
bash /data/projects/lai/LAI/scripts/ops/resume_step6.sh --status

# Stop Step 6 only, keep the embedding container running:
bash /data/projects/lai/LAI/scripts/ops/resume_step6.sh --stop

# Stop Step 6 AND the embedding container:
bash /data/projects/lai/LAI/scripts/ops/resume_step6.sh --stop-all
```

What "resume" means: an automatic SQL filter
(`WHERE NOT EXISTS (SELECT 1 FROM child_embeddings e WHERE e.child_id = c.id)`)
skips child_chunks that already have embeddings, so re-running keeps going from
the current cursor.

Tail the log:
```bash
ls -t /data/projects/lai/LAI/logs/pipeline/step6_resume_*.log | head -1 | xargs tail -f
```

### Step 5 — synthetic Q&A generation (Qwen2.5-72B-AWQ)

```bash
bash /data/projects/lai/LAI/scripts/ops/resume_step5.sh           # start / resume
bash /data/projects/lai/LAI/scripts/ops/resume_step5.sh --status  # progress check
bash /data/projects/lai/LAI/scripts/ops/resume_step5.sh --stop    # stop generation only
bash /data/projects/lai/LAI/scripts/ops/resume_step5.sh --stop-all # stop + container
```

> **GPU contention warning.** Both Step 5 and Step 6 want a GPU. If the
> analyzer container `lai_analyzer_llm` is also up (it's needed for chat +
> DDiQ), GPU 0 may not have headroom for Step 6's Qwen3-Embedding-8B. Check
> `nvidia-smi` first — pause the chat/DDiQ services if you want a clean run.

---

## Statute feed (Phase 4.3 — gesetze-im-internet.de → corpus_*)

Keeps the German federal-statute portion of the corpus current. All modes go
through `scripts/ops/statute_feed.sh`, which auto-sources
`LAI/micro-services/.env` (DB password), writes logs under
`LAI/logs/pipeline/`, and PID-tracks background jobs in
`LAI/processed/statute_feed.pid`.

```bash
# Current state (counts per domain, last_seen range):
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --status

# Daily — refresh the 29 wind-relevant laws (foreground; ~12 min):
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --mapped

# Weekly — full TOC sweep (background ~43 h; survives SSH disconnect):
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --full
# Smoke-test variant — only the first 50 laws:
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --full --limit 50

# Tail the latest log / stop the background --full / prune dead laws:
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --tail
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --stop
bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --prune    # default 7-day window
```

Idempotent: `statute_feed_state.content_hash` makes re-runs cheap — unchanged
laws skip in ~1 s. The TOC is fetched once per backfill (one HTTP client
shared across the loop).

Recommended cron (install on the same box as `lai-backend`):
```bash
# Daily 03:00 — mapped backfill (~12 min)
0 3 * * *  bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --mapped \
  >> /data/projects/lai/LAI/logs/pipeline/statute_feed_cron_mapped.log 2>&1

# Sunday 22:00 — full TOC sweep (background; finishes by Tuesday)
0 22 * * 0  bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --full

# Wednesday 02:00 — prune laws gone from the TOC for ≥ 7 days
0 2 * * 3  bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --prune \
  >> /data/projects/lai/LAI/logs/pipeline/statute_feed_cron_prune.log 2>&1
```

> **Coordinate before installing cron** — the embedding server on `:8003`
> is shared with `serve_rag` /query. The schedule above puts the heavy
> full sweep in the quiet weekend window.

---

## LAI runtime (chat + DDiQ + UI)

```bash
# Start everything (Docker services + serve_rag + Vite UI):
bash /data/projects/lai/LAI/scripts/ops/start.sh

# Stop everything:
bash /data/projects/lai/LAI/scripts/ops/stop.sh

# Stop only host processes (serve_rag + Vite), keep Docker:
bash /data/projects/lai/LAI/scripts/ops/stop.sh --keep-docker

# Stop only Docker, keep host processes:
bash /data/projects/lai/LAI/scripts/ops/stop.sh --keep-host

# Health snapshot:
bash /data/projects/lai/LAI/scripts/ops/status.sh
```

### Restart just serve_rag (the chat backend)

```bash
bash /data/projects/lai/LAI/scripts/ops/restart_serve_rag.sh            # restart + wait for ready
bash /data/projects/lai/LAI/scripts/ops/restart_serve_rag.sh --status   # PID + /health
bash /data/projects/lai/LAI/scripts/ops/restart_serve_rag.sh --stop     # stop only
bash /data/projects/lai/LAI/scripts/ops/restart_serve_rag.sh --no-wait  # restart, skip health gate
```

The safe one-command restart. It SIGTERMs serve_rag and **waits for the process
to fully exit** (which is when its GPU VRAM is released — so the relaunch can't
CUDA-OOM), escalates to SIGKILL if it overstays, relaunches detached
(`setsid + nohup`, SSH-disconnect-proof) after sourcing `.env.auth` + DB/CORS
env, then polls `/health` until `loaded:true` AND `retrieval_ready:true`. It
does **not** touch Docker, the Vite UI, or the running pipeline / migration jobs.

> If you ever launch serve_rag by hand instead, you must pass the env or it
> binds to `127.0.0.1` and LAN browsers see "Failed to fetch":
> `LAI_BIND_HOST=0.0.0.0 CUDA_VISIBLE_DEVICES=1 .venv/bin/python -m lai.api.serve_rag --port 18000`
> — but prefer the restart script above.

### Auto-restart serve_rag via systemd (recommended; install once by ks_admin)

Origin: a clean operator shutdown on 2026-05-31 21:13 left serve_rag down for
~20 h until the BM25-revert restart picked it back up the next afternoon. The
hourly smoke cron catches future outages within an hour, but a supervisor
that auto-restarts on failure + at boot is the real fix.

```bash
# One-time install (must run as root — touches /etc/systemd/system):
sudo bash /data/projects/lai/LAI/scripts/ops/systemd/install.sh

# Day-to-day after install:
sudo systemctl status   serve_rag
sudo systemctl restart  serve_rag
sudo systemctl stop     serve_rag
journalctl -u serve_rag -n 100 -f
```

The unit (`scripts/ops/systemd/serve_rag.service`) runs as `rj:lai` with
`LAI_BIND_HOST=0.0.0.0` + `CUDA_VISIBLE_DEVICES=1` baked in, sources
`.env.auth` + `micro-services/.env`, `Restart=on-failure`, `RestartSec=10`,
`WantedBy=multi-user.target` (auto-start at boot). `install.sh` gracefully
stops any existing nohup-launched instance before handing off so the
takeover is one ~20 s window.

> **Cohabitation note:** once the unit is active, `restart_serve_rag.sh`
> still works (its `SIGTERM` triggers `Restart=on-failure`), but the
> standard play becomes `sudo systemctl restart serve_rag`. A
> systemctl-aware mode in the wrapper is a follow-up.

---

## Quick smoke tests

> The `/query`, `/sessions` and most endpoints below now require a Bearer token
> (`Authorization: Bearer <jwt>`). The token-free `curl` snippets in this section
> are illustrative — get a token from `POST /auth/login` first, or use the
> system smoke test, which logs in for you.

### System smoke test (reranker-on-CPU guard)

The canonical check to run **after every `restart_serve_rag.sh`**. It logs in,
sends one RAG query, and fails loudly if the round-trip is slow OR the reranker
fell back to CPU (the boss-test root cause — see ROADMAP 1.2). Stdlib-only, so
any `python3` runs it; exit code names the failure cause (0 pass, 5 slow, 6
reranker-on-CPU, 7 ddiq-report).

```bash
# Credentials via env (or LAI_SMOKE_TOKEN to skip login):
export LAI_SMOKE_EMAIL=ops@yourfirm.de LAI_SMOKE_PASSWORD=...
python3 /data/projects/lai/LAI/scripts/ops/smoke_test.py
```

Add `--report` to also exercise the DDiQ async-report pipeline (catches
report-engine regressions, not just retrieval). It needs a seeded
``ddiq_documents`` row — seed one tiny doc **once** per environment and reuse
the id forever:

```bash
# One-time seed of a 1-page test doc:
DOC_ID=$(curl -s -X POST http://localhost:18001/ddiq/documents/upload \
  -F "file=@/path/to/tiny.pdf" -F "category=smoke" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["document_id"])')
echo "$DOC_ID"   # save this in your ops vault

# Then on every run:
export LAI_SMOKE_DDIQ_DOC_ID=$DOC_ID
python3 /data/projects/lai/LAI/scripts/ops/smoke_test.py --report
```

```bash
# Hourly cron (installed for rj 2026-06-01 — tightened from daily after a
# 20 h silent outage). Sources LAI/.env.smoke.local (gitignored, chmod 600
# with LAI_SMOKE_USER/PASS/DDIQ_DOC_ID) so creds aren't in `crontab -l`.
0 * * * * bash -c "cd /data/projects/lai/LAI && set -a && . ./.env.smoke.local && set +a && (echo \"=== \$(date -Iseconds) ===\"; LAI_SMOKE_MAX_S=60 .venv/bin/python scripts/ops/smoke_test.py) >> logs/host/smoke_test_cron.log 2>&1"
```

Once the systemd unit (above) is in place, the cron can return to daily
(it becomes a canary against the supervisor itself, not the primary
recovery path). Until then, hourly catches outages within an hour.

Tunables (all optional): `LAI_SMOKE_URL` (default `http://localhost:18000`),
`LAI_SMOKE_MAX_S` (latency budget, default 20), `LAI_SMOKE_QUESTION`,
`LAI_SMOKE_FORCE_MODE` (default `rag`), `LAI_SERVE_RAG_LOG`,
`LAI_SMOKE_DDIQ_URL`, `LAI_SMOKE_DDIQ_PRESET` (default `comprehensive`),
`LAI_SMOKE_DDIQ_MAX_S` (default 600), `LAI_SMOKE_DDIQ_POLL_S` (default 10).
`LAI_SMOKE_USER` / `LAI_SMOKE_PASS` are accepted as aliases for the
`_EMAIL`/`_PASSWORD` pair.

### Chat round-trip
```bash
SID=$(python3 -c "import uuid; print(uuid.uuid4())")
curl -s -m 60 -X POST http://localhost:18000/query \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"force_mode\":\"chat\",\"question\":\"Hello in one short sentence.\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("answer",""))'
```

### DDiQ document upload
```bash
curl -s -X POST http://localhost:18001/ddiq/documents/upload \
  -F "file=@/path/to/your.pdf" \
  -F "category=test" | python3 -m json.tool
```

### DDiQ async report (poll until done)
```bash
DOC_ID=...   # from documents/upload above
RID=$(curl -s -X POST http://localhost:18001/ddiq/report/generate/async \
  -H "Content-Type: application/json" \
  -d "{\"document_ids\":[\"$DOC_ID\"],\"preset\":\"comprehensive\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["report_id"])')
echo "report_id: $RID"
watch -n 5 "curl -s http://localhost:18001/ddiq/report/$RID/status | python3 -m json.tool"
```

---

## Audit-log export + retention

`scripts/ops/audit_export.py` is the ops/compliance counterpart to the admin
UI's audit-log viewer (`/dashboard/admin/audit`). It reuses
`lai.common.audit.query` for reads and issues a bound-parameter `DELETE` for
retention (migration 006's `audit_log_no_update` trigger blocks UPDATE but
intentionally allows DELETE under a privileged job). Async, asyncpg-only.

```bash
# CSV export of the last 7 days to a file:
.venv/bin/python scripts/ops/audit_export.py \
  --since 2026-05-23 --format csv --out audit_2026-05.csv

# JSON to stdout, filtered to one action / one org:
.venv/bin/python scripts/ops/audit_export.py \
  --action login --org-id <uuid> --format json

# Dry-run a retention cull (always do this first):
.venv/bin/python scripts/ops/audit_export.py --purge-older-than 365

# Actually delete rows older than 365 days:
.venv/bin/python scripts/ops/audit_export.py --purge-older-than 365 --yes
```

Reads `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` from the
same env the audit writer uses, so a sourced `.env` is enough. Exit codes:
0 ok, 1 config, 2 DB, 3 dry-run notice (`--purge-older-than` without `--yes`).

> **EU AI Act:** Art. 12 sets a *minimum* retention period (6 months). Pick a
> purge cutoff that's longer than your policy, not shorter, and run the
> dry-run first to confirm the count.

---

## Database state queries

### Pipeline progress (SQLite local DB, 331 GB)
```bash
python3 -c "
import sqlite3
con = sqlite3.connect('/data/projects/lai/LAI/processed/pipeline_local.db')
for t in ('parent_chunks','child_chunks','child_embeddings','training_samples','chunk_classifications'):
    n = con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t:25s} {n:>15,}')
"
```

### DDiQ runtime DB (Postgres on :5434)
```bash
docker exec lai_postgres_main psql -U lai_user -d lai_db -c "
SELECT
  (SELECT COUNT(*) FROM ddiq_documents) AS docs,
  (SELECT COUNT(*) FROM ddiq_reports)   AS reports,
  (SELECT COUNT(*) FROM ddiq_reports WHERE status='running') AS reports_running,
  (SELECT COUNT(*) FROM ddiq_reports WHERE status='failed')  AS reports_failed;"
```

### Chat sessions DB (SQLite, host)
```bash
python3 -c "
import sqlite3
con = sqlite3.connect('/data/projects/lai/LAI/processed/sessions.db')
print('sessions:', con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
print('messages:', con.execute('SELECT COUNT(*) FROM messages').fetchone()[0])
"
```

---

## GPU + container snapshot

```bash
nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.free --format=csv
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'lai_|ddiq|backend|embedding|analyzer'
```

---

## Recent ops history (rolling, last 5)

- 2026-05-30 — Added `scripts/ops/audit_export.py` (vm-4 / ROADMAP 2.3 follow-up): CSV/JSON bulk export of `audit_log` with date / action / org / user filters, plus a `--purge-older-than DAYS` retention path that requires `--yes` to actually DELETE (dry-run by default). Reads via `audit.query`; purge issues a bound-parameter `DELETE`.
- 2026-05-30 — `scripts/ops/smoke_test.py` gained an optional `--report` leg (vm-3 / ROADMAP 1.2 follow-up): POSTs a DDiQ async report against `LAI_SMOKE_DDIQ_DOC_ID` and polls `/status` until `done` or budget, so the smoke test now catches DDiQ-pipeline regressions too (exit 7). `LAI_SMOKE_USER`/`LAI_SMOKE_PASS` accepted as aliases for the EMAIL/PASSWORD pair. Cron line documented but **not installed** — shared-box change, awaits rj's OK.
- 2026-05-29 — Added `scripts/ops/smoke_test.py` (vm-1 / ROADMAP 1.2): post-restart guard that fails loudly when a query is slow or the reranker is on CPU.
- 2026-05-21 — Documented `scripts/ops/restart_serve_rag.sh` (Sahid's S-5 script) as the canonical safe restart; removed a duplicate `restart-serve_rag.sh` that had been added by mistake.
- 2026-04-30 — `ops/resume_step6.sh` written (resume embeddings, 16.6% done at last check, ~41.6 M child chunks remaining).
- 2026-04-30 — `LAI-UI/` directory rename (was `lai-ui/`); all references in scripts + docs flipped uppercase.
- 2026-04-30 — `serve_rag` LAN bind hardened — restart pattern now in `feedback_serve_rag_restart` memory.
- 2026-04-30 — Frontend chat-history rehydration fix; clicking a sidebar conversation now actually loads its messages.
- 2026-04-29 — Mini DDiQ smoke test (1 doc, 1h 02m); surfaced the empty-LLM-content + Pydantic-null-fallback bugs which are now fixed.
