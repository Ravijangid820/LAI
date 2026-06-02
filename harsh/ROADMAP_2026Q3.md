# LAI Roadmap — 2026 Q3

**Author:** Engineering (rj)
**Date:** 2026-05-27
**Revised:** 2026-05-28 — verification pass against code, git, live system, and EU-law sources. Corrections marked inline with `[verified 05-28]`.
**Audience:** Engineering team + boss (€6,500/mo stakeholder)
**Replaces:** ad-hoc backlog discussions; supersedes informal sprint notes

This document is the source of truth for what we are building, in what order,
why, and by when. It is written so that (a) the boss can see the plan with
dated milestones, (b) any new engineer can pick up the work, and (c) future
Claude sessions can re-enter context without losing thread.

---

## Executive summary

LAI is technically strong but operationally fragile. Today's session
(2026-05-26 / 2026-05-27) surfaced three classes of issue:

1. **Silent degradation** — on the morning reboot the serve_rag host process
   lost GPU access (`/dev/nvidia*` came back `crw-rw---- root:lai`; the host user
   is not in group `lai` — so NOT strictly "root-only", `[verified 05-28]`).
   PyTorch fell back to CPU silently, the reranker (Qwen3-Reranker-8B) started
   running on CPU, every chat query blocked for 60–180s, and the UI showed the
   cryptic "no data for 60s" error. Health checks were all green. This was the
   cause of the boss-test failure — `[verified 05-28]` confirmed: the chat path
   uses an in-process torch reranker that auto-falls-back to CPU when the host
   process can't see the GPU (`search/eval.py:402`); the :8004 container serves
   only DDiQ, not chat (Phase 0.0b).
2. **Boundary defects** — the upload endpoint accepted 0-byte multiparts
   silently, then Docling 30 seconds later reported "Input document is not
   valid". A real user filing a 267 KB PDF saw a row stuck at "failed" with
   no path to retry.
3. **Pipeline gaps** — same-name uploads created duplicate matter_documents
   rows; failed rows couldn't be retried; DDiQ reports had three known bugs
   (`ampel: null` on findings, `multiParkDetected: false` despite multi-park
   evidence, `parks[].location` taking SH address for a NI park).

All three classes were fixed in code today. `[verified 05-28]` The fixes are
still **uncommitted** (commit them — Phase 0.0) and none are deployed until
`scripts/ops/restart_serve_rag.sh` runs. That restart is gated on the GPU-access
fix (a udev rule putting the serve_rag user in group `lai` — see Phase 0.1, it
does NOT require world-writable `chmod 666`).

The strategic direction the boss received from Claude.ai (foundation model +
specialist agents via knowledge distillation) is correct. What's missing is
the **operational discipline** to deploy and pilot it without it silently
failing.

---

## Phase 0 — Unblock today (NOW; blocks everything else)

| # | Action | Owner | Effort | Status |
|---|---|---|---|---|
| 0.0 | **`[verified 05-28]` COMMIT THE WORK FIRST.** All of today's fixes are UNCOMMITTED working-tree changes (`git status` shows `M` on serve_rag.py, persistence.py, ddiq_report.py, ddiq/llm.py, ddiq/models.py, restart_serve_rag.sh; `upload_tus.py` is untracked `??`). One `git checkout`/`stash`/reboot loses all of it. The earlier "committed locally only" note was wrong. Commit before touching anything else. | rj | 5 min | **NOT DONE — highest risk** |
| 0.0b | **`[verified 05-28]` RESOLVED — the GPU-access fix IS the right fix.** The serve_rag chat path uses an **in-process** torch reranker (`Reranker`, `search/eval.py:350`; instantiated `serve_rag.py:2849`, scored at `:1224` matter / `:3228` corpus). `_pick_device()` (`eval.py:402`) returns `"cpu"` whenever `torch.cuda.is_available()` is False — which is exactly why the log prints `Loading reranker … on cpu` (`eval.py:370`) and queries crawl. serve_rag reads **no** `RERANKER_URL`. The `:8004 lai-test-reranker` container is consumed only by the DDiQ microservice (`api.py:445`, `ddiq/rag.py:125`) and is a **CPU-only** TEI image (`text-embeddings-inference:cpu-1.8`) anyway — not the chat path. So restoring GPU visibility to the serve_rag host process resolves the chat slowness. | rj | done | **RESOLVED** |
| 0.1 | `[verified 05-28]` Send `ks_admin` a udev rule. NOTE: device nodes are `crw-rw---- root:lai`, NOT root-only — group `lai` already has rw. The durable fix is a udev rule setting **group `lai`** + ensuring the serve_rag user is in `lai` and that it survives reboot — NOT `chmod 666`/world-writable. The reranker is on CPU because the host process isn't getting group access at start, not because the device is locked to root. | rj | 5 min | message drafted, awaiting send |
| 0.2 | After `ks_admin` confirms the serve_rag user can open `/dev/nvidia0/1/ctl`: run `bash scripts/ops/restart_serve_rag.sh`. This restarts serve_rag with GPU access AND rebuilds DDiQ backend+worker — and because the rebuild copies the working tree, it bakes in the (now-committed, per 0.0) fixes. | rj | 5 min (script self-completes) | Blocked on 0.1 (0.0b resolved) |
| 0.3 | Verify GPU access: `nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv` returns the 2 GPUs (not "Insufficient Permissions") | rj | 1 min | Blocked on 0.2 |
| 0.4 | Verify reranker on GPU: `grep "Loading reranker" logs/host/serve_rag.log \| tail -3` says `on cuda` (this in-process reranker IS the chat path — confirmed 0.0b). | rj | 1 min | Blocked on 0.2 |
| 0.5 | Smoke-test chat: ask "list documents" on a real session, expect <10s response with citations. | rj | 2 min | Blocked on 0.4 |

