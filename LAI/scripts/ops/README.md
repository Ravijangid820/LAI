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

## Disaster recovery — `lai_db` backup + restore

Nightly logical dump of the irreplaceable user-content tables; the 901 GB
legal corpus is **not** dumped (reproducible from MinIO via the v2
pipeline). Rationale + per-table breakdown in
[`rj/blueprint/2026-06-10-dr-runbook.md`](../../../rj/blueprint/2026-06-10-dr-runbook.md).

### What gets backed up

| Category | Tables | Mechanism | RPO | RTO |
|---|---|---|---|---|
| Irreplaceable user content | `audit_log` (EU AI Act Art. 12), `users`, `refresh_tokens`, `password_reset_tokens`, `organizations`, `org_invitations`, `projects`, `project_members`, `conversations`, `messages`, `matter_chunks`, `ddiq_*` (all 11 ddiq tables) | nightly `pg_dump -Fc` → `user_data_YYYY-MM-DD.dump` | **24 h** | **~10 min** |
| Schema / DDL | full DB structure | nightly `pg_dump --schema-only --no-tablespaces` → `schema_YYYY-MM-DD.sql.gz` | 24 h | ~5 min |
| Legal corpus (901 GB) | `corpus_parent_chunks`, `corpus_child_chunks`, `corpus_migration_state`, `statute_feed_state` | **NOT backed up — reproducible** from MinIO `lai-raw` via `lai.pipeline.cli step1..step6` + `statute_feed.sh --full` | **0 on raw** in MinIO | **~50 h** rebuild |

### Where

```
/data/projects/lai/LAI/data/postgres-backups/
  daily/    schema_YYYY-MM-DD.sql.gz   + user_data_YYYY-MM-DD.dump   (14-day rolling)
  weekly/   same shape, copied every Sunday                          (35-day rolling)
  monthly/  same shape, copied on the 1st                            (kept indefinitely)
```

Permissions: `0640 rj:ks_admin` — owner write, group read for restore,
no world access (dumps contain audit-log identity events + user emails).

### Run a backup manually (or kick the daily early)

```bash
bash /data/projects/lai/LAI/scripts/ops/backup_postgres.sh
# Logs to stdout (or to logs/host/backup_postgres.log via the cron).
# Wall-time: ~1.5 min, output ~520 MB (mostly matter_chunks embeddings).
```

### Restore — single copy-paste block

Use this verbatim during an incident. Substitute the target date.
**This assumes you've already created an empty `lai_db_test` (or fresh
`lai_db`) on the target Postgres.** For a full host restore (production
DB gone), bring up a fresh `lai_postgres_main` first via
`docker compose -f Docker/database/pgvector/docker-compose.yml up -d`.

```bash
DATE=2026-06-10
TARGET_CONT=lai_postgres_main   # or scratch container name
TARGET_DB=lai_db                # or lai_db_test for rehearsal

# 1. restore schema (DDL)
zcat /data/projects/lai/LAI/data/postgres-backups/daily/schema_${DATE}.sql.gz \
  | docker exec -i "${TARGET_CONT}" psql -U lai_user -d "${TARGET_DB}" -v ON_ERROR_STOP=1

# 2. restore user data
cat /data/projects/lai/LAI/data/postgres-backups/daily/user_data_${DATE}.dump \
  | docker exec -i "${TARGET_CONT}" pg_restore -U lai_user -d "${TARGET_DB}" \
      --data-only --no-owner --no-privileges

# 3. verify row counts
docker exec "${TARGET_CONT}" psql -U lai_user -d "${TARGET_DB}" -c "
SELECT 'audit_log' AS t, count(*) FROM audit_log
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'ddiq_reports', count(*) FROM ddiq_reports
UNION ALL SELECT 'matter_chunks', count(*) FROM matter_chunks;"

# 4. rebuild corpus (only if corpus tables are empty too)
# bash /data/projects/lai/LAI/scripts/ops/statute_feed.sh --full
# python -m lai.pipeline.cli step1   # → step6 for VDR/library corpus
```

### Rehearsal — verify a dump restores cleanly to a scratch DB

