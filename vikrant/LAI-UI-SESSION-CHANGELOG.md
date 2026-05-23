
# LAI Web App — Frontend Work Log

**Scope:** Frontend only (`/data/projects/lai/LAI-UI`). No backend Python was modified — backend behavior was read for understanding only, per the standing constraint not to override teammates' work.

**Stack:** React 19 + Vite 7 + TypeScript, Tailwind CSS (semantic theme tokens), Hono/Cloudflare Workers, SSE streaming, served same-origin via the Vite dev proxy (`/rag` → 127.0.0.1:18000, `/ddiqsvc` → 18001).

**Verification gate used throughout:** `npx tsc --noEmit` (clean), `npx eslint` (0 errors; one pre-existing `exhaustive-deps` warning at `DashboardChat.tsx` is intentional and untouched), and `npm run build` (green).

---

## 1. Chat actions wired up & composer cleanup

**Files:** `components/chat/ChatMessage.tsx`, `components/chat/ChatInput.tsx`, `components/chat/NotificationsMenu.tsx` (new), `components/chat/UploadProgress.tsx` (new), `pages/DashboardChat.tsx`

- **Copy button** — was silently failing because `navigator.clipboard` is `undefined` on an insecure origin (http over the SSH tunnel). Added a `window.isSecureContext` guard with a hidden-`textarea` + `execCommand("copy")` fallback.
- **Regenerate button** — wired to a shared `streamAnswerInto` helper so the normal `/query` path and Regenerate can't drift.
- **Notifications** — new `NotificationsMenu` (bell + unread badge, localStorage-backed, seeded product tips). Rendered via a `createPortal` fixed-position panel so the chat's `overflow-hidden` root can't clip it.
- **Multi-file upload progress** — new `UploadProgress` panel above the composer: per-file status (pending → uploading → done/error) and an overall counter, instead of bubbles silently appearing one by one.
- **Removed the mic / voice-input feature** from the composer.
- **Attachment Download** button wired (object URL + anchor), hidden on replayed messages with no file handle.

---

## 2. Three reported chat bugs (root-caused)

**Files:** `components/chat/ChatMessage.tsx`, `components/chat/NotificationsMenu.tsx`, `pages/DashboardChat.tsx`

- **"Two thinking LAI assistants"** — every send added an *empty* streaming assistant bubble (avatar + name) **and** the standalone `TypingIndicator` (a second avatar). Fix: an assistant bubble that is streaming with empty content now renders nothing, so only the typing indicator shows; it hands off to the filling bubble on the first token.
- **"Two user inputs" for document + text** — the attachment rendered as its own box and the text as a separate bubble below. Fix: a user turn now renders the attachment chip(s) **inside the same primary bubble** as the text — one cohesive input.
- **Notification panel "overlapping" the chat** — added a soft `bg-black/20` backdrop so the panel reads as a layer above the chat (click-to-close), plus a crisper panel (`shadow-2xl`, `ring-1`).

---

## 3. Favicon 404 + stream stall watchdog

**Files:** `index.html`, `lib/ragApi.ts`

- **Favicon 404** — `index.html` had no icon link. Added an inline SVG (scales-of-justice, brand cyan/violet) as a data-URI so there's no file to 404 on.
- **"LAI only thinking, never responds"** — traced the full stack (backend warm, proxy + auth + SSE + parser all verified end-to-end). The real defect: the stream had **no timeout**, so any stall hung forever. Added a **60-second stall watchdog** in `streamQuery` that arms before the request, re-arms on every token, and on a stall aborts + surfaces an actionable error instead of an eternal spinner. A `settled` guard prevents any terminal handler firing twice.

---

## 4. Edit / Stop / Drop-to-compose

**Files:** `lib/ragApi.ts`, `components/chat/ChatInput.tsx`, `components/chat/ChatMessage.tsx`, `components/chat/DropZone.tsx`, `pages/DashboardChat.tsx`

- **Stop / pause generation** — `streamQuery` gained a clean `onAbort` terminal handler (aborting now resolves the promise and finalizes the partial answer instead of hanging). `DashboardChat` tracks `isStreaming`; the composer's Send button becomes a red **Stop (■)** while generating, keeping whatever text already streamed (appends a quiet "⏹ Stopped").
- **Edit a user message** — user bubbles get an inline **Edit** (pencil) action: editing truncates the stale answer and re-streams a fresh response (standard edit-and-resubmit).
- **Drag-and-drop = attach, not auto-send** — the empty-state `DropZone` previously uploaded instantly and pushed a bare user turn. It now feeds files into the **composer** (attachment state lifted up into `DashboardChat`), so the user can add a question and send one combined turn, exactly like the paperclip.

---

## 5. Follow-up bug fixes

**Files:** `components/chat/ChatInput.tsx`, `pages/DashboardChat.tsx`, `lib/ragApi.ts`, `components/chat/ChatMessage.tsx`, `components/project/types.ts`, `components/project/ProjectChatView.tsx`, `pages/DashboardProjects.tsx`