**Definition of done for Phase 0:** Work is committed (0.0), the hot rerank
path is known (0.0b), Kristian's next chat query returns in seconds not
timeouts. The boss test stops looking broken.

---

## Phase 1 — Stop the silent failures from recurring (this week)

| # | Action | Owner | Effort | Justification |
|---|---|---|---|---|
| 1.1 | Ship SSE keepalive + watchdog bump: backend emits `:keepalive` SSE comment frame every 15s during reranker + LLM-TTFT; FE bumps `WATCHDOG_MS` from 60_000 → 120_000. | engineer | ~30 lines / 1 day | Without this, even a HEALTHY-GPU complex query that takes 80s will 60s-timeout in the FE. Today's issue won't be the last. |
| 1.2 | System smoke-test script: boot a session, ask "list documents", assert <20s response AND reranker logs `on cuda`. Run after every restart_serve_rag.sh; cron daily. | engineer | ~3 hours | Catches reranker-on-CPU pattern before a real user does. Cheap insurance. |
| 1.3 | Fix DDiQ progress bar (sits at 7% for 11 min) — per-question ticks not per-section ticks. Documented in earlier sessions. | engineer | ~half day | First thing a pilot user notices. Frozen progress bar = "it's broken". |
| 1.4 | "Still indexing" → green chip transition: when `matter_documents.status = 'done'`, the chip explicitly turns green; chat error message stops saying "wait a moment" once ingestion is complete. | engineer | ~3 hours | Today's repro: Kristian uploaded `GB-Auszug Tostedt`, was told "still processing" — but it actually was processed seconds later. UX gap. |
| 1.5 | Slow-query telemetry: log every `/query/stream` >30s with session_id, focus_doc_indexes, retrieval_ms, rerank_ms, llm_ttft_ms, total_ms. Structured JSON line. | engineer | ~half day | You cannot fix what you cannot measure. Today's incident: chat was broken all day and no telemetry pointed at the reranker. |

**Definition of done for Phase 1:** No more "no data for 60s" errors on
healthy-GPU queries. No more frozen progress bars. Slow queries are
visible in logs before users complain.

---

## Phase 2 — Pilot-ready features (next 2–4 weeks)

These are the v1 blockers strategy-Claude correctly flagged. Without them,
no real German law firm will use LAI on a real matter.

