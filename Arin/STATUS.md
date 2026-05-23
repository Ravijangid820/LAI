# LAI v1 — Implementation Status

**Date:** 2026-05-18
**Source of truth:** [`UI_GUIDE.md`](UI_GUIDE.md)
**Audit basis:** read of `LAI-UI/src/react-app/` + `LAI/src/lai/api/serve_rag.py`

> **Update 2026-05-18:** All six remaining frontend code items shipped this session — see "Recently shipped (2026-05-18)" below. The only outstanding v1 work is **content** (#7: the seven curated demo PDFs).

---

## ✅ Done

### Backend (`LAI/src/lai/api/serve_rag.py`)
- `POST /query` — non-streaming chat with `[C-n]`/`[M-n]` chunks
- `POST /query/stream` — SSE: `event: token` deltas + terminal `event: complete` with validated answer + chunks (line 1607)
- `POST /upload` — PDF/DOCX upload; Docling parse
- `GET /health` — `{ok, loaded, llm_model}`
- `GET /sessions/{session_id}/document` — raw PDF bytes for `[M-n]` preview (line 2175)
- `GET /sessions/{id}/messages` — persisted message replay
- Citation validator (`validate_citations`) — strips fabricated handles, rewrites surrounding sentence `(unbelegt)`
- Jurisdiction sanity check (Bundesland mismatch warnings)
- `QueryReq.target_language` field + `_language_directive()` helper — `"en"` switches model to English while keeping cited German verbatim; threaded through `build_rag_messages` / `build_chat_messages` in both `/query` and `/query/stream`

### Frontend (`LAI-UI/src/react-app/`)
| Component | File |
|---|---|
| `ConfidentialityBadge` | `components/ConfidentialityBadge.tsx` |
| `LanguageToggle` + `LanguageProvider` | `components/LanguageToggle.tsx` + `contexts/LanguageContext.tsx` |
| `CitationChip` | `components/chat/CitationChip.tsx` |
| `CitationPanel` (PDF preview via native `<object>`) | `components/chat/CitationPanel.tsx` |
| `CitedMarkdown` (parses `[C-n]`/`[M-n]`/`(unbelegt)`) | `components/chat/CitedMarkdown.tsx` |
| `UnverifiedBadge` (German tooltip, §5.4) | `components/chat/UnverifiedBadge.tsx` |
| `ChatMessage` (with "⚠ N unbelegt" badge) | `components/chat/ChatMessage.tsx` |
| `ChatInput` (file picker + multiline submit) | `components/chat/ChatInput.tsx` |
| `DropZone` (drag-and-drop, 50 MB cap, ext validation) | `components/chat/DropZone.tsx` |
| `DocumentList` (single-doc list w/ `●parsed` / `◐uploading` / `◯failed`) | `components/chat/DocumentList.tsx` |
| `HealthGate` (cold-start splash + offline blocking screen, 5 s polling) | `components/HealthGate.tsx` |
| `TypingIndicator` | `components/chat/TypingIndicator.tsx` |
| `ragApi.ts` types + `streamQuery` + `queryRAG` (both accept `targetLanguage`) + `fetchHealth()` | `lib/ragApi.ts` |
| SSE wired into `DashboardChat` — placeholder bubble, `onToken` append, `onComplete` swap to validated answer + chunks, `onError` inline error, `AbortController` aborts on conversation switch / unmount / superseded submit | `pages/DashboardChat.tsx` |
| Empty-state example questions (`suggestedPrompts`) + `DropZone` + `DocumentList` slotted into empty states | `pages/DashboardChat.tsx` |
| `/upload` and `/query` 5xx error bubbles | `pages/DashboardChat.tsx` |
| Session rehydration via `getSession()` | `pages/DashboardChat.tsx` |
| `?session_id=` deep-link (root → `/dashboard/chat` redirect + mount-time query-param read, then stripped via `history.replaceState`) | `App.tsx` + `components/DashboardLayout.tsx` |
| Shared `applyUploadConfirmation` helper — single source of truth for both `ChatInput` and `DropZone` upload paths | `pages/DashboardChat.tsx` |

### Trust signals (UI_GUIDE.md §2)
- "On-Premise · BRAO § 43a · DSGVO · EU AI Act" header badge ✅
- Citation chips on every assistant claim ✅
- `(unbelegt)` markers on uncited claims ✅
- Corpus messaging (sidebar / empty state placeholder) — partial, no explicit "grounded in 350 GB" label visible

---

## ❌ Remaining — code

All six code items from this list shipped on 2026-05-18. See "Recently shipped" below.

### Recently shipped (2026-05-18)

| # | Item | Guide § | Where |
|---|---|---|---|
| 1 | **`<DropZone>`** drag-and-drop area | §5.8 / Day 6 | `components/chat/DropZone.tsx` — accepts `.pdf/.doc/.docx/.xlsx/.xls/.txt/.csv/.md`, validates 50 MB cap + extension, shows per-row uploading/parsed/failed status. Slotted into both empty states of `DashboardChat.tsx`. |
| 2 | **`<DocumentList>`** with status dots | §5.7 / Day 5 | `components/chat/DocumentList.tsx` — fetches from `GET /sessions/{id}` (single-doc backend reality), renders as a list for v1.1 forward-compat. `●parsed` / `◐uploading` / `◯failed` dots. Multi-doc still blocked on `GET /sessions/{id}/documents`. |
| 3 | **Cold-start splash** polling `/health` | §8.2 | `components/HealthGate.tsx` — wraps `DashboardLayout`'s render. New `fetchHealth()` helper returns `{ reachable, status }` so the gate can distinguish "warming" from "offline". Polls every 5 s, tears down on `loaded: true`. |
| 4 | **Backend-offline blocking screen** | §8.4 | Same `HealthGate` — `reachable: false` renders the offline message with a Retry button. |
| 5 | **`<UnverifiedBadge>` as a dedicated component** | §5.4 | `components/chat/UnverifiedBadge.tsx` — German tooltip from §5.4 verbatim; replaces the inline pill that used to live in `CitedMarkdown.tsx`. |
| 6 | **`?session_id=...` deep-link** | §9 Day 8 | `App.tsx` `<DemoDeepLinkRedirect>` forwards `/?session_id=…` → `/dashboard/chat?session_id=…`; `DashboardLayout` reads the query param at mount (prefers it over the localStorage cache) and strips it from the URL via `history.replaceState`. |

---

## ⚠️ Remaining — content (not code)

| # | Item | Notes |
|---|---|---|
| 7 | **Demo seed PDFs (Windpark Lamstedt)** | `LAI/scripts/ops/load_demo_matter.py` is ready and uses fixed `session_id="lamstedt-demo"`. `LAI/demo-seed/lamstedt/` contains only `README.md`. Need 7 curated PDFs (Pachtvertrag, BImSchG-Bescheid, OVG ruling, Wartungsvertrag, Lageplan, Versicherungsschein, Netzanschlussvertrag) dropped in, then run the loader. |

---

## 🚫 Explicitly out of scope (UI_GUIDE.md §11)

Don't accidentally build these — they are deferred to v1.1 or beyond:

- Login / signup, settings page, conversation history sidebar
- DDiQ "Generate report" button, DOCX letterhead export
- Deadline → `.ics` calendar export, risk matrix / Ampel render
- Audit log viewer, Word / Outlook plugin
- Multi-user concurrent sessions

---

## Critical path to demo-readiness

All six code items shipped. The **only** blocker remaining for the
v1 demo is content delivery:

1. Drop the 7 curated PDFs into `LAI/demo-seed/lamstedt/` (Pachtvertrag,
   BImSchG-Bescheid, OVG ruling, Wartungsvertrag, Lageplan,
   Versicherungsschein, Netzanschlussvertrag).
2. Run `python LAI/scripts/ops/load_demo_matter.py` — uploads them under
   the fixed `session_id="lamstedt-demo"`.
3. Open `<frontend>/?session_id=lamstedt-demo` — the deep-link wired in
   `App.tsx` forwards to `/dashboard/chat` and `DashboardLayout`
   rehydrates the matter.

### Verification still owed (none of these block demo, but worth a pass)

- `HealthGate`: confirm the 5 s poll cadence by toggling `serve_rag`
  off and watching the offline screen appear, then re-starting and
  confirming the splash hands off to the dashboard once
  `loaded: true`.
- `DropZone`: drop a >50 MB file and an unsupported extension — both
  should land as red error rows with the correct copy.
- `DocumentList`: refresh on a session that already has an upload;
  the row should appear with `●parsed` immediately on rehydration.
- `?session_id=` deep-link: open `/?session_id=lamstedt-demo` cold —
  should redirect to `/dashboard/chat` AND strip the query string so
  a subsequent refresh respects the user's later conversation switches.
