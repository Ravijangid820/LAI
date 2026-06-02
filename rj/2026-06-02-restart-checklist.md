# Pre-restart checklist — serve_rag + LAI-UI for 2026-06-02 changes

**Date:** 2026-06-02 · **Owner:** rj · **Status:** READY TO APPLY

The following changes are on `origin/develop` but NOT yet live on the
host. They land on the next `restart_serve_rag.sh` (serve_rag) /
Vercel auto-roll (LAI-UI).

## LAI — needs `restart_serve_rag.sh`

| Commit | Change | Visible effect |
|---|---|---|
| `a43b440` | Persistence RLock fix | Closes intermittent 500s on `GET /sessions/{id}/documents` |
| `0f4ce4d` | UI_META → chat router | "was kann ich hier tun?" no longer routes to RAG → no random fraud-forum content |
| `11975c5` | UI_META → contract injection skip | Meta question on a doc-session no longer pulls 8k chars of contract |
| `e84241f` | German language detector fix | "was kannst du …" now correctly detected as German → answered in German |
| `3be15a3` | BM25 default flipped v1→v5 (DE-stopword filter) | ~14 % BM25 latency win (-398 ms / query), Recall@30 unchanged |

**How to apply:**

```bash
cd /data/projects/lai/LAI && ./scripts/ops/restart_serve_rag.sh
# ~5 min cold start (reranker + LLM warmup)
# Verify with: curl -s http://localhost:18000/health
# Smoke test:   ./scripts/ops/smoke_test.py --report
```

## LAI-UI — Vercel auto-rolls on push

Already on `origin/develop` (`5f8f311`); Vercel rolls automatically.
Verify rollout at the user-facing URL → check:

* `/dashboard/admin/audit` admin view renders (audit-log table)
* `/eval` public route renders (vm-9 lawyer-blind A/B)
* The IngestionStatusToast appears bottom-right when a session is
  mid-ingest (cross-reload visibility)
* DOCX export uses German labels + firm-letterhead placeholder
* DDiQ report progress labels are human-readable
* On report completion, the *"Your report is ready"* toast fires

## After restart — verify the 3 mode-router fixes manually

A 60-second smoke check that the new routing actually works in the
real LLM path:

1. Log in to the live UI.
2. Send chat: **"was kann ich hier tun?"** → expect a short
   conversational answer about the app's capabilities, NOT a legal
   lecture with `[C-n]` citations.
3. Upload any short PDF → send: **"gehst du semantisch vor?"** →
   expect a short meta-answer about how the assistant works, NOT a
   document-grounded "no info found" answer.
4. Send chat: **"was kannst du hier im datenraum erkennen?"** →
   expect a GERMAN answer (not English).

If any of these fail, the routing change didn't land — check
`logs/host/serve_rag.log` for the active worker process and confirm
it's the post-restart PID.

## What's NOT in this restart

* HNSW `ef_search` config — sweep proved no hybrid lift; staying at
  100. No change.
* Phase 2 of mode-router (embedding-based intent classifier) — only
  built if Phase 1 turns out brittle in pilot use.