- **`ChatInput` crash** (`Cannot read properties of undefined (reading 'length')`) — from a wedged HMR state. Made `attachments` default to `[]` so the component can't crash mid-reload.
- **"output stopped" on document + text** — uploading mints a session → `setActiveConversationId` fired the "abort stream on conversation change" effect, killing the in-flight answer. Fix: that abort effect now skips our **own** programmatic session switches (reuses the `skipNextRehydrate` flag); it still aborts on genuine user navigation.
- **"Document still processing" placeholder** — the German `⏳` message and the long wait are the **backend's** wait-and-answer (it waits for OCR/ingestion, then auto-answers). Fix on our side: `streamQuery` now parses the `status` SSE event (`onStatus`); the chat shows a clean **"Dokument wird verarbeitet… Seite X/Y"** spinner indicator (a `processingNote` on the message) and filters out the transient `⏳` token and empty heartbeats. Applied to both the main chat and the project chat.

---

## 6. Projects — file uploads now reach the backend

**Files:** `components/project/types.ts`, `pages/DashboardProjects.tsx`, `components/project/ProjectFileGrid.tsx`

- **Root cause:** the project file section's `handleAddFiles` only stored local metadata — it **never uploaded** to the backend, and each conversation had its own isolated session, so the chat queried an empty session ("no document attached").
- **Fix — a project-level matter session:**
  - `Project` gained a `sessionId`; `ProjectFile` gained `status` (`uploading`/`ready`/`error`).
  - `handleAddFiles` now uploads each file to the project's session (threading the id across the batch) and pins the session on the project.
  - `runConversationTurn` queries the **project session** (so chat reads file-section documents); new conversations inherit it; the id is kept in sync on both project and conversation.
  - `ProjectFileGrid` shows per-file status (spinner / green check / red alert).

---

## 7. Sidebar redesign (Claude-style)

**File:** `components/DashboardLayout.tsx`

- Rebuilt with real lucide icons and a `SidebarItem` helper for pixel-consistent rows. Refined active state (soft `bg-sidebar-accent` + primary-colored icon, not a heavy fill); focus rings; collapsed icon-only mode preserved.
- **Order (top → bottom):** Dashboard → Projects → Chat → Documents → Risk Assessment → **New chat** → **Recents** (previous chats, now always visible, titles only, scrollable, with hover Rename/Delete) → **Support** (Settings, Guided Tour) → **account**, pinned at the bottom.
- Conversations open from any page (navigate to chat + select). Logo links home.

---

## 8. Light theme as default

**Files:** `index.html`, `contexts/ThemeContext.tsx`

- `index.html` → `class="light"`; `ThemeContext` default → `stored || "light"` (a saved preference still wins). Reviewed light theme: the app is built on semantic tokens that adapt automatically; the only hardcoded-dark spots are intentional (gradient avatars, modal backdrops, landing page).

---

## 9. Global top header

**Files:** `components/AppHeader.tsx` (new), `components/DashboardLayout.tsx`, `pages/DashboardChat.tsx`

- New `AppHeader` shown on every dashboard page: left = page glyph + title + subtitle (per route; on chat the title is the active conversation); right = notification bell + theme toggle, now collected in one place.
- Moved the theme toggle **out of** the sidebar and **removed the chat page's duplicate header** so there's exactly one header.
- (A Dribbble template reference was requested but the link couldn't be read — Dribbble shots are images — so the header is a clean, neutral foundation pending a screenshot/description to match a specific template.)

---

## 10. Risk Overview — real, document-centric risk register

**Files:** `components/RiskOverview.tsx` (new), `pages/DashboardRisk.tsx`

- The "Risk Overview" tab was a hardcoded empty placeholder. Replaced with a real component that aggregates the **actual findings** from completed DDiQ reports (the same reports whose `finding_count` feeds the dashboard's Risk card): fetches the report list, keeps `done` reports, fetches each full report in parallel, and collects findings (incl. cross-document) tagged with their source matter.
- **Document source attribution** — each risk shows the source document(s) from its evidence (`doc_filename` + page), de-duplicated, with a file icon, plus the matter name.
- **Reworked layout:**
  - **Total at the top** — combined risk count across all documents + High/Medium/Low breakdown.
  - **Document selector** — filter pills ("All documents" first, then per-document with counts and a red dot if any High-severity), worst-first.
  - **Concise per-document risks** — severity dot · the risk text · meta (domain · page · legal basis) · severity badge; in the "All" view the source file shows inline (with `+N` for multi-document findings).

---

## 11. Projects — clear Back button

**File:** `components/project/ProjectChatView.tsx`

- Inside a project chat the back control only showed the project name (`← ProjectName`) and was easy to miss. Replaced with an explicit **"← Back"** button (hover state) plus a breadcrumb (**Back / Project / Chat title**), so from any chat there's an obvious way back to the conversation list.

---

## Notes / known follow-ups

- **Edit-and-resubmit** rewrites the live session view; the server still holds the original turns, so a hard reload replays the pre-edit thread (matching server-side would need a backend endpoint, intentionally left untouched).
- **Existing project files** added before §6 were never uploaded to the backend — re-add them to upload properly.
- If a saved `lai-theme: "dark"` exists in a browser from earlier testing, toggle once (or clear the key) to see the new light default.
- The Dribbble-template header match is pending a readable reference (screenshot in-repo or a written description).