| # | Action | Owner | Effort | Justification |
|---|---|---|---|---|
| 2.1 | **DOCX export of DDiQ findings.** Templated `python-docx` writer; placeholders for firm letterhead. Even without letterhead, ships. | engineer | 2–3 days | Without it the lawyer cannot deliver LAI's output to a client. The workflow has no exit. Cited as v1 blocker in [DDIQ_ROADMAP.md], [LAI_V1_STRATEGY.md]. |
| 2.2 | **Shared Matters (multi-user) — FE only.** `[verified 05-28]` The BACKEND IS ALREADY BUILT AND MOUNTED, not just "drafted": `share_router.py` exposes `list_shares` (:95), `add_share` (:137), `revoke_share` (:225), `search_share_targets` (:260) with a `_can_manage_shares` owner gate (:76); read-only collaborator semantics are already wired in `persistence.py` (`load_session:328`, `session_exists:527` carry the `session_shares` OR-clause). What's left is the **FE "Share" modal** wired to those existing endpoints. | engineer | ~2 days (was mis-scoped at ~1 week) | Per [MULTIUSER_PLAN.md]. Every serious pilot is a 2–3 person team. No sharing = no pilot. |
| 2.3 | **Minimal audit log.** Append-only `audit_log` table: user_id, session_id, action (query/upload/report/export/login), timestamp, outcome, latency_ms. JSON-readable, queryable. | engineer | 3 days | `[verified 05-28]` EU AI Act Art. 12 (record-keeping) and the Aug 2, 2026 date are both real — BUT they bind only **high-risk** systems (Annex III), and a private firm's DD/research assistant is arguably NOT high-risk (the "administration of justice" category targets AI used *by a judicial authority*, not lawyers' tools). Treat this as **good practice + a genuine on-prem sales differentiator**, NOT a confirmed regulatory hard deadline, until someone spends 30 min on the classification. ("No cloud competitor offers on-prem audit trails" is an unverified sales claim — don't repeat it as fact.) |
| 2.4 | **Find ONE pilot firm.** Not Kristian — a real outside lawyer. Boss should provide 3 most-trusted contacts at small German wind-energy firms. Get one in front of LAI for a 30-min demo. | boss + rj | (relational, not engineering) | The actual bottleneck strategy-Claude correctly flagged. 5 months in, no pilot customer. Boss owns this. |

**Definition of done for Phase 2:** LAI is usable by a 2-person team on a
real matter, produces a Word document the lawyer can send to a client,
and has an audit log a compliance officer can inspect.

---

## Phase 3 — Foundation-model PoC, scoped to ONE law area (6–8 weeks)

Strategy-Claude's foundation-model vision is right but should be proven on a
scoped surface before it scales. Sequence matters.

| # | Action | Owner | Effort | Justification |
|---|---|---|---|---|
| 3.1 | Pick ONE law area: **BImSchG**. Reason: we already have wind corpus, the boss test runs on it, smallest viable surface. | engineer | decision, 1 day | Don't generate 500k Q&A across all of German law and then discover LoRA doesn't work. |
| 3.2 | Generate 30–50k synthetic Q&A pairs using Claude API as teacher. Feed BImSchG statute text + relevant OVG/BVerwG rulings + existing matter examples; ask Claude to produce realistic wind-energy lawyer questions with grounded answers. | engineer | ~5 days scripting + API time | Cost estimate: €1,500–3,000 one-time. NOT the €500–1,500 strategy-Claude quoted — those numbers were light for real legal-prompt sizes. `[verified 05-28]` PIN THE TEACHER MODEL: this range holds for a **Sonnet-class teacher + prompt caching** (~50M output tokens ≈ €700–1,000); an **Opus teacher exceeds €3,000 in output alone** (~€3,400). Use Sonnet 4.6 as teacher and cache the statute/ruling context. |
| 3.3 | **LoRA fine-tune** Qwen3-27B on those pairs. NOT full fine-tune (that's why the team's earlier attempt regressed — catastrophic forgetting). | engineer | ~1–2 weeks of GPU time when not serving production | Strategy-Claude was correct on the technique. |
| 3.4 | A/B test: pick 50 real BImSchG questions from existing matter logs. Run both base Qwen3-27B and the LoRA-fine-tuned version. Hand-label which answers are better. | engineer + a lawyer | ~3 days | If LoRA can't beat base on BImSchG (our richest domain), it won't beat base anywhere. Stop and iterate on data quality before going broad. |
| 3.5 | If LoRA wins: ship as `qwen3.6-27b-lai-bimschg` alongside base. FE can route BImSchG queries to it. THEN decide on BauGB, EEG, BGB next quarter. | engineer | 2 days deploy | Validates the strategic direction with €3k spent, not €30k. |

**Definition of done for Phase 3:** We have empirical evidence that
domain-adapted Qwen3-27B is better than base for German wind law on real
questions — or we have learned what the training data needs and saved
ourselves a moonshot.

**Note on the RAG-vs-fine-tune debate:** strategy-Claude's framing was half
wrong. German law CHANGES (BImSchG amendments, new OVG rulings, EEG novellen).
Fine-tuned weights go stale the moment the BGB is amended. The right
architecture is BOTH:

- **Fine-tune** for German legal reasoning style and legal-terminology fluency
- **Keep RAG** (existing pgvector pipeline) as the source of CURRENT statute
  text
- **Add** a live ingestion feed from gesetze-im-internet.de so amendments
  flow in automatically (Phase 4 candidate)

Foundation model = how to think like a German lawyer. RAG = what the law
actually says today. Both, not either.

---

## Phase 4 — Ongoing discipline (don't drop these)

| # | Action | Owner | Cadence |
|---|---|---|---|
| 4.1 | Weekly Friday status to boss: "this week shipped X, fixed Y, blocked on Z, next week ABC". 10 minutes of writing. | engineer | Every Friday |
| 4.2 | EU AI Act August 2026 backstop tracker. Audit log + technical documentation package + human-oversight UI must be live by then. Schedule into roadmap as fixed dates, not "later". | engineer | Monthly review |
| 4.3 | Live ingestion feed from gesetze-im-internet.de — daily diff against current statutes, embed new/changed sections into pgvector. | engineer | Phase 4 PROJECT (~2 weeks) |
| 4.4 | Pilot-firm retention. Once Phase 2.4 lands a firm: weekly check-in with their lead lawyer, capture what queries failed, what they wished LAI did. This is the only feedback loop that matters. | rj + boss | Weekly |

---

## What's blocking what (dependency view)

```
[ks_admin runs udev+chmod]            ← BLOCKING EVERYTHING
   ↓
[restart_serve_rag.sh]                ← unblocks all Phase 0
   ↓
[smoke test passes]
   ↓
[Phase 1: keepalive + telemetry + UX fixes]
   ↓
[Phase 2: DOCX + sharing + audit + pilot firm]
   ↓ (in parallel from week 4)
[Phase 3: LoRA PoC]
```

**Single point of failure right now:** waiting on a sudoer (ks_admin / aime /
dn_admin) to run a 4-line script. Until that, the demo system is broken AND
none of today's code fixes are live.

---

## Decisions deferred (revisit at end of Phase 2)

- **Full foundation-model corpus build.** Don't generate Q&A across the whole
  German legal corpus until BImSchG PoC validates the approach.
- **EU AI Act conformity-assessment paperwork.** Mostly writing, not
  engineering. Schedule for July when audit log lands.
- **Solar / property-law specialist agents.** Defer until BImSchG agent is
  live and we know what worked.
- **Real production hosting** (currently dev box). The 2× RTX 6000 setup is
  fine for a single-firm pilot; if the pilot succeeds, plan a properly-managed
  GPU box.

---

## What this document is NOT

- Not a sprint plan with sub-day granularity — that's tracked in tasks
- Not a marketing/strategy doc — that's [LAI_V1_STRATEGY.md] + [DEEP_RESEARCH.md]
- Not a full technical architecture — that's [ARCHITECTURE_BRIEF.md]
- Not an authoritative dependency graph — small dependencies will shift

It IS: a written, dated, accountable list of what to do next, in priority
order, so the next time the boss asks "where are we", the answer is on disk
and not in someone's head.

---

## For the next Claude session

Read this file first. Then:

1. Check `/data/projects/lai/LAI/logs/host/serve_rag.log` for `Loading reranker
   ... on (cuda|cpu)` to know whether Phase 0 completed.
2. Check `git log --oneline -20` in LAI and LAI-UI repos for which Phase 1/2
   items shipped.
3. Resume from the lowest-numbered incomplete phase.

Today's session shipped to disk (need restart to deploy). All `[verified
05-28]` present at the cited file:line:
- Empty-bytes upload guard — `serve_rag.py:2180` (confirmed; the shared
  ingestion path covers the TUS finalize too)
- Failed-row retry — `__dedup_failed_retry` + `reset_matter_document_for_retry`
  (`persistence.py:903,968`; both endpoints `serve_rag.py:4137/4148,4270/4281`)
- Same-name dedup — `__dedup_existing` (`persistence.py:870,905`; both endpoints
  `serve_rag.py:4128,4251`)
- Folder picker (3 surfaces in `ProjectFileGrid.tsx`: drop, primary button :290,
  secondary CTA :345)
- DDiQ: `Finding.ampel` derived property + serialize override (`models.py:233,238`)
- DDiQ: `parks[].location` bundesland-gated address selection (committed series
  `a2fec22` etc. + working tree)
- DDiQ: multiParkDetected re-trip from cross-doc findings + stub ParkFacts for
  cross-doc-only parks

**`[verified 05-28]` STATUS CORRECTION:** the original line here claimed "all
edits are committed locally only." That is FALSE. As of 2026-05-28 these are
**uncommitted working-tree changes** (`M`), and `upload_tus.py` is untracked
(`??`). Some related DDiQ work IS committed (the bundesland/multi-park series in
`git log`), but the current diff — `Finding.ampel`, dedup/retry, the serve_rag
VDR/upload/OCR work, the whole TUS module — is NOT. **Commit first (Phase 0.0)**,
then push/deploy are owner decisions.