```bash
# Start ephemeral pg on tmpfs (no host port; auto-cleans on docker rm)
docker run -d --name lai_pg_scratch_5499 --network lai_network \
  -e POSTGRES_DB=lai_db_test -e POSTGRES_USER=lai_user -e POSTGRES_PASSWORD=test \
  --tmpfs /var/lib/postgresql/data:rw,size=2g \
  pgvector/pgvector:pg16

# Wait for ready (~2s):
until docker exec lai_pg_scratch_5499 pg_isready -U lai_user -d lai_db_test >/dev/null 2>&1; do sleep 1; done

# Then run the restore block above with TARGET_CONT=lai_pg_scratch_5499 / TARGET_DB=lai_db_test
# Tear down when done:
docker stop lai_pg_scratch_5499 && docker rm lai_pg_scratch_5499
```

**Last verified rehearsal:** 2026-06-10 — all 9 sample tables matched
source row counts exactly (audit_log 465, users 25, ddiq_reports 46,
ddiq_documents 56, matter_chunks 37,731, conversations 0, messages 0,
organizations 2, projects 0); 0 errors, 0 warnings. Schema restore:
173 lines; user-data restore: 26 archive entries; total wall ~3 minutes.

### Cron (already installed in rj's crontab 2026-06-10)

```bash
30 2 * * *  bash /data/projects/lai/LAI/scripts/ops/backup_postgres.sh \
  >> /data/projects/lai/LAI/logs/host/backup_postgres.log 2>&1
```

Runs at 02:30 local (off-peak; statute_feed `--mapped` runs at 03:00).
Off-host shipping is **not yet wired** — flagged as deferred work in the
blueprint. For now, a filesystem-level loss of `/data/nvme1n1p1` would
take both the live DB and its backups together.

### Honest gaps (documented, not yet closed)

1. **Same-filesystem risk.** Backups live next to the live DB. RAID
   loss or `rm -rf /data` kills both. Mitigation: rsync to S3 or a
   second host. Deferred to post-pilot.
2. **No PITR.** Daily snapshots only; up to 24 h of user data can be
   lost. WAL archiving + a replica is the right tool; out of scope.
3. **No GPG encryption.** Dumps are `0640` on a project-private
   filesystem. Encrypt before shipping off-host.
4. **No automatic restore rehearsal.** Today's rehearsal was manual.
   Add a weekly job if a pilot firm asks for it.

---

## Email deliverability — Brevo + DNS setup (Phase 4.5.4)

The transactional-mail stack is wired via [`lai.api.email`](../../src/lai/api/email.py)
(Brevo · 4 templates: password-reset, org-invite, report-ready,
report-failed). For pilot-firm-grade deliverability the sender must
be a domain we control, with SPF + DKIM + DMARC records that authorise
Brevo. Today (2026-06-10) sender is a personal gmail address and base
URL is a LAN IP — both are broken; see
[`rj/blueprint/2026-06-10-email-deliverability.md`](../../../rj/blueprint/2026-06-10-email-deliverability.md).

### Setup steps (in order; ~1 day end-to-end mostly waiting for DNS)

1. **Decide subdomain** — recommended `lai.blockland.ae` (parent domain
   already owned, MX = Outlook 365, SPF locked to Outlook). Subdomain
   keeps Brevo IPs isolated from the parent's strict SPF.
2. **Brevo console** (whoever has the Brevo login) → Senders, Domains
   → Domains → "Add a domain" → enter the subdomain.
3. **Brevo emits 2 DKIM CNAME targets** specific to your Brevo account.
   Copy them.
