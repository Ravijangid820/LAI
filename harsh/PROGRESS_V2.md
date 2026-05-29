# LAI v2 — Progress Tracker

**Tracks:** [ROADMAP_2026Q3.md](./ROADMAP_2026Q3.md)
**Started:** 2026-05-28
**How to use:** the single place to answer "where are we". Update an item's status the moment it lands. Statuses verified against the actual code/git, not the roadmap's assumptions.

**Legend:** ✅ done · 🔄 in progress · ⛔ blocked (external) · ⬜ todo · ⭐ already shipped (roadmap didn't know)

---

## Phase 0 — Unblock

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 0.0 | Commit the uncommitted work | us | ✅ | LAI backend committed in 5 commits (see log). LAI-UI still has team WIP (upload). |
| 0.0b | Confirm the hot rerank path | us | ✅ | Chat path = **in-process** torch reranker (`search/eval.py:350`); `:8004 lai-test-reranker` serves **DDiQ only**. **UPDATE 05-28:** it was already running on GPU (18.5 GB) — the "on cpu" log was stale, so there was nothing to fix. |
| 0.1 | GPU access | — | ✅ N/A | **No perms issue ever (verified 05-28).** Reranker was already on GPU (pid 558860 held 18.5 GB); nodes are `root:lai`, rj is in `lai`, `/dev/nvidia-uvm` world-rw. The "on cpu" log was **stale**. ks_admin NOT needed — the whole Phase-0 GPU premise was a ghost. |
| 0.2 | Restart serve_rag + rebuild/recreate DDiQ | rj | ✅ | Done 05-28 20:16: serve_rag restarted (PID 3959685, healthy); DDiQ images rebuilt + `lai-backend`/`lai-worker` recreated & healthy (after a stale-container name conflict that self-cleared). |
| 0.3–0.5 | Verify GPU / reranker / smoke chat | rj | ✅ | GPU confirmed (18.5 GB held); serve_rag `/health` + `:18001/health` ok; worker running. Smoke-test a live chat + report next. |

## Phase 1 — Stop the silent failures

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 1.1 | SSE keepalive + watchdog bump | us | ✅ | Backend SSE heartbeats already present. FE `WATCHDOG_MS` 60s→120s done in `ragApi.ts` (in working tree; commit alongside the upload WIP). |
| 1.2 | System smoke-test script | **vm** | ✅ | Committed `b7c141c`: `scripts/ops/smoke_test.py` (stdlib) — health→login→seed→timed RAG query; asserts <budget AND reranker `on cuda`. Distinct exit codes; doc'd in ops README. See vm track. |
| 1.3 | DDiQ progress bar (per-question ticks) | us | ✅ | Per-question ticks 0.07→0.55 (`7db20ea`) + cadastral pipeline ticks 0.78→0.84 (`ad470a1`); no flat windows left. Rides next rebuild. |
| 1.4 | "Still indexing" → green chip | **vm** | ✅ | Green chip already in WIP; real fix = best-copy-per-filename dedup in `DocumentList` poll so a stale dup row stops gating chat ("still processing" after done). **Uncommitted** — bundle with upload WIP. See vm track. |
| 1.5 | Slow-query telemetry | us | ✅ | Committed `9d516dc`. One JSON `slow_query` line ≥ `LAI_SLOW_QUERY_S` (30s) with embed/retrieve/rerank/generate/total ms + session/mode/focus. |

## Phase 2 — Pilot-ready

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 2.1 | DOCX export | us | ✅ | Client-side exporter already existed; consolidated + translated to German + firm-letterhead placeholder (`f0f0441`). Server-side endpoint reverted (`ca7b2d2`) to avoid a dead duplicate (couldn't be verified live behind the blocked rebuild). |
| 2.2 | Shared Matters (multi-user) | — | ⭐ | **Already fully built**: `share_router` backend + `ShareDialog` FE wired in `ProjectChatView`. No work needed — roadmap mis-scoped this as ~1 week. |
| 2.3 | Minimal audit log | us | ⬜ | Genuinely missing. ⚠️ The AI-Act "hard deadline Aug 2026" only binds **high-risk** (Annex III) systems; LAI's classification is unverified — build as good practice + sales differentiator, not a confirmed legal deadline. |
| 2.4 | Find ONE pilot firm | boss + rj | ⬜ | Relational, not engineering. |

## Phase 3 — Foundation-model PoC (BImSchG LoRA)
⬜ Not started. 6–8 weeks, sequence **after** the pilot. LoRA fine-tune of Qwen3-27B on 30–50k Claude-distilled BImSchG Q&A; A/B vs base; ship as a routed variant if it wins. Architecture = fine-tune for reasoning + RAG for current statute, both.

## Phase 4 — Ongoing discipline
⬜ Friday status to boss · EU AI-Act tracker · `gesetze-im-internet.de` ingestion feed · pilot retention loop.

---

## Completed this session (commits)

**LAI** (`v2-restructure`):
- `884ea24` feat(ddiq): ampel serialization, refusal guards, per-park bundesland gating
- `339cf11` feat(upload): resumable tus 1.0 upload server
- `3cb2547` feat(serve_rag): VDR-scale retrieval, image OCR, resumable-upload wiring
- `c4eac72` chore(ops): restart_serve_rag.sh rebuilds backend+worker together
- `18f23d5` feat(stress): VDR-scale matter staging + delivery scripts
- `9d516dc` feat(serve_rag): slow-query telemetry (1.5)
- `5902054` feat(serve_rag): narrate retrieval in /query/stream — UX, no dead air before first token (ships on restart)
- `7db20ea` fix(ddiq): per-question report progress ticks — kills the 7% stall (1.3; rides next rebuild)
- `ad470a1` fix(ddiq): cadastral pipeline progress ticks — kills the 78% freeze (1.3 follow-up; rides next rebuild)
- `023a189` docx backend → **reverted** by `ca7b2d2` (consolidated on client-side exporter)
- `47c933b` fix(serve_rag): restore chat history + meta refresh (`uid` → `user_id`) — a real bug ruff's F821 surfaced; history was silently loading empty
- `c42744c` chore(ops): restart_serve_rag.sh `down --remove-orphans` before recreate — kills the stale-container name conflict hit on the 05-28 deploy
- `f30d0a0` + `2d73c9e` style: ruff 0.15.5 auto-fix + format + manual fixes — **CI lint gate green** (563 errors + 64 files → 0)
- `16b31f2` fix(ci): **mypy strict + bandit gates green** on lai.common (14 type errors → 0; 14 bandit findings → 0; B608 audited-safe, XML hardened with defusedxml)
- `fc931f9` fix(ci): run the ci-gate step in the workspace root — fixes the aggregate-gate `No such file or directory` (job had no checkout under the global `working-directory: LAI`)
- `b7c141c` feat(ops): system smoke test — guards against reranker-on-CPU (vm-1 / 1.2; stdlib, distinct exit codes, doc'd in ops README)

**LAI-UI** (`fix/cross-account-isolation`):
- `f0f0441` fix(ddiq): German labels + firm-letterhead placeholder in DOCX export (2.1)
- `9a2040e` fix(report): readable progress labels for the DDiQ report pipeline (Wave 2 / R2 — clean file, committed)
- `ragApi.ts` watchdog 60s→120s (1.1) — **uncommitted** (file holds team upload WIP; commit together).
- `pages/DashboardChat.tsx` C2 (rehydration skeleton — no "New Conversation" flash) + C3 (keep partial answer on stream timeout) — **uncommitted** (file holds +56/−23 team WIP; my edits are in regions clear of the WIP hunks, lint-clean; commit together with that WIP).
- `components/chat/DocumentList.tsx` vm-2 (1.4): best-copy-per-filename dedup in the status poll → a stale duplicate row no longer keeps chat gated on "still processing" after a `done` copy exists (green chip already present). tsc + eslint clean; dedup logic unit-checked; not browser-tested. **Uncommitted** — edit sits in the poll region, clear of the upload-WIP hunks; commit together with that WIP.

## UX smoothness — Wave 2 status
- **R2** (report step labels) ✅ committed `9a2040e`.
- **C2** (rehydration skeleton) ✅ done, uncommitted in `DashboardChat.tsx` (WIP file).
- **C3** (keep partial answer on timeout) ✅ done, uncommitted in `DashboardChat.tsx` (WIP file).
- **R3** (report completion toast) ⬜ **DEFERRED** — a teammate is actively editing the exact done-branch in `ReportDownloadPanel.tsx` (WIP hunk `@@ -1432 +1445,24`). Editing there risks duplicating/conflicting with live work. Ready-to-apply spec:
  > In `ReportDownloadPanel.tsx`, in the poll loop's `if (s.status === "done")` branch (~line 1425, right before `setStep("preview")`), add `toast.success("Your report is ready", { description: s.project_name })`. `toast` is already imported. One line; do it once the teammate's WIP in that region lands.

---

## Quality gates (CI) — now green

All four CI gates pass, verified locally on the CI-locked tooling (ruff 0.15.5, mypy 1.19.1, fresh env):
- **lint** (ruff check + format) ✅ — `f30d0a0` (auto-fix + format) + `2d73c9e` (manual + scoped config)
- **type** (mypy strict, lai.common) ✅ — `16b31f2`
- **security** (bandit, lai.common) ✅ — `16b31f2` (B608 audited-safe skip; XML → defusedxml)
- **test** (pytest) ✅ — 591 unit tests pass
- **ci-gate** (aggregate) ✅ — `fc931f9` fixed the workspace-dir bug that failed it even with the four gates green

Pre-existing debt confirmed (not caused by our edits): the lint/type/security failures were branch-wide and latent — CI had been red on multiple gates, hidden because upstream failures skipped ci-gate.

## Deploy state — live vs pending (as of 2026-05-29 04:10)

rj re-ran `restart_serve_rag.sh` → serve_rag restarted AND DDiQ rebuilt+recreated. **Backend is fully live (verified):**
- **serve_rag (host, PID 3007929, healthy):** ✅ `uid`→`user_id` history fix (chat memory restored), C1 chat narration, slow-query telemetry; reranker on `cuda:1`.
- **DDiQ (containers built 05-29 04:10, healthy):** ✅ per-question + cadastral progress ticks, ampel/bundesland fixes, defusedxml XML hardening (confirmed importable in the container). The hardened `down --remove-orphans` recreate worked — no name conflict.

**Still pending:**
- **LAI-UI (FE — separate deploy):** ⬜ **not deployed.** R2 step-labels + German DOCX labels (committed) and C2/C3/watchdog (uncommitted) need an FE build/deploy.
- **CI fix (`fc931f9`):** ⬜ committed locally; needs a **push** to update PR #9 and turn `ci-gate` green.

## Next steps (grounded — no invented work)

Ordered by value / unblocking:
1. **Push `fc931f9`** → PR #9 ci-gate goes green (the four gates already pass). [Ravi/owner — push decision]
2. **Restart serve_rag** to deploy the `uid` history fix (`47c933b`) — chat conversation memory is currently broken on the live box. Cheap (host process). Then smoke-test chat with the vm-1 script `scripts/ops/smoke_test.py` (now shipped, `b7c141c`).
3. **Commit the uncommitted FE** (`DashboardChat.tsx` C2/C3, `ragApi.ts` watchdog, `DocumentList.tsx` vm-2 dedup) alongside the team's upload WIP, then **deploy LAI-UI** to make R2 + German DOCX labels + C2/C3 + watchdog + vm-2 live. [needs coordination with the FE-WIP owner]
4. **Next DDiQ rebuild** (via `restart_serve_rag.sh`, now hardened) to deploy defusedxml + the report-progress fixes.
5. **R3 completion toast** — apply the 1-line spec above once the teammate's `ReportDownloadPanel.tsx` WIP lands.
6. **Phase 2.3 audit log** — genuinely missing; build as good practice + on-prem sales differentiator (NOT a confirmed AI-Act hard deadline — classification unverified). Bounded: append-only table + instrument query/upload/report/export/login. Spans serve_rag (host) + DDiQ (rebuild-gated).
7. **Phase 2.4 pilot firm** — boss/rj, relational not engineering. The actual bottleneck (5 months, no pilot).

Deferred / later: Phase 3 foundation-model PoC (after a pilot); Phase 4 discipline items.
Minor follow-up noted in code: an always-`"running"` ternary in the gated V2-analyzer progress path (collapsed for lint; logic smell — status never reports "done" there).

## Vikrant Malik (vm) — parallel track

Picked because they're **isolated from our current work** (serve_rag retrieval/telemetry, the ddiq report engine, DOCX). vm can run these in parallel with no merge collisions on our files.


### vm-1 — System smoke-test script  (roadmap 1.2)  · easiest, zero collision
- **✅ DONE — committed `b7c141c`.** Shipped `LAI/scripts/ops/smoke_test.py` (stdlib-only): `/health` → `/auth/login` → seed `/sessions` → timed `/query`, asserting (a) round-trip < `LAI_SMOKE_MAX_S` (20s) and (b) the latest `Loading reranker … on <dev>` log line is `cuda`. Distinct exit codes (5=slow, 6=reranker-on-CPU) for cron alerting; documented in `scripts/ops/README.md` (usage + cron line). **One deliberate deviation:** sends a `force_mode=rag` query, not a literal chat "list documents" — chat mode skips the reranker, so it couldn't surface a CPU fallback via latency (env-overridable). Validated live: `/health` + log-parser confirmed against the running box; the query/latency leg reuses the same verified HTTP path (no test account to run it end-to-end). Cron NOT installed (shared-box change; line is in the README).
- **File:** brand-new, e.g. `LAI/scripts/ops/smoke_test.sh` (or `.py`). Touches nothing we're editing.
- **Do:** boot/seed a session, send a "list documents" chat query to serve_rag (`:18000`), then assert: (a) response returns in < 20s, and (b) `logs/host/serve_rag.log` shows `Loading reranker … on cuda` (not `cpu`). Exit non-zero with a clear message on failure.
- **Why:** catches the reranker-on-CPU regression — the actual boss-test root cause — before a user hits it. Run after every `restart_serve_rag.sh`; then wire a daily cron.
- **Done when:** returns 0 on a healthy box; non-zero + readable reason when the reranker is on CPU or the query is slow.
- **Collision risk:** none — new standalone file; only reads the log and hits the HTTP API.

### vm-2 — "Still indexing" → green chip transition  (roadmap 1.4)  · FE, isolated from our work
- **✅ DONE — implemented, UNCOMMITTED (bundle with the upload WIP).** Findings: (1) the **green-chip half was already done** in the working tree — `DocumentList.tsx` renders an emerald `CheckCircle2` + "· bereit" on `status === "done"`. (2) The real bug is the **stale "still processing" chat gate**: `DashboardChat.tsx` `docsIngesting` (disables Send + shows "Document is being processed…") comes from `DocumentList`'s `onIngestingChange`, computed as `active = docs.some(queued||processing)` over **raw, un-deduped** docs. A matter can hold duplicate rows per filename (re-drop / retry / an old `failed` beside a fresh `done`), so a stale copy kept `active` true after a `done` copy existed → the exact `GB-Auszug Tostedt` repro. **Fix:** added `bestDocPerFilename` (rank `done>ready>processing>queued>failed`) in the poll, applied to both the rendered rows and `active` — mirrors the composer's own poll-match in `useComposerAttachments.ts`. The edit lands in the poll region, **clear of the upload-WIP hunks**. tsc + eslint clean; dedup logic unit-checked; **not browser-tested** (needs a seeded duplicate matter row to reproduce live). Left uncommitted for the upload-WIP owner to review/bundle (the file is intermingled with their uncommitted changes; do NOT commit standalone).
- **Where:** the FE document-status chip (LAI-UI chat Documents list, likely `components/chat/DocumentList.tsx`) — **not** `ragApi.ts` / `ddiqDocx.ts` which we touched.
- **Do:** when `matter_documents.status === 'done'`, flip the chip to green explicitly; stop the chat error saying "wait a moment / still processing" once ingestion is actually complete.
- **Why:** repro — a user uploaded `GB-Auszug Tostedt`, was told "still processing" though it had finished seconds earlier.
- **Done when:** a finished upload shows a green "ready" chip and chat answers from it with no stale "still processing" message.
- **⚠️ Check first:** `DocumentList.tsx` already has uncommitted WIP, and the upload-status changes in `ragApi.ts` (`BACKEND_URL`/`createSession`/`deduplicated`) are adjacent — vm should sync with whoever owns that upload WIP and read the same status source, so this doesn't collide with *that* (it won't collide with ours).
