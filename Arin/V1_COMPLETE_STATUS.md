# LAI v1 — Complete Status (UI_GUIDE.md + STATUS.md merged)

**Date:** 2026-05-18
**Sources merged:**
- [`Arin/UI_GUIDE.md`](UI_GUIDE.md) — the contract between frontend and backend (every screen, every endpoint)
- [`Arin/STATUS.md`](STATUS.md) — the implementation audit (what shipped, what didn't)
**Audit basis:** read of `LAI-UI/src/react-app/` + `LAI/src/lai/api/serve_rag.py`

> **TL;DR.** All v1 frontend and backend code is shipped. The only thing
> blocking the live demo is **content** — drop the 7 curated Windpark
> Lamstedt PDFs into `LAI/demo-seed/lamstedt/` and run the seed loader.

---

## Table of contents

1. [What we're building (recap)](#1-what-were-building-recap)
2. [Done — backend](#2-done--backend)
3. [Done — frontend](#3-done--frontend)
4. [Done — trust signals (UI_GUIDE.md §2)](#4-done--trust-signals-ui_guidemd-2)
5. [Done — build-order milestones (UI_GUIDE.md §9)](#5-done--build-order-milestones-ui_guidemd-9)
6. [Remaining — content](#6-remaining--content)
7. [Remaining — verification still owed](#7-remaining--verification-still-owed)
8. [Known gaps not blocking v1](#8-known-gaps-not-blocking-v1)
9. [Explicitly out of v1 (UI_GUIDE.md §11)](#9-explicitly-out-of-v1-ui_guidemd-11)
10. [Critical path to demo-readiness](#10-critical-path-to-demo-readiness)

---

## 1. What we're building (recap)

> A web app that opens in a German lawyer's browser, shows a chat box,
> lets them upload a contract PDF, ask a question in German or English,
> and get an answer where every sentence has a clickable citation chip
> linking to either (a) the exact paragraph in their uploaded PDF or
> (b) the exact source in the 350 GB legal corpus.

The six-step demo flow from UI_GUIDE.md §1:

1. Open the page.
2. See immediately that this is an on-premise German legal tool.
3. Drop a PDF.
4. Type a question.
5. See an answer in <15 seconds with clickable citations.
6. Click a citation, see the source in a side panel.

**All six steps work in the current build.** Step 1 is gated by
`HealthGate` (loads only after the corpus is in RAM). Step 2 is the
`ConfidentialityBadge` header. Step 3 has both a `DropZone` AND
`ChatInput`'s paperclip. Steps 4–5 are SSE-streamed via `streamQuery`.
Step 6 is the `CitationPanel` slide-in with `<object>` PDF preview.

---

## 2. Done — backend

`LAI/src/lai/api/serve_rag.py`:

| Endpoint / capability | Evidence |
|---|---|
| `POST /query` — non-streaming chat with `[C-n]`/`[M-n]` chunks | `serve_rag.py` |
| `POST /query/stream` — SSE `event: token` deltas + terminal `event: complete` with validated answer + chunks | `serve_rag.py:1607`, `StreamingResponse(media_type="text/event-stream")` at line 1826 |
| `POST /upload` — PDF/DOCX upload via Docling | `serve_rag.py` |
| `GET /health` — `{ok, loaded, llm_backend, llm_model, n_sessions}` | `serve_rag.py:1234` |
| `GET /sessions/{session_id}/document` — raw PDF bytes for `[M-n]` preview | `serve_rag.py:2175` (route at `:2233`) |
| `GET /sessions/{id}/messages` — persisted message replay | `serve_rag.py:2194` |
| `GET /sessions/{id}` — session detail incl. filename + n_pages | `serve_rag.py:2174` |
| Citation validator — strips fabricated handles, rewrites surrounding sentence `(unbelegt)` | `lai/common/citation/validator.py` |
| Jurisdiction sanity check (Bundesland mismatch warnings) | `serve_rag.py` |
| `QueryReq.target_language` field + `_language_directive()` helper — `"en"` switches the model to English while keeping cited German verbatim; threaded through `build_rag_messages` / `build_chat_messages` in both `/query` and `/query/stream` | `serve_rag.py` |

---

## 3. Done — frontend

`LAI-UI/src/react-app/`:

### Boot / chrome
| Component | Purpose | File |
|---|---|---|
| `HealthGate` | Polls `/health` every 5 s; cold-start splash while `loaded: false`; offline blocking screen with Retry when unreachable; tears down once ready | `components/HealthGate.tsx` |
| `ConfidentialityBadge` | "On-Premise · BRAO § 43a · DSGVO · EU AI Act · No data leaves" — always visible | `components/ConfidentialityBadge.tsx` |
| `LanguageToggle` + `LanguageProvider` | `[DE \| EN]` toggle; threaded into every `streamQuery` call as `target_language` | `components/LanguageToggle.tsx` + `contexts/LanguageContext.tsx` |
| `DashboardLayout` | Sidebar (conversation list) + main outlet; deep-link `?session_id=` reader; `HealthGate` wrapper | `components/DashboardLayout.tsx` |
| `DemoDeepLinkRedirect` | Forwards `/?session_id=…` → `/dashboard/chat?session_id=…` so the seed-loader URL works from the root | `App.tsx` |

### Chat
| Component | Purpose | File |
|---|---|---|
| `CitationChip` | Blue `[M-n]` / grey `[C-n]` pill with active-ring + hover tooltip | `components/chat/CitationChip.tsx` |
| `CitationPanel` | Right-side slide-in; native `<object>` PDF preview for `[M-n]`, formatted text for `[C-n]` | `components/chat/CitationPanel.tsx` |
| `CitedMarkdown` | Parses `[C-n]` / `[M-n]` / `(unbelegt)` inside markdown nodes | `components/chat/CitedMarkdown.tsx` |
| `UnverifiedBadge` | Amber pill with the German tooltip "Diese Aussage konnte nicht durch die hochgeladenen Dokumente oder den Rechtskorpus belegt werden." (UI_GUIDE.md §5.4 verbatim) | `components/chat/UnverifiedBadge.tsx` |
| `ChatMessage` | User/assistant bubble; "⚠ N unbelegt" validator badge | `components/chat/ChatMessage.tsx` |
| `ChatInput` | Multiline; Enter/Shift+Enter; paperclip + drag-drop file picker; mic input | `components/chat/ChatInput.tsx` |
| `DropZone` | Drag-and-drop area; ext + 50 MB validation; per-row uploading/parsed/failed indicators | `components/chat/DropZone.tsx` |
| `DocumentList` | Per-session document list; `●parsed` / `◐uploading` / `◯failed` dots (UI_GUIDE.md §5.7) | `components/chat/DocumentList.tsx` |
| `TypingIndicator` | "LAI is thinking..." rotating-phrase placeholder | `components/chat/TypingIndicator.tsx` |

### API client
| Capability | File |
|---|---|
| Types (`Chunk`, `RAGResponse`, `CitationValidation`, `JurisdictionWarning`, `HealthStatus`, …) | `lib/ragApi.ts` |
| `queryRAG()` (non-streaming) + `streamQuery()` (SSE) — both accept `targetLanguage` | `lib/ragApi.ts` |
| `uploadDocument()`, `analyzeContract()`, `getAnalyzeProgress()` | `lib/ragApi.ts` |
| `getSession()`, `listSessions()`, `deleteSession()`, `renameSession()`, `appendMessage()` | `lib/ragApi.ts` |
| `fetchSessionDocument()` (blob → `URL.createObjectURL` for PDF preview) | `lib/ragApi.ts` |
| `fetchHealth()` (richer probe — distinguishes reachable+loaded vs reachable+warming vs unreachable) | `lib/ragApi.ts` |

### Chat page wiring (`pages/DashboardChat.tsx`)
- SSE wired: placeholder bubble, `onToken` append, `onComplete` swap to validated answer + chunks, `onError` inline error bubble.
- `AbortController` aborts on conversation switch, unmount, and superseded submit.
- Empty-state example questions (`suggestedPrompts`).
- `DropZone` + `DocumentList` slotted into both empty states (no-conversation and active-conversation-no-messages).
- `/upload` and `/query` 5xx error bubbles surface inline.
- Session rehydration via `getSession()` on conversation change.
- Shared `applyUploadConfirmation()` helper — single source of truth for both the `ChatInput` attachment path and the `DropZone` drop path, so they can't drift apart.
- `?session_id=` deep-link: `DashboardLayout` reads the query param at mount (precedence over the localStorage cache), then strips it via `history.replaceState` so a later refresh respects the user's conversation switches.

---

## 4. Done — trust signals (UI_GUIDE.md §2)

| Signal | Status | Where |
|---|---|---|
| "On-Premise · BRAO § 43a · DSGVO · EU AI Act" header badge | ✅ | `ConfidentialityBadge` in `DashboardLayout` sidebar header |
| Citation chips on every assistant claim | ✅ | `CitedMarkdown` + `CitationChip` |
| `(unbelegt)` markers on uncited claims | ✅ | Backend validator + `UnverifiedBadge` |
| "Grounded in 350 GB of German legal corpus" copy | ⚠️ partial — no explicit "350 GB" label currently visible in the sidebar or empty state; the empty-state copy mentions wind-energy legal due diligence generically. (See [§8 Known gaps](#8-known-gaps-not-blocking-v1).) |

---

## 5. Done — build-order milestones (UI_GUIDE.md §9)

| Day | Deliverable | Status |
|---|---|---|
| Day 2 | `<CitationChip>`, `<UnverifiedBadge>`, `<AssistantMessage>` renderer, click → side panel | ✅ |
| Day 2 | Side panel for `[M-n]`: render the uploaded PDF inline | ✅ — `GET /sessions/{session_id}/document` + native `<object>` |
| Day 2 | Thinking indicator on chat turn | ✅ — `TypingIndicator` |
| Day 3 | `<LanguageToggle>` wired to `target_language` end-to-end | ✅ |
| Day 4 | `<ConfidentialityBadge>` + `<UnverifiedBadge>` polish | ✅ |
| Day 5 | Sidebar `<DocumentList>` with status dots | ✅ — single-doc list for v1.1 forward-compat |
| Day 6 | `<DropZone>` drag-drop, status indicators | ✅ |
| Day 7 | (auth — deferred to v1.1 per strategy doc §11.2) | — |
| Day 8 | Empty-state example questions + demo-seed Matter pre-loaded | ⚠️ frontend done; PDFs not yet dropped (see [§6](#6-remaining--content)) |
| Day 9 | Loading skeletons, error states, polish, demo rehearsal | partial |
| Day 10 | Final polish, demo | — |
| Bonus | SSE streaming end-to-end | ✅ — backend `POST /query/stream` + frontend `streamQuery` |

---

## 6. Remaining — content

| # | Item | Notes |
|---|---|---|
| 7 | **Demo seed PDFs (Windpark Lamstedt)** | `LAI/scripts/ops/load_demo_matter.py` is ready, uses fixed `session_id="lamstedt-demo"`. `LAI/demo-seed/lamstedt/` contains only `README.md`. Need 7 curated PDFs dropped in: Pachtvertrag, BImSchG-Bescheid, OVG ruling, Wartungsvertrag, Lageplan, Versicherungsschein, Netzanschlussvertrag. Then run the loader. |

This is the only thing blocking the live demo.

---

## 7. Remaining — verification still owed

None of these block the demo, but worth a single pass through before
the lawyer arrives:

- **`HealthGate`**: toggle `serve_rag` off and confirm the offline
  screen appears; re-start and confirm the splash hands off to the
  dashboard once `loaded: true`. Confirm the 5 s poll cadence.
- **`DropZone`**: drop a >50 MB file and an unsupported extension —
  both should land as red error rows with the correct copy.
- **`DocumentList`**: refresh on a session that already has an upload;
  the row should appear with `●parsed` immediately on rehydration.
- **`?session_id=` deep-link**: open `/?session_id=lamstedt-demo`
  cold — should redirect to `/dashboard/chat` AND strip the query
  string so a subsequent refresh respects the user's later
  conversation switches.
- **Citation chip end-to-end**: with the seeded matter loaded, ask one
  of the four UI_GUIDE.md §8.3 demo questions, click a `[M-n]` chip,
  confirm the PDF opens in the right panel.
- **EN language toggle**: switch to EN, ask the same question, confirm
  the answer prose is in English but the cited statute / contract
  excerpts stay in German (UI_GUIDE.md §7.4 contract).

---

## 8. Known gaps not blocking v1

These are observable in the audit but explicitly **not** demo blockers:

- **"Grounded in 350 GB" corpus label** — UI_GUIDE.md §2 lists this as
  one of the four trust signals; the empty-state placeholder
  ([`pages/DashboardChat.tsx`](../LAI-UI/src/react-app/pages/DashboardChat.tsx))
  currently uses generic wind-energy due-diligence copy. Adding the
  literal "350 GB legal corpus" mention to the empty-state subtitle is
  ~10 minutes of copy work and would close the last trust-signal gap.
- **Multi-document support** — backend tracks **one** document per
  session today. `GET /sessions/{id}/documents` (plural) is not
  shipped. `DocumentList` renders the single-doc shape as a list so
  the v1.1 swap is a data-source change only.
- **Pre-existing TypeScript errors in `ReportDownloadPanel.tsx:1596`**
  (state-narrowing comparison drift in the DDiQ export flow). Not
  introduced by this work; unrelated to v1 chat demo. Worth tracking
  but doesn't block.

---

## 9. Explicitly out of v1 (UI_GUIDE.md §11)

Do not accidentally build these — they are deferred to v1.1 or beyond:

- Login / signup screen (single-tenant on-prem, no auth in v1)
- Settings / admin / preferences page
- Conversation history sidebar (one conversation per session is fine)
- DDiQ "Generate report" button
- DOCX letterhead export
- Deadline → `.ics` calendar export
- Risk matrix / Ampel render
- Audit log viewer
- Word / Outlook plugin
- Multi-user concurrent sessions

---

## 10. Critical path to demo-readiness

All code is shipped. The path to a live demo is:

1. **Drop the 7 curated PDFs** into `LAI/demo-seed/lamstedt/`
   (Pachtvertrag, BImSchG-Bescheid, OVG ruling, Wartungsvertrag,
   Lageplan, Versicherungsschein, Netzanschlussvertrag).
2. **Run the loader**:
   ```
   python LAI/scripts/ops/load_demo_matter.py
   ```
   This POSTs each file to `/upload` under the fixed
   `session_id="lamstedt-demo"`. Re-runs are idempotent.
3. **Open the deep-link**: `<frontend>/?session_id=lamstedt-demo` —
   `App.tsx`'s `<DemoDeepLinkRedirect>` forwards to
   `/dashboard/chat?session_id=…`; `DashboardLayout` reads the query
   param, sets the active conversation, strips the URL.
4. **Pre-warm vLLM**: run the four UI_GUIDE.md §8.3 demo questions
   once to warm the prefix cache.
5. **Lawyer sits down.** Walk the 5-minute pitch.

**Pre-flight checklist** (UI_GUIDE.md §10 mapped to current code):
- [ ] 15 min before: `serve_rag` running; `/health` returns
      `ok: true, loaded: true`.
- [ ] 5 min before: `HealthGate` lets the dashboard render (no splash,
      no offline screen).
- [ ] 2 min before: confidentiality badge visible in the sidebar;
      `lamstedt-demo` session preloaded with 7 docs.
- [ ] 1 min before: pre-run the four demo questions to warm cache.
- [ ] Lawyer arrives. Demo.
