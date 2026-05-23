
# LAI v1 — UI Guide

**Date:** 2026-05-17
**Audience:** the frontend engineer building `LAI-UI/`
**Purpose:** every screen, every interaction, and every backend hook you need
to ship the v1 demo. Reads stand-alone — you do not need to read the
strategy doc, the BUILD_PROGRESS, or the architecture doc to use this.

This is the UI companion to [`LAI_V1_STRATEGY.md`](LAI_V1_STRATEGY.md). The
strategy doc explains *why* the v1 looks the way it does (the lawyer's
feedback, the four USPs, the 10-day plan); this doc explains *how* to build
it.

---

## Status snapshot — 2026-05-17 audit (re-verified)

Re-audited against `LAI-UI/src/react-app/` and
`LAI/src/lai/api/serve_rag.py`. The prior snapshot was stale — several
"blocked" items have actually shipped on the backend; what remains is
mostly frontend wire-up plus the seed PDFs.

| Component | Status | Evidence |
|---|---|---|
| `CitationChip` (indigo/amber pills, active-ring) | ✅ shipped | `components/chat/CitationChip.tsx` |
| `CitationPanel` (slide-in side panel) | ✅ shipped | `components/chat/CitationPanel.tsx` |
| `CitedMarkdown` (parses `[C-n]`/`[M-n]`/`(unbelegt)`) | ✅ shipped | `components/chat/CitedMarkdown.tsx` |
| `ChatMessage` with "⚠ N unbelegt" badge | ✅ shipped | `components/chat/ChatMessage.tsx` |
| `ConfidentialityBadge` | ✅ shipped | `components/ConfidentialityBadge.tsx` |
| `LanguageToggle` + `LanguageProvider` | ✅ shipped | `components/LanguageToggle.tsx` + `contexts/LanguageContext.tsx` |
| `ragApi.ts` types (`Chunk.cite_id`, `source_kind`, `citation_validation`) | ✅ shipped | `lib/ragApi.ts` |
| **PDF preview for `[M-n]` in the side panel** | ✅ shipped | backend `GET /sessions/{session_id}/document` at `serve_rag.py:2175`; frontend `CitationPanel` fetches via `fetchSessionDocument` and renders in a native `<object>` tag (no `react-pdf` dep) |
| **SSE streaming backend** | ✅ shipped | `POST /query/stream` at `serve_rag.py:1607`, `StreamingResponse(media_type="text/event-stream")` at line 1826 |
| **SSE streaming — frontend wired** | ✅ shipped | `DashboardChat.tsx` now calls `streamQuery()` with placeholder-bubble + `onToken`/`onComplete`/`onError` handlers; `streamAbortRef` aborts on conversation switch + unmount; typing indicator hides on first token |
| **`target_language` end-to-end** | ✅ shipped | backend `QueryReq.target_language` + `_language_directive()` helper in `serve_rag.py`; threaded through both `build_rag_messages` and `build_chat_messages` in `/query` and `/query/stream`; frontend `streamQuery()` / `queryRAG()` accept `targetLanguage`; `DashboardChat` reads `useLanguage()` and passes it on every turn |
| **Demo seed Matter ("Windpark Lamstedt")** | ⚠️ infra ready, content missing | loader script at `LAI/scripts/ops/load_demo_matter.py` uses fixed `session_id="lamstedt-demo"`; seed dir `LAI/demo-seed/lamstedt/` contains only `README.md` — no curated PDFs yet |

**TL;DR:** the two code gaps are closed. The only remaining v1 demo
work is content delivery — dropping the 7 curated PDFs
(Pachtvertrag, BImSchG-Bescheid, OVG ruling, Wartungsvertrag,
Lageplan, Versicherungsschein, Netzanschlussvertrag — full list in
`load_demo_matter.py` header) into `LAI/demo-seed/lamstedt/` and
running the loader. Then verify the frontend reads
`?session_id=lamstedt-demo` from the query string for deep-linking
(small follow-up if not yet wired).

Build-order Days 5–8 items (multi-doc `DocumentList` with status polling,
`DropZone`, `GET /sessions/{id}/documents` listing, empty-state example
questions) were not re-audited in this pass and may have shipped too —
verify before quoting.

The rest of this guide stays useful as the contract between frontend and backend.