4. **DNS host for `blockland.ae`** (IT) → add four records under the
   chosen subdomain:

   | Type | Host | Value |
   |---|---|---|
   | TXT | `lai.blockland.ae` | `v=spf1 include:spf.brevo.com -all` |
   | CNAME | `mail._domainkey.lai.blockland.ae` | (from Brevo console, step 3) |
   | CNAME | `mail2._domainkey.lai.blockland.ae` | (from Brevo console, step 3) |
   | TXT | `_dmarc.lai.blockland.ae` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@blockland.ae; ruf=mailto:postmaster@blockland.ae; fo=1; adkim=r; aspf=r` |

   **Do NOT** touch the existing `blockland.ae` SPF or add a parent
   `_dmarc.blockland.ae` without coordinating with IT — parent SPF is
   `-all`-locked for Outlook 365 and a careless DMARC could quarantine
   real corporate mail.

5. **Wait for propagation** — 1-2 h typical, up to 24 h. Brevo console
   shows `Domain authenticated ✓` when ready.
6. **Update [`.env.auth`](../../.env.auth)** on the host:
   ```bash
   LAI_EMAIL_SENDER_EMAIL=no-reply@lai.blockland.ae
   LAI_EMAIL_PUBLIC_APP_BASE_URL=https://lai.blockland.ae   # or wherever the UI lives
   ```
   Then restart serve_rag:
   ```bash
   bash scripts/ops/restart_serve_rag.sh
   ```
7. **Send the test mails** — dry-run first to confirm rendering, then
   `--yes` to fire:
   ```bash
   cd /data/projects/lai/LAI
   set -a && . ./.env.auth && set +a   # load LAI_EMAIL_* from .env.auth

   # Dry-run — shows what would be sent
   .venv/bin/python scripts/ops/email_deliverability_test.py \
       --to ravi@blockland.ae \
       --to harsh@<google-workspace-strict-dmarc-host> \
       --to <some-account>@gmx.de \
       --to <some-account>@web.de \
       --to <some-account>@<your-custom-domain>

   # Actually fire (sends 4 templates × N recipients):
   .venv/bin/python scripts/ops/email_deliverability_test.py \
       --to <addr> --to <addr> ... --yes
   ```
   The script refuses to run if sender is a freemail address or
   base URL is an RFC1918 IP — same guard the production code path
   should have. Output includes Brevo's `messageId` per send + a
   checklist for INBOX-vs-SPAM verification.

8. **Bonus: mail-tester.com** — for an objective 10/10 score, send one
   of the templates to a `*@mail-tester.com` address it generates
   for you and grab the result URL.

### Honest gaps not closed by this work

- No bounce / complaint webhook syncing back to LAI DB — Brevo records
  bounces in its dashboard but a permanently-bouncing user keeps getting
  invite mails forever. Deferred (would need a new `email_events` table).
- No DMARC `rua=` report parsing — reports go to
  `postmaster@blockland.ae` but nobody is reading them. Use
  dmarcian.com / Postmark free tier when traffic grows.
- All 4 templates are English. Pilot firms will want German. Post-pilot.

### Verify current sender / base URL config

```bash
grep -iE '^LAI_EMAIL_(SENDER|PUBLIC|ENABLED)' LAI/.env.auth
```

If sender ends in `@gmail.com` / `@web.de` / `@gmx.de` / etc., or if
base URL contains `192.168.` / `10.` / `localhost`, the test harness
will refuse to send.

---

## Push access — getting set up (Phase 4.5.5)

Both repos are at `Ravijangid820/LAI` and `Ravijangid820/LAI-UI` — rj's
*personal* GitHub account. For anyone else to push from the shared
workstation, their personal GitHub user must be a collaborator AND
their SSH key on the box must be attached to that personal account.
Full procedure (member-side, rj-side, verification, troubleshooting,
offboarding) in **[`LAI/docs/team_access/README.md`](../../docs/team_access/README.md)**.

**Symptom of an unconfigured team member:**
```
ERROR: Permission to Ravijangid820/LAI.git denied to <your-user>.
fatal: Could not read from remote repository.
```

**Quick fix:** ssh into the shared workstation as yourself, then:
```bash
bash /data/projects/lai/LAI/scripts/ops/team_access_bootstrap.sh
```
Follow the 3 printed steps (paste pubkey to your personal GitHub →
Settings → SSH keys, send rj your GH username if not already
invited, accept the 2 invites + test push). ~5 min.

Design context (why option (a) per-collaborator now, option (b) org
transfer deferred) in
[`rj/blueprint/2026-06-10-push-access-spof.md`](../../../rj/blueprint/2026-06-10-push-access-spof.md).

---

## Recent ops history (rolling, last 5)

- 2026-06-10 — Phase 4.5.5 push-access SPOF decision brief landed (rj-DR-3): blueprint comparing (a) per-collaborator additions vs (b) shared-org transfer. Recommended (b) — only real cost is the boss decision on org name + admin set, and the existing TAI-Agent identity is almost certainly already a GitHub org (owns `DS_Platform` at the `owner/repo` URL pattern), which would collapse (b) to a 2-step transfer because team members already have org access. Verified state: both repos at `Ravijangid820`, LAI has 0 CI workflows, LAI-UI has 1 (`ci.yml`, no hardcoded owner — survives transfer unchanged), Vercel relinks via dashboard not config, 7 active files reference `Ravijangid820/` and would need a 1-line sed during the flip. GitHub auto-redirect covers the historical doc references. Engineering side cannot do the transfer unilaterally (one-way operation pending org decision); README pointer added so future "I can't push" hits get the link. Blueprint: [`rj/blueprint/2026-06-10-push-access-spof.md`](../../../rj/blueprint/2026-06-10-push-access-spof.md). PROGRESS_V2 4.5.5 stays at 🔄 partial.
- 2026-06-10 — Phase 4.5.4 email-deliverability engineering side landed (rj-DR-2): caught that `LAI_EMAIL_SENDER_EMAIL` is a personal gmail address (pre-determined spam at every corporate receiver — Brevo can't DKIM-align with gmail.com) and `LAI_EMAIL_PUBLIC_APP_BASE_URL` is a LAN IP (call-to-action link unreachable for external recipients). Shipped: `scripts/ops/email_deliverability_test.py` (dry-run-by-default test harness, 4 templates × N recipients, guards against freemail-sender + RFC1918-base-URL, captures Brevo `messageId` + MX provider per send, prints inbox-vs-spam checklist); Brevo+DNS setup runbook in this README; full blueprint at [`rj/blueprint/2026-06-10-email-deliverability.md`](../../../rj/blueprint/2026-06-10-email-deliverability.md) with the exact TXT/CNAME records to plant on `blockland.ae` for the recommended `lai.blockland.ae` subdomain. **Blocked on** DNS-host access at blockland.ae (boss/IT) + Brevo console domain add. PROGRESS_V2 4.5.4 stays at 🔄 partial.
- 2026-06-10 — Phase 4.5.3 DR runbook landed (rj-DR-1): `scripts/ops/backup_postgres.sh` nightly-dumps the irreplaceable user-content tables (~520 MB compressed; `audit_log`, `users`, `ddiq_*`, `matter_chunks`, plus schema-only DDL of the whole DB) into `LAI/data/postgres-backups/{daily,weekly,monthly}/` with 14d/35d/∞ retention. Corpus (901 GB) deliberately not dumped — reproducible from MinIO via Step 1-6 + statute_feed. Cron `30 2 * * *` installed; first scratch-container restore rehearsal matched all 9 sample row counts exactly with zero errors. Honest gaps documented (same-filesystem risk, no PITR, no GPG, no auto-rehearsal). Blueprint: [`rj/blueprint/2026-06-10-dr-runbook.md`](../../../rj/blueprint/2026-06-10-dr-runbook.md). Closes [`PROGRESS_V2.md` row 4.5.3](../../../harsh/PROGRESS_V2.md).
- 2026-05-30 — Added `scripts/ops/audit_export.py` (vm-4 / ROADMAP 2.3 follow-up): CSV/JSON bulk export of `audit_log` with date / action / org / user filters, plus a `--purge-older-than DAYS` retention path that requires `--yes` to actually DELETE (dry-run by default). Reads via `audit.query`; purge issues a bound-parameter `DELETE`.
- 2026-05-30 — `scripts/ops/smoke_test.py` gained an optional `--report` leg (vm-3 / ROADMAP 1.2 follow-up): POSTs a DDiQ async report against `LAI_SMOKE_DDIQ_DOC_ID` and polls `/status` until `done` or budget, so the smoke test now catches DDiQ-pipeline regressions too (exit 7). `LAI_SMOKE_USER`/`LAI_SMOKE_PASS` accepted as aliases for the EMAIL/PASSWORD pair. Cron line documented but **not installed** — shared-box change, awaits rj's OK.
- 2026-05-29 — Added `scripts/ops/smoke_test.py` (vm-1 / ROADMAP 1.2): post-restart guard that fails loudly when a query is slow or the reranker is on CPU.
- 2026-05-21 — Documented `scripts/ops/restart_serve_rag.sh` (Sahid's S-5 script) as the canonical safe restart; removed a duplicate `restart-serve_rag.sh` that had been added by mistake.
- 2026-04-30 — `ops/resume_step6.sh` written (resume embeddings, 16.6% done at last check, ~41.6 M child chunks remaining).
- 2026-04-30 — `LAI-UI/` directory rename (was `lai-ui/`); all references in scripts + docs flipped uppercase.
- 2026-04-30 — `serve_rag` LAN bind hardened — restart pattern now in `feedback_serve_rag_restart` memory.
- 2026-04-30 — Frontend chat-history rehydration fix; clicking a sidebar conversation now actually loads its messages.
- 2026-04-29 — Mini DDiQ smoke test (1 doc, 1h 02m); surfaced the empty-LLM-content + Pydantic-null-fallback bugs which are now fixed.
