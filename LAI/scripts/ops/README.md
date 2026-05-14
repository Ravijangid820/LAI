# LAI Ops — copy-paste command index

Mobile-friendly catalogue of operational commands for running, resuming, and
inspecting the LAI runtime + data pipeline. Each block is self-contained: copy
the whole block into a terminal (or paste into an SSH session from your phone)
and it runs.

The actual scripts live alongside this README in `LAI/ops/` and in
`LAI/scripts/`. New ops scripts go in `LAI/ops/`; legacy ones still live in
`scripts/`. Both are referenced below by absolute path.

> Always run from the LAI repo root or use absolute paths — the scripts use
> relative paths internally to find `processed/`, `.venv/`, etc.

---

## Resume the long pipeline runs

These take hours to days; design is "kick off, close terminal, come back later".

### Step 6 — embeddings → child_embeddings (Qwen3-Embedding-8B)

```bash
# Resume from where it stopped (skips already-embedded child_chunks):
bash /data/projects/lai/LAI/ops/resume_step6.sh

# Show progress without starting anything:
bash /data/projects/lai/LAI/ops/resume_step6.sh --status

# Stop Step 6 only, keep the embedding container running:
bash /data/projects/lai/LAI/ops/resume_step6.sh --stop

# Stop Step 6 AND the embedding container:
bash /data/projects/lai/LAI/ops/resume_step6.sh --stop-all
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
bash /data/projects/lai/LAI/scripts/resume_step5.sh           # start / resume
bash /data/projects/lai/LAI/scripts/resume_step5.sh --status  # progress check
bash /data/projects/lai/LAI/scripts/resume_step5.sh --stop    # stop generation only
bash /data/projects/lai/LAI/scripts/resume_step5.sh --stop-all # stop + container
```

> **GPU contention warning.** Both Step 5 and Step 6 want a GPU. If the
> analyzer container `lai_analyzer_llm` is also up (it's needed for chat +
> DDiQ), GPU 0 may not have headroom for Step 6's Qwen3-Embedding-8B. Check
> `nvidia-smi` first — pause the chat/DDiQ services if you want a clean run.

---

## LAI runtime (chat + DDiQ + UI)

```bash
# Start everything (Docker services + serve_rag + Vite UI):
bash /data/projects/lai/LAI/scripts/start.sh

# Stop everything:
bash /data/projects/lai/LAI/scripts/stop.sh

# Stop only host processes (serve_rag + Vite), keep Docker:
bash /data/projects/lai/LAI/scripts/stop.sh --keep-docker

# Stop only Docker, keep host processes:
bash /data/projects/lai/LAI/scripts/stop.sh --keep-host

# Health snapshot:
bash /data/projects/lai/LAI/scripts/status.sh
```

> **Restart caveat.** When restarting `serve_rag` manually (without `start.sh`),
> always pass the env vars or it'll bind to `127.0.0.1` and your LAN browser
> sees "Failed to fetch":
>
> ```bash
> LAI_BIND_HOST=0.0.0.0 CUDA_VISIBLE_DEVICES=1 \
>   /data/projects/lai/LAI/.venv/bin/python \
>   -m lai.api.serve_rag --host 0.0.0.0 --port 18000 \
>   > /data/projects/lai/LAI/logs/tmp/serve_rag.log 2>&1 &
> ```

---

## Quick smoke tests

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

- 2026-04-30 — `ops/resume_step6.sh` written (resume embeddings, 16.6% done at last check, ~41.6 M child chunks remaining).
- 2026-04-30 — `LAI-UI/` directory rename (was `lai-ui/`); all references in scripts + docs flipped uppercase.
- 2026-04-30 — `serve_rag` LAN bind hardened — restart pattern now in `feedback_serve_rag_restart` memory.
- 2026-04-30 — Frontend chat-history rehydration fix; clicking a sidebar conversation now actually loads its messages.
- 2026-04-29 — Mini DDiQ smoke test (1 doc, 1h 02m); surfaced the empty-LLM-content + Pydantic-null-fallback bugs which are now fixed.
