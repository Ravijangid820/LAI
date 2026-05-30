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
| 1.2 | System smoke-test script | **vm** | ✅ | Committed `b7c141c`: `scripts/ops/smoke_test.py` (stdlib) — health→login→seed→timed RAG query; asserts <budget AND reranker `on cuda`. Distinct exit codes; doc'd in ops README. Extended for DDiQ report leg (vm-3, `290bb25`). See vm track. |
| 1.3 | DDiQ progress bar (per-question ticks) | us | ✅ | Per-question ticks 0.07→0.55 (`7db20ea`) + cadastral pipeline ticks 0.78→0.84 (`ad470a1`); no flat windows left. Rides next rebuild. |
| 1.4 | "Still indexing" → green chip | **vm** | ✅ | Green chip already in WIP; real fix = best-copy-per-filename dedup in `DocumentList` poll so a stale dup row stops gating chat ("still processing" after done). **Uncommitted** — bundle with upload WIP. See vm track. |
| 1.5 | Slow-query telemetry | us | ✅ | Committed `9d516dc`. One JSON `slow_query` line ≥ `LAI_SLOW_QUERY_S` (30s) with embed/retrieve/rerank/generate/total ms + session/mode/focus. |

## Phase 2 — Pilot-ready

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 2.1 | DOCX export | us | ✅ | Client-side exporter already existed; consolidated + translated to German + firm-letterhead placeholder (`f0f0441`). Server-side endpoint reverted (`ca7b2d2`) to avoid a dead duplicate (couldn't be verified live behind the blocked rebuild). |
| 2.2 | Shared Matters (multi-user) | — | ⭐ | **Already fully built**: `share_router` backend + `ShareDialog` FE wired in `ProjectChatView`. No work needed — roadmap mis-scoped this as ~1 week. |
| 2.3 | Minimal audit log | us | ✅ | Core done (`5a6a3b2`): migration 006 `audit_log` (append-only via no-UPDATE trigger) + `lai.common.audit` best-effort writer (async+sync, 98% cov) + **login / query / report** instrumented. ⚠️ good-practice + sales differentiator, NOT a confirmed AI-Act deadline. Read endpoint `GET /admin/audit` (`d9ed39a`) + FE view at `/dashboard/admin/audit` (`c554842`, LAI-UI — table w/ action filter + paging, admin-gated). **Deploy:** ✅ migration 006 applied + serve_rag restarted + DDiQ rebuilt (05-29 14:25 — audit_log live & append-only, reranker on cuda:1); ⬜ deploy LAI-UI (view) still pending. Upload + export events now wired (`8ddd324`, committed by rj): serve_rag `/upload` audits filename/doc_index/bytes; new `POST /ddiq/report/{id}/export` audits format after an owner/share visibility check. FE export-ping (`ddiqApi.recordExport` + `ReportDownloadPanel` handlers) implemented but **uncommitted in LAI-UI** — bundle with team upload WIP. Ops export + retention CLI shipped (vm-4, `5abe968`) — `scripts/ops/audit_export.py`. |
| 2.4 | Find ONE pilot firm | boss + rj | ⬜ | Relational, not engineering. |

## Phase 3 — Foundation-model PoC (BImSchG LoRA)
⬜ Not started. 6–8 weeks, sequence **after** the pilot. LoRA fine-tune of **Qwen3.6-27B** (verified live base — Apache 2.0, served on :8005 with `--reasoning-parser qwen3`; *not* "Qwen3-27B") on 30–50k Claude-distilled BImSchG Q&A; A/B vs base; ship as a routed variant if it wins. Architecture = fine-tune for reasoning + RAG for current statute, both.
**Base-model choice (05-29 — full analysis in [MODEL_COMPARISON.md](./MODEL_COMPARISON.md)):** no clearly-better *free* model justifies switching the base — Gemma 4 27B & Mistral Small 24B are Apache-2.0 *peers*, not upgrades. Plan: keep Qwen3.6-27B as the LoRA base (zero pipeline-switch cost — our analyzer is bonded to Qwen3's reasoning parser + JSON decoding), add **base Gemma 4 27B** as a same-size/same-license A/B challenger (the one published German-legal LoRA paper used Gemma). Avoid Llama 4 (Meta custom license, 700M-MAU clause — needless legal-review burden for a legal product). Hardware fits all candidates (2× RTX PRO 6000 Blackwell 96 GB); gotcha = pin training libs to Blackwell sm_120 / CUDA 13.2 builds.

## Phase 4 — Ongoing discipline
- 4.1 Friday status to boss — ⬜
- 4.2 EU AI-Act tracker — ⬜
- 4.3 `gesetze-im-internet.de` ingestion feed — 🔄 **Phase A (read-only) DONE 05-29.** Built on `develop` (commits `4861a10`, `0a73f16`, `a2f975f`): GII law-XML + TOC parser, `GesetzeImInternetClient` (httpx+tenacity+metrics+typed errors), data-driven law→domain category registry (aligned to `classify.py` taxonomy; 29 wind-relevant laws mapped, rest → `allgemein`), and a `python -m lai.pipeline.statute_feed` dry-run CLI. Validated live (6,123 laws fetched, 29 categorised, sample parse OK — BauGB 298 §§); 22 tests, mypy --strict, `lai.common` cov 89.01%. Doc: [`docs/statute_feed.md`](../LAI/docs/statute_feed.md). Standalone per-law disk fetcher shipped (vm-5, `3c4033b`) — `scripts/ingest/fetch_gesetze.py` (BImSchG default; reuses the Phase-A client+parser to write idempotent per-§ JSON under `data/statutes/<slug>/` ahead of Phase B's corpus write path). ⬜ Phase B (write path → `corpus_*` + migration 007, **touches live retrieval — needs sign-off**); ⬜ Phase C (daily cron + full statute set).
- 4.4 pilot retention loop — ⬜ (needs a pilot first, see 2.4)

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
- `5a6a3b2` feat(audit): append-only audit log (2.3) — migration 006 + `lai.common.audit` (async+sync, best-effort, 98% cov) + login/query/report instrumented; CI gates all green (599 tests, cov 87%)
- `d9ed39a` feat(audit): admin read endpoint `GET /admin/audit` + `audit.query()` reader; fixed the audit suite being deselected by `make cov` (added `pytestmark = unit`); 601 tests, cov 87.56%
- `b7c141c` feat(ops): system smoke test — guards against reranker-on-CPU (vm-1 / 1.2; stdlib, distinct exit codes, doc'd in ops README)
- `290bb25` feat(ops): smoke_test `--report` leg for DDiQ pipeline (vm-3 / 1.2 follow-up; new exit code 7, env-aliased creds, cron line documented but not installed pending rj OK)
- `5abe968` feat(ops): audit_log export + retention CLI (vm-4 / 2.3 follow-up; CSV/JSON export with date+action+org+user filters, dry-run-by-default `--purge-older-than DAYS`, EU AI Act Art. 12 callout in README)
- `3c4033b` feat(ingest): one-law `gesetze-im-internet.de` fetcher (vm-5 / Phase 4 feed; thin wrapper around rj's Phase-A client+parser, writes per-§ JSON to `data/statutes/<slug>/`, atomic swap, sha256-keyed idempotency)

**LAI-UI** (`fix/cross-account-isolation`):
- `f0f0441` fix(ddiq): German labels + firm-letterhead placeholder in DOCX export (2.1)
- `9a2040e` fix(report): readable progress labels for the DDiQ report pipeline (Wave 2 / R2 — clean file, committed)
- `c554842` feat(audit-ui): admin audit-log view at `/dashboard/admin/audit` (new page + adminApi.listAudit + route + link; tsc/eslint clean, clean of upload WIP)
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

## Deploy state — live vs pending (updated 2026-05-29 14:25)

**Update 2026-05-29 14:25 — audit deploy complete + `v2.1.0` released.**
- **`v2.1.0` released:** repo consolidated to trunk-based **Git Flow** (single `master` + `develop`; `v2-restructure` retired). Tags: `v1.0.0`, `v2.0.0`, `v2.1.0`. The audit subsystem, CI fix (`fc931f9`), smoke test, and Git Flow docs all shipped in `v2.1.0`. master == develop == v2.1.0.
- **Audit log LIVE:** migration 006 applied to `lai_db` (audit_log table + append-only trigger verified); serve_rag restarted + DDiQ rebuilt with the audit code (reranker confirmed `on cuda:1`). login/query/upload (serve_rag) + report/export (DDiQ) instrumented end-to-end. Table records on next user action (0 rows at deploy).
- **Still pending:** LAI-UI FE deploy (audit-log view + C2/C3/watchdog/vm-2) — blocked on the team upload WIP (26 dirty files).

---

(historical, 2026-05-29 04:10) rj re-ran `restart_serve_rag.sh` → serve_rag restarted AND DDiQ rebuilt+recreated. **Backend is fully live (verified):**
- **serve_rag (host, PID 3007929, healthy):** ✅ `uid`→`user_id` history fix (chat memory restored), C1 chat narration, slow-query telemetry; reranker on `cuda:1`.
- **DDiQ (containers built 05-29 04:10, healthy):** ✅ per-question + cadastral progress ticks, ampel/bundesland fixes, defusedxml XML hardening (confirmed importable in the container). The hardened `down --remove-orphans` recreate worked — no name conflict.

**Still pending:**
- **LAI-UI (FE — separate deploy):** ⬜ **not deployed.** R2 step-labels + German DOCX labels + audit-log view (committed) and C2/C3/watchdog + vm-2 (uncommitted) need an FE build/deploy.
- **CI fix (`fc931f9`):** ✅ released in `v2.1.0` (merged to master + develop; ci-gate green).
- **Audit log (`5a6a3b2`):** ✅ migration 006 applied 05-29 14:25; serve_rag + DDiQ restarted with audit code → events recording on next action.

## Next steps (grounded — no invented work)


Ordered by value / unblocking:
1. ✅ **DONE — `fc931f9` released in `v2.1.0`** (ci-gate green; merged to master + develop).
2. ✅ **DONE — serve_rag restarted (05-29 14:25)**; `uid` history fix + audit code live; reranker on cuda:1. Smoke-test still pending a test login.
3. **Commit the uncommitted FE** (`DashboardChat.tsx` C2/C3, `ragApi.ts` watchdog, `DocumentList.tsx` vm-2 dedup) alongside the team's upload WIP, then **deploy LAI-UI** to make R2 + German DOCX labels + C2/C3 + watchdog + vm-2 live. ⛔ **still open — blocked on the FE-WIP owner (26 dirty files in LAI-UI).**
4. ✅ **DONE — DDiQ rebuilt (05-29 14:25)** via `restart_serve_rag.sh`; defusedxml + report-progress fixes live.
5. **R3 completion toast** — apply the 1-line spec above once the teammate's `ReportDownloadPanel.tsx` WIP lands. ⛔ **still open — blocked (same FE WIP region).**
6. ✅ **DONE — Phase 2.3 audit log shipped (`v2.1.0`) AND deployed (05-29 14:25)**; migration 006 applied; login/query/upload/report/export instrumented across serve_rag + DDiQ.
7. **Phase 2.4 pilot firm** — boss/rj, relational not engineering. The actual bottleneck (5 months, no pilot). ⬜ **← the remaining priority.**

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

---

### Next picks for vm (assigned 2026-05-29)
All three are **new/standalone files or vm's own files** — zero collision with our serve_rag/DDiQ/FE work and with the team's LAI-UI upload WIP. Ordered easiest-first.

### vm-3 — Smoke-test: real login leg + DDiQ report leg  (roadmap 1.2 follow-up)  · easiest, zero collision
- **✅ DONE — committed `290bb25`.** Findings on landing: the "real login leg" (item 1 of the spec) was **already shipped in vm-1** — `smoke_test.py` reads `LAI_SMOKE_EMAIL`/`PASSWORD`, hits `/auth/login`, and uses the bearer token for the seeded query. So only the report leg + ergonomics were new. Added: (a) `--report` flag that POSTs `/ddiq/report/generate/async` against `LAI_SMOKE_DDIQ_DOC_ID` and polls `/ddiq/report/{id}/status` until `done` OR observed-advance within `LAI_SMOKE_DDIQ_MAX_S` (default 600s); new exit code 7 = "ddiq report failed / never advanced". (b) `LAI_SMOKE_USER`/`LAI_SMOKE_PASS` accepted as aliases for the EMAIL/PASSWORD pair (vm-3 spec named them that way). (c) README documents the one-time `ddiq_documents` seed pattern + the new tunables. Cron line is in the README but explicitly **not installed** — shared-box change, awaits rj's OK (per the spec). ruff/format clean. **Original-spec correction:** vm-3 said vm-1 "couldn't" do a login leg because of no test account; in fact vm-1 shipped it env-driven, so the leg has been there since `b7c141c`.
- **Where:** `LAI/scripts/ops/smoke_test.py` (+198/−29) + `LAI/scripts/ops/README.md` (+30) — vm's own files.

### vm-4 — `audit_log` export / retention CLI  (roadmap 2.3 follow-up)  · easy, isolated
- **✅ DONE — committed `5abe968`.** Added `scripts/ops/audit_export.py` (asyncpg, 378 LOC) with three things: (a) CSV / JSON bulk export filtered by `--since` / `--until` / `--action` / `--org-id` / `--user-id` / `--limit` — pages through `lai.common.audit.query` (the same single read primitive the admin endpoint uses) and trims by `ts` client-side, bailing as soon as we cross below `--since` since rows are newest-first; (b) `--purge-older-than DAYS` retention that's **dry-run by default** (exits 3 with a row count) and only deletes with `--yes` via a bound-parameter `DELETE FROM audit_log WHERE ts < $1` — migration 006's trigger blocks UPDATE but intentionally leaves DELETE to a privileged retention job, which is this script; (c) README block documents the flags and adds the EU AI Act Art. 12 retention minimum-6-months callout. Same `DB_*` env as the audit writer. ruff/format clean.
- **Where:** new `LAI/scripts/ops/audit_export.py` + README block — read-only import of `lai.common.audit`, nothing modified.

### vm-5 — `gesetze-im-internet.de` statute fetcher (one law: BImSchG)  (roadmap Phase 4 feed)
- **✅ DONE — committed `3c4033b`.** **Stale-spec correction up-front:** the vm-5 brief said "no existing ingest code" — that was written before rj shipped Phase 4.3 A on 05-29 (commits `0a73f16` + `a2f975f`: `GesetzeImInternetClient`, `parse_law_xml`, `parse_toc`, the law→domain registry). I **imported and reused** those (Phase A's parsing is defusedxml-hardened and unit-tested; re-implementing it in `scripts/ingest/` would have been pure duplication and a collision risk with rj's surface). The script is therefore a thin disk-writer: per-§ JSON files under `data/statutes/<slug>/sections/NNNN_<enbez>.json` carrying `seq / law_slug / jurabk / enbez / titel / text / sha256 / fetched_at`, plus a top-level `meta.json` with `xml_sha256` as the fast-path idempotency skip key. Atomic swap via sibling temp dir + double-rename so a crash never leaves partial state under the canonical path. Default `--slug bimschg`; `--force` overrides the skip. New `scripts/ingest/README.md` explains the extension path (`--slug baugb`, `--slug eeg_2023`, …) using rj's existing `python -m lai.pipeline.statute_feed --fetch-sections` dry-run TOC tool for slug discovery. **Not run live yet** — fetches the federal portal, so first execution should be ops-coordinated (politeness throttle is already configured in the `GesetzeConfig`).
- **Where:** new `LAI/scripts/ingest/fetch_gesetze.py` (316 LOC) + `LAI/scripts/ingest/README.md` (75 LOC) + new `LAI/data/statutes/` dir.