---

## Table of contents

1. [What we're building, in one paragraph](#1-what-were-building)
2. [The four trust signals that must be visible from second 1](#2-trust-signals)
3. [Layout & screens (with ASCII mockups)](#3-layout--screens)
4. [Backend reference — exact endpoints, request/response shape](#4-backend-reference)
5. [Components, one by one](#5-components-one-by-one)
6. [State management](#6-state-management)
7. [Citation chips — the demo's killer feature](#7-citation-chips)
8. [Streaming, loading, and error states](#8-streaming-loading-error-states)
9. [Build order — what to ship each day of the sprint](#9-build-order)
10. [Demo-day operations](#10-demo-day-operations)
11. [What is explicitly out of v1](#11-out-of-v1)

---

## 1. What we're building

> A web app that opens in a German lawyer's browser, shows a chat box, lets
> them upload a contract PDF, ask a question in German or English, and get
> an answer back where every sentence has a clickable citation chip linking
> to either (a) the exact paragraph in their uploaded PDF or (b) the exact
> source in our 350 GB legal corpus.

That's it. No login screen for v1, no settings page, no admin panel. One
working screen. The lawyer should be able to:

1. Open the page.
2. See immediately that this is an on-premise German legal tool.
3. Drop a PDF.
4. Type a question.
5. See an answer in <15 seconds with clickable citations.
6. Click a citation, see the source in a side panel.

If those six steps work, the demo works.

---

## 2. Trust signals

These four signals must be visible **without scrolling, without clicking**,
on the very first screen the lawyer sees. The lawyer's v0 dismissal was
30 seconds — you have 30 seconds.

| Signal | Where it goes |
|---|---|
| **"On-Premise · BRAO § 43a · DSGVO · EU AI Act"** badge | Top right corner, always visible |
| **The model is grounded in 350 GB of German legal corpus** | Sidebar header label or empty-state placeholder text |
| **Every claim has a [C-n] / [M-n] citation chip** | Inside assistant messages — see §7 |
| **Uncited claims explicitly marked "(unbelegt)"** | Inside assistant messages |

Avoid: any footer reading *"This output does not substitute legal review"*
— the strategy doc identifies this as a credibility-breaker. (The wording
depends on a legal review still pending; treat it as "do not show by
default".)

---

## 3. Layout & screens

### 3.1 Primary screen

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAI                          [DE | EN]                On-Premise · BRAO §43a │
├──────────────────────────────────────────────────────────────────────────────┤
│  MANDATE WORKSPACE                       │                                   │
│  ──────────────────                      │     ┌─[M-1] Pachtvertrag.pdf ─┐  │
│  Windpark Lamstedt                       │     │ page 7, § 7              │  │
│  Niedersachsen · 4 docs                  │     │                          │  │
│                                          │     │ Der Pächter hat die      │  │
│  Documents (drop or click)               │     │ Anlage bei Vertragsende  │  │
│  ┌──────────────────────────┐            │     │ vollständig zurück...    │  │
│  │ pachtvertrag.pdf  ●parsed │           │     └──────────────────────────┘  │
│  │ bimschg.pdf       ●parsed │           │                                   │
│  │ ovg-urteil.pdf    ●parsed │           │     [Click a citation to open]    │
│  │ wartungsvertrag…  ●parsed │           │                                   │
│  └──────────────────────────┘            │                                   │
│                                          │                                   │
├──────────────────────────────────────────┤                                   │
│  CHAT                                    │                                   │
│  Lawyer: Ist die Rückbauverpflichtung    │                                   │
│  im Vertrag ausreichend?                 │                                   │
│                                          │                                   │
│  LAI: § 7 des Pachtvertrags [M-1]        │                                   │
│  verlangt vollständigen Rückbau bei      │                                   │
│  Vertragsende. § 35 Abs. 5 BauGB [C-3]   │                                   │
│  fordert zusätzlich eine                 │                                   │
│  Rückbausicherheit, die im Vertrag       │                                   │
│  jedoch nicht geregelt ist (unbelegt).   │                                   │
│                                          │                                   │
│  [Type a question...]                    │                                   │
└──────────────────────────────────────────┴───────────────────────────────────┘
```

Three regions, fixed:

- **Header strip** (40px): logo · language toggle · confidentiality badge.
- **Left panel** (320px): mandate / matter workspace — the documents the
  lawyer has uploaded.
- **Center** (flexible): chat thread + input box.
- **Right panel** (480px, slides in): citation preview. Hidden by default;
  opens when the lawyer clicks a `[C-n]` or `[M-n]` chip.

### 3.2 Why three regions and not two

The lawyer's "is this a real product?" judgment comes from how the answer
sits next to its sources. A two-column layout (chat alone, then a modal
for citations) reads like a toy. Three columns — documents on the left,
chat in the middle, source preview on the right — reads like Westlaw or
Beck-online, which is the visual language the lawyer already trusts.

---

## 4. Backend reference

The backend lives at `http://localhost:18000` (host-mode) or
`http://lai_backend:18000` (Docker). Only **three endpoints** matter for v1.

### 4.1 `POST /query` — chat turn

The single biggest endpoint. Sends the user's question, returns the
assistant's answer + citation chunks.

**Request body:**
```json
{
  "question": "Ist die Rückbauverpflichtung ausreichend?",
  "session_id": "abc-123",
  "top_k": 3,
  "candidate_k": 30,
  "force_mode": null
}
```

- `session_id` — opaque string the UI generates and reuses across turns.
  Defaults to a fresh UUID on first turn. Persisted server-side.
- `top_k`, `candidate_k` — retrieval knobs. Defaults are fine. Don't expose
  in the UI for v1.
- `force_mode` — `"rag"` / `"chat"` / `null`. Leave `null`; the backend
  decides.

**Response body:**
```json
{
  "answer": "§ 7 des Pachtvertrags [M-1] verlangt ... (unbelegt).",
  "chunks": [
    {
      "text": "§ 7 Der Pächter hat ...",
      "section": "Hochgeladener Vertrag — pachtvertrag.pdf",
      "law_refs": [],
      "sources": ["upload"],
      "similarity": 1.0,
      "rerank_score": 1.0,
      "cite_id": "M-1",
      "source_kind": "matter"
    },
    {
      "text": "§ 35 BauGB Außenbereichsvorhaben ...",
      "section": "Parent 142053",
      "law_refs": [],
      "sources": ["dense", "bm25"],
      "similarity": 0.84,
      "rerank_score": 0.91,
      "cite_id": "C-3",
      "source_kind": "corpus"
    }
  ],
  "timings": {"embed_s": 0.04, "retrieve_s": 0.12, "rerank_s": 0.21,
              "generate_s": 8.4, "total_s": 8.77},
  "tokens": {"prompt": 1240, "completion": 312},
  "session_id": "abc-123",
  "mode": "rag+contract"
}
```

The **two fields you care about most:**

- `answer` — render this. Replace every `[M-n]` / `[C-n]` substring with a
  clickable chip (§7). Render `(unbelegt)` markers as amber pills.
- `chunks[]` — each chunk has a `cite_id` matching one of the handles in
  the answer. When the lawyer clicks a chip, look the chunk up by
  `cite_id`, open the right-panel preview with `chunk.text` and
  `chunk.section`.

The other fields are diagnostics — `timings` is useful for a debug
indicator if you want one, `tokens` you can ignore.

### 4.2 `POST /upload` — drop a PDF

**Request:** multipart form with two fields:
- `file` — the PDF/DOCX bytes
- `session_id` — optional; same `session_id` you use in `/query`

**Response:**
```json
{
  "session_id": "abc-123",
  "filename": "pachtvertrag.pdf",
  "pages": 42,
  "chunks": 87,
  "message": "Vertrag eingelesen (210,300 Zeichen, 42 Seiten)."
}
```

Show a green "parsed" dot next to the filename in the left panel once this
returns. Show a spinning indicator until then.

### 4.3 `GET /health` — health check

Use on app start to verify the backend is up. If down, show a blocking
error screen.

**Response:**
```json
{
  "ok": true,
  "loaded": true,
  "llm_backend": "remote",
  "llm_model": "qwen3.6-27b",
  "n_sessions": 47
}
```

`loaded: false` means the backend is still warming up (the 5-minute cold
start). Show a "Loading the legal corpus, ~5 minutes…" splash and poll
every 5 seconds until `loaded: true`.

### 4.4 Endpoints you can ignore for v1

- `POST /analyze-contract` — exists for the contract clause-by-clause
  analyzer (the existing v0 feature). v1 demo path is chat-first, so don't
  surface this. The lawyer can ask the chat "analyse this contract clause
  by clause" and get the same content inline.
- Everything else under `/analyze-contract/*`, `/session/*`, etc. —
  legacy.

---

## 5. Components, one by one

Below are the React components you need. I list them in dependency order —
the lowest-level ones first.

### 5.1 `<ConfidentialityBadge />`

Static pill in the header. **No props, no state**. Five tokens, separated
by interpuncts (`·`):

```
On-Premise · BRAO § 43a · DSGVO · EU AI Act · No data leaves
```

Color: muted background, dark text. Reading the strategy doc §2.1 again
— this is the response to the lawyer's #1 implicit question: *"is my
client confidentiality safe?"*

### 5.2 `<LanguageToggle />`

Two-button toggle in the header: `[DE | EN]`. Stores the choice in app
state. Reads it back on every chat turn. (How: the backend doesn't take a
language parameter today; we add one in Day 3 — see §9. For Day 2 it's
visual only.)

### 5.3 `<CitationChip handle text source_kind onClick />`

The most important component in the app. Renders one `[C-n]` or `[M-n]`
inside an assistant message. Props:

- `handle: "C-3" | "M-1"` — the cite ID
- `source_kind: "corpus" | "matter"` — drives color
- `onClick: () => void` — opens the right panel

Visual:
```
[ M-1 ]    blue pill, white text     (matter — user's own doc)
[ C-3 ]    grey pill, dark text      (corpus — legal background)
```

Hover: shows a 1-line tooltip with the first ~80 chars of the chunk text.

### 5.4 `<UnverifiedBadge />`

A small amber pill rendered inline wherever the answer contains
`(unbelegt)`. Replace the literal `(unbelegt)` substring with this
component on render.

Visual:
```
(unbelegt)    amber pill
```

Tooltip: "Diese Aussage konnte nicht durch die hochgeladenen Dokumente
oder den Rechtskorpus belegt werden."

### 5.5 `<AssistantMessage answer chunks />`

The renderer that turns the backend's `answer` string + `chunks[]` array
into clickable markup. The renderer's only job is to:

1. Walk the `answer` string.
2. Replace every `[C-n]` / `[M-n]` substring with a `<CitationChip>`.
3. Replace every `(unbelegt)` substring with an `<UnverifiedBadge>`.
4. Pass the click handlers through.

**Backend hook**: the renderer uses `chunks[]` to resolve a chip's
`onClick` to the right chunk: `chunks.find(c => c.cite_id === handle)`.

A regex that handles this cleanly:
```js
/\[([CM])-(\d+)\]|\(unbelegt\)/g
```

### 5.6 `<CitationPanel chunk onClose />`

The right-side panel that opens when the lawyer clicks a chip. Two
variants based on `chunk.source_kind`:

**Corpus** (`source_kind === "corpus"`):
- Header: chunk.section (e.g. "Parent 142053") or a friendlier label
- Body: full chunk text, formatted as plain-text legal paragraph

**Matter** (`source_kind === "matter"`):
- Header: filename
- Body: PDF preview via `react-pdf`, opened to page 1 in v1 (page
  precision is a v1.1 feature). Below the PDF: the chunk excerpt as text.

The panel is dismissible (X button top-right, ESC key, click outside).

### 5.7 `<DocumentList session_id />`

Left panel. Shows the list of documents the user has uploaded in this
session. Each row:
```
filename.pdf      ● parsed
                  ◐ uploading
                  ◯ failed
```

**Backend hook**: today the backend tracks one document per session
(legacy from the contract-analyzer UI). For v1 the "list" is at most one
row — but render it as a list for forward-compatibility with the v1.1
multi-document Matter workspace.

### 5.8 `<DropZone onFile />`

A drag-and-drop area at the top of the left panel. On file drop, calls
`POST /upload`. Shows progress, then refreshes the document list.

Accept: `.pdf`, `.docx`, `.txt`, `.md`. Max 50 MB.

### 5.9 `<ChatInput onSubmit disabled />`

Multiline text box at the bottom of the center pane. Enter to submit,
Shift+Enter for newline. Disabled while a response is streaming.

### 5.10 `<ChatThread messages />`

The scrolling list of user and assistant messages. Auto-scrolls to bottom
on new message. User messages right-aligned; assistant messages full-width
because of citation chips and side panel.

---

## 6. State management

A single React context (or Zustand store, your call) holds:

```ts
{
  session_id: string,        // uuid generated on first load
  language: "de" | "en",     // language toggle
  documents: Document[],     // {filename, status, pages}
  messages: Message[],       // {role, content, chunks?, mode?}
  activeChunk: Chunk | null, // the chunk in the right panel
  backendHealthy: boolean,
  isStreaming: boolean,
}
```

That's the whole app state. No Redux, no Saga, no nonsense.

`session_id` is generated client-side once on first load and reused
forever. Persist in `localStorage` so a refresh keeps the conversation.

---

## 7. Citation chips — the demo's killer feature

This is the **single most important UI feature**. Get this right and the
demo works. Get this wrong and the backend's Day-1 and Day-4 work is
invisible.

### 7.1 What the backend gives you

The assistant's `answer` string contains tokens like `[M-1]` and `[C-3]`
interleaved with the text:

```
§ 7 des Pachtvertrags [M-1] verlangt vollständigen Rückbau bei
Vertragsende. § 35 Abs. 5 BauGB [C-3] fordert zusätzlich eine
Rückbausicherheit, die im Vertrag jedoch nicht geregelt ist (unbelegt).
```

Each `[M-n]` / `[C-n]` corresponds to one entry in the `chunks[]` array
where `chunks[i].cite_id === "M-n"`.

### 7.2 How to render

Parse the answer string with the regex above. Replace each match with the
right component:

```jsx
function renderAnswer(answer: string, chunks: Chunk[]) {
  const chunkByHandle = new Map(chunks.map(c => [c.cite_id, c]));
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of answer.matchAll(/\[([CM])-(\d+)\]|\(unbelegt\)/g)) {
    if (m.index! > last) parts.push(answer.slice(last, m.index));
    if (m[0] === "(unbelegt)") {
      parts.push(<UnverifiedBadge key={m.index} />);
    } else {
      const handle = `${m[1]}-${m[2]}`;
      const chunk = chunkByHandle.get(handle);
      parts.push(
        <CitationChip
          key={m.index}
          handle={handle}
          source_kind={chunk?.source_kind ?? "corpus"}
          text={chunk?.text ?? ""}
          onClick={() => setActiveChunk(chunk)}
        />,
      );
    }
    last = m.index! + m[0].length;
  }
  if (last < answer.length) parts.push(answer.slice(last));
  return parts;
}
```

### 7.3 What can go wrong

| Edge case | What to do |
|---|---|
| Answer has `[C-99]` but `chunks[]` doesn't carry C-99 | The backend validator already strips these and replaces with `(unbelegt)`. If you still see one, render as plain text — do NOT make it clickable. |
| Answer has no citation tokens at all | Fine. Render the answer as plain text. This is what happens in pure chat mode (greetings). |
| Chunk `text` is very long | Truncate to ~2,000 chars in the side panel; keep the full text in a "show more" expansion. |

### 7.4 Why this specifically wins the demo

The lawyer's #1 quote: *"I cannot defend a sentence I didn't write."*
Citation chips are the answer. Every sentence either has a chip → click →
source paragraph (lawyer can defend it), or has `(unbelegt)` (lawyer
knows not to use it). Nothing falls into the "I trust the AI" gap.

---

## 8. Streaming, loading, error states

### 8.1 Streaming

The v1 backend returns the full answer at once (~8-15 seconds wait). That
feels broken. To fix:

**Option A (Day 2 cheap win):** keep `POST /query` non-streaming, but show
a thinking indicator with three rotating phrases:
- "Searching the legal corpus..."
- "Reranking the most relevant statutes..."
- "Composing answer with citations..."

This is purely cosmetic but turns a "is it frozen?" into "is it working".

**Option B (Day 2 proper fix):** add SSE streaming to `/query` backend
and consume with `EventSource` on the frontend. Each SSE event is a token
delta; append to the current assistant message. Citation chips render
once the full message arrives (the `[C-n]` tokens appear progressively in
the text). This is a half-day backend change — ask backend to add it.

### 8.2 Loading states

- **App boot, backend cold:** full-screen splash "Loading the legal
  corpus, ~5 minutes…" with progress dots. Poll `/health` every 5s until
  `loaded: true`.
- **Upload in progress:** spinning indicator next to filename in left
  panel.
- **Chat turn pending:** disable input box, show thinking indicator in
  chat thread.
- **Citation panel loading:** never — chunks come back with the answer,
  no second request needed.

### 8.3 Empty states

- **No upload yet:** show "Drop a PDF on the left to start, or ask a
  general question."
- **Brand new chat:** show 4 example questions the lawyer can click to
  pre-fill the input. Use real Lamstedt-style questions:
  - *"Ist die Rückbauverpflichtung im hochgeladenen Vertrag ausreichend?"*
  - *"Welche Genehmigung nach BImSchG braucht das Projekt?"*
  - *"Vergleiche § 550 BGB Schriftform mit dem Pachtvertrag."*
  - *"What deadlines exist across these documents?"*

### 8.4 Error states

- **`/query` 5xx:** "Connection to the backend was lost. Try again."
  Keep the input box's text so they don't lose it.
- **`/upload` 4xx (file too big / format unsupported):** show the
  message inline next to the drop zone.
- **`/health` `ok: false`:** "Backend is offline. Contact the operator."
  Block the rest of the UI.

---

## 9. Build order — what to ship each day

Mapped to the strategy doc's [10-day roadmap](LAI_V1_STRATEGY.md#10-10-day-roadmap).
Today is Day 3 already; we're behind.

| Day | Frontend deliverables | Status (2026-05-17) |
|---|---|---|
| Day 2 | `<CitationChip>`, `<UnverifiedBadge>`, `<AssistantMessage>` renderer. Wire `/query` → render chips. Click chip → side panel with chunk text. | ✅ shipped |
| Day 2 | Side panel for `M-n`: render the uploaded PDF inline | ✅ shipped — backend `GET /sessions/{session_id}/document` (`serve_rag.py:2175`) + frontend native `<object>` viewer in `CitationPanel` |
| Day 2 | Thinking indicator on chat turn (Option A above) | ✅ `TypingIndicator` shipped |
| Day 3 | `<LanguageToggle>` wired to a new `target_language` field in `/query` | ✅ shipped end-to-end — backend `QueryReq.target_language` + `_language_directive()`; frontend `streamQuery`/`queryRAG` accept `targetLanguage`; `DashboardChat` reads `useLanguage()` and passes it. EN keeps cited German verbatim per UI_GUIDE.md §7.4. |
| Day 4 | `<ConfidentialityBadge>` (static), `<UnverifiedBadge>` styling polish | ✅ shipped |
| Day 5 | Sidebar `<DocumentList>` with status dots, multi-doc support | ⚠️ not re-audited in this pass — verify before quoting |
| Day 6 | `<DropZone>` drag-and-drop, document status polling, parsed/embedded/indexed indicators | ⚠️ not re-audited in this pass — verify before quoting |
| Day 7 | (auth — deferred to v1.1 per strategy doc §11.2) | — |
| Day 8 | Empty-state example questions, demo-seed Matter pre-loaded ("Windpark Lamstedt") | ⚠️ loader script + fixed session id ready (`LAI/scripts/ops/load_demo_matter.py`, `session_id="lamstedt-demo"`); seed dir empty of PDFs |
| Day 9 | Loading skeletons, error states, polish, demo rehearsal | partial |
| Day 10 | Final polish, demo | — |

**SSE streaming (not in the original day plan):** ✅ shipped
end-to-end. Backend `POST /query/stream` (`serve_rag.py:1607`)
produces `event: token` frames followed by a single `event: complete`
carrying the citation-validated answer + chunks. Frontend
`DashboardChat` opens the stream via `streamQuery()`, renders tokens
into a placeholder bubble in real time, then swaps to the validated
answer + chunks on `onComplete` so `CitationChip` can resolve handles.
`AbortController` is stored in `streamAbortRef` and aborted on
conversation switch / unmount / superseded submit.

### Critical path — minimum viable demo

If you ship only **Day 2** plus the `<ConfidentialityBadge>`, the demo
works. Everything else is polish. Concretely the must-ship list is:

1. `<ConfidentialityBadge>` (1 hour)
2. `<CitationChip>` + `<UnverifiedBadge>` + `<AssistantMessage>` renderer (½ day)
3. `<CitationPanel>` with `react-pdf` for matter and plain text for corpus (½ day)
4. Wire `POST /query` and `POST /upload` (½ day if you have a working app shell)
5. Thinking indicator + decent loading states (¼ day)
6. Backend `GET /document/{session_id}` to serve the uploaded PDF for react-pdf (½ day backend)

**Total: ~2.5 frontend days + ½ backend day = 3 days.** Doable in the
remaining sprint.

---

## 10. Demo-day operations

The lawyer arrives at the office. Before they sit down:

1. **15 min before:** start `serve_rag` if not running. Cold start is ~5
   minutes; the corpus loads into RAM.
2. **5 min before:** verify `/health` returns `ok: true` and
   `loaded: true`.
3. **2 min before:** open the UI in a fresh browser window. Confirm the
   confidentiality badge is visible. Confirm the demo seed Matter
   ("Windpark Lamstedt") is preloaded with 6-8 PDFs.
4. **1 min before:** pre-run the four demo-script questions once
   (Appendix A of the strategy doc). This warms vLLM's prefix cache so
   the live questions feel snappy.
5. **Lawyer sits down.** Walk through the 5-minute pitch. The chat box
   does the work.

**Do not** demo with a fresh installation — the first query of the
session pays 5-10 seconds extra for first-batch JIT, and the lawyer will
notice. Pre-warm.

---

## 11. Out of v1

Explicitly out of scope so you don't accidentally build them:

- ❌ Login / signup screen — single-tenant on-prem, no auth in v1
- ❌ Settings / admin / preferences page
- ❌ Conversation list / chat history sidebar (one conversation per session is fine)
- ❌ DDiQ "Generate report" button — strategy doc §7.5 says hide it
- ❌ DOCX letterhead export — strategy doc §11.2 cuts it
- ❌ Deadline → .ics calendar — v1.1
- ❌ Risk matrix / Ampel render — v1.1
- ❌ Audit log viewer — v1.1
- ❌ Word / Outlook plugin — far future
- ❌ Multi-user concurrent sessions (one lawyer at a time for the demo)

If you find yourself reaching for any of the above, stop and check with
the project lead first.

---

## 12. Quick reference card

Pin this above your monitor:

```
Backend base URL          http://localhost:18000

POST /query    →  { answer, chunks[], session_id, mode }
POST /upload   →  { session_id, filename, pages, message }
GET  /health   →  { ok, loaded, llm_model }

Chunk shape:
  { text, section, cite_id, source_kind, similarity, rerank_score }
  cite_id : "M-1" | "M-2" | "C-1" | "C-2" | ...
  source_kind : "matter" | "corpus"

Citation chip regex
  /\[([CM])-(\d+)\]|\(unbelegt\)/g

Trust signals (top of screen always):
  On-Premise · BRAO § 43a · DSGVO · EU AI Act

Colors:
  [M-1]    blue   (the lawyer's own document)
  [C-3]    grey   (legal corpus)
  (unbelegt) amber (uncited claim)
```

---

## Appendix — backend code references

If you need to see how the backend produces something, here are the exact
line refs:

| Frontend question | Backend code |
|---|---|
| What does `cite_id` look like? | [`src/lai/api/serve_rag.py`](../src/lai/api/serve_rag.py) — search `_corpus_cite_id` / `_matter_cite_id` |
| What does `chunks[]` carry? | `class ChunkOut` in the same file |
| How is `[C-n]` validated post-LLM? | [`src/lai/common/citation/validator.py`](../src/lai/common/citation/validator.py) — `validate_citations` |
| How does the prompt teach citations? | `RAG_SYSTEM` constant in `serve_rag.py` |
| Why does the chat fire RAG even on English? | `session_uses_contract` + `use_rag = True` block in `serve_rag.py` around line 1115 |
| Where to add `target_language` (Day 3)? | `build_rag_messages` in `serve_rag.py` — add a template var to `RAG_SYSTEM` |

---

*End of guide. Update this file as the UI evolves — keep it as the single
source of truth so future frontend engineers don't have to read the rest
of the docs.*
