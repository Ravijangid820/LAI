# LAI-UI Frontend Architecture Analysis

## Executive Summary

The LAI-UI is a **React 19 + Vite + TypeScript** SPA for German wind-energy legal due diligence. It connects to two separate backend services (conversational RAG on port 18000, DDiQ reporting on 18001) via REST APIs. The architecture emphasizes **backend-as-source-of-truth** with minimal client-side state management, using localStorage for session persistence and caching across browser refreshes.

---

## 1. Overall Architecture

### 1.1 Frontend Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Framework | React 19.0 | Strict mode, latest hooks |
| Router | React Router 7.5.3 | Client-side routing |
| Build Tool | Vite 7.1.3 | Fast dev/build, code splitting on route chunks |
| Styling | TailwindCSS 3.4.17 | With animate plugin, dark mode support |
| UI Components | Radix UI + custom shadcn pattern | Unstyled primitives + Tailwind composition |
| Language | TypeScript 5.8.3 | Strict mode, bundler module resolution |
| Maps | React Leaflet 5.0.0 | For ProjectLocationMap (cadastral/parcel display) |
| Markdown | React Markdown 10.1.0 | RAG answer rendering with custom components |
| HTTP Client | Fetch API (no axios/SWR/React Query) | Direct fetch() calls with error handling |
| Validation | Zod 3.24.3 | For shared types (unused currently) |
| Server Framework | Hono 4.7.7 | Minimal Router for Cloudflare Worker (unused) |

### 1.2 Deployment Targets

The project supports **three parallel deployment paths**:

1. **Vercel** (default)
   - `vercel.json` SPA rewrite (all routes → index.html)
   - No serverless functions, pure static
   - Environment: `.env` variables (VITE_BACKEND_URL, VITE_DDIQ_URL)

2. **Cloudflare Workers** (optional)
   - `wrangler.json` config with D1 database + R2 storage bindings
   - `src/worker/index.ts` - minimal Hono entrypoint (mostly unused)
   - Conditional plugin in vite.config.ts

3. **Local Development**
   - `npm run dev` → http://localhost:5173 with hot reload
   - Targets http://192.168.178.82:8000 (configurable)

### 1.3 Project Structure

```
src/
├── react-app/                      # Main React application
│   ├── pages/                      # 9 route pages (Dashboard, Chat, Documents, Projects, Risk, Settings, Landing, Login, Signup)
│   ├── components/
│   │   ├── chat/                   # ChatMessage, ChatInput, ConversationList, MarkdownRenderer, TypingIndicator
│   │   ├── project/                # ProjectDetailView, ProjectChatView, ProjectConversationList, ProjectSidebar, ProjectFileGrid
│   │   ├── ui/                     # 30+ Radix-based UI primitives (button, card, dialog, tabs, etc.)
│   │   └── DashboardLayout.tsx     # Sidebar + outlet router (collapsible sidebar)
│   ├── contexts/
│   │   ├── AuthContext.tsx         # User login/signup/logout (JWT-based, demo auth)
│   │   └── ThemeContext.tsx        # Dark/light theme toggle (localStorage)
│   ├── hooks/
│   │   └── useSpeechRecognition.ts # Browser Web Speech API wrapper
│   ├── lib/
│   │   ├── ragApi.ts               # Client for serve_rag backend (/query, /upload, /analyze-contract, /sessions)
│   │   ├── ddiqApi.ts              # Client for DDiQ backend (/ddiq/documents, /ddiq/report/*)
│   │   ├── ddiqDemoData.ts         # Mock report structures for UI testing
│   │   └── utils.ts                # cn() class merge utility
│   ├── utils/
│   │   ├── jwt.ts                  # Token generation/verification (client-side, demo mode)
│   │   └── uuid.ts                 # randomId() - Web Crypto fallback for HTTP contexts
│   ├── App.tsx                     # Route definitions
│   ├── main.tsx                    # React root render
│   └── index.css                   # Global Tailwind + CSS variables (light/dark themes)
├── shared/
│   └── types.ts                    # Shared Zod schemas (currently empty)
└── worker/
    └── index.ts                    # Cloudflare Worker entrypoint (minimal Hono app)
```

---

## 2. Component Structure & Organization

### 2.1 Page Hierarchy

```
App.tsx
├── Landing                     # Public landing page
├── Login                       # Email/password login (demo: any email/pass accepted)
├── Signup                      # Email/password signup
└── DashboardLayout (Protected)  # Main authenticated app
    ├── Dashboard               # Overview + stats
    ├── DashboardChat           # Core conversational interface + document upload
    ├── DashboardDocuments      # Upload hub for DDiQ analysis
    ├── DashboardProjects       # Project workspaces (mock data currently)
    │   └── ProjectDetailView   # Project-level chat, file management, instructions
    ├── DashboardRisk           # DDiQ Reports browser + Risk Overview
    └── DashboardSettings       # User preferences (stub)
```

### 2.2 Chat Component Hierarchy

```
DashboardChat
├── ConversationList            # Sidebar: grouped by date, searchable, actions menu
├── ChatMessage (array)         # Single message bubble with attachments, copy, feedback
├── ChatInput                   # Text + file attachment + voice input + send button
│   └── useSpeechRecognition    # Browser Web Speech API
├── TypingIndicator            # "LAI is thinking..." with animated dots
└── ReportDownloadPanel        # DDiQ report generation/polling UI
```

### 2.3 Project Component Hierarchy

```
ProjectDetailView
├── ProjectSidebar             # Instructions editor, file grid, metadata
├── ProjectConversationList    # List of conversations in this project
└── ProjectChatView            # Chat interface scoped to project
    └── (reuses chat components)
```

### 2.4 UI Component Library

30+ Radix UI-based primitives with Tailwind styling:
- Form controls: Button, Input, Textarea, Select, Checkbox, Radio, Toggle
- Feedback: Badge, Alert, Progress, Skeleton, Tooltip
- Layout: Card, Separator, ScrollArea, Accordion, Tabs
- Navigation: Dropdown Menu, Popover
- Dialog: Dialog, AlertDialog

All exported from `components/ui/` and follow `shadcn` pattern (component-specific classes, no external deps).

---

## 3. State Management Approach

### 3.1 Architecture: Minimal Client State + Backend as Source of Truth

**Philosophy**: Avoid Redux/Context bloat. Use React hooks + localStorage for session persistence, backend provides authoritative state.

#### State Layers

```
┌─────────────────────────────────────────────┐
│ Global (Contexts)                           │
│ ├─ AuthContext (user, isAuthenticated)      │
│ └─ ThemeContext (dark|light)                │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│ Page-Level State (useState)                 │
│ ├─ DashboardChat: messages, isTyping        │
│ ├─ DashboardDocuments: documents, filters   │
│ ├─ ReportDownloadPanel: status polling      │
│ └─ DashboardProjects: projects, selected    │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│ Component-Level State                       │
│ ├─ ChatInput: message, attachments, isDrag  │
│ └─ ReportForm: selectedDocs, preset         │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│ Persistent Storage (localStorage)           │
│ ├─ lai-auth-token (JWT, 24h expiry)         │
│ ├─ lai-theme (dark|light)                   │
│ ├─ lai.activeConversation (current conv ID) │
│ ├─ lai.session.{convId} (session_id)        │
│ └─ lai.ddiq.activeReport (full report JSON) │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│ Server (Backend)                            │
│ ├─ serve_rag:18000 (/sessions, /query)     │
│ └─ lai-backend:18001 (/ddiq/reports)       │
└─────────────────────────────────────────────┘
```

### 3.2 Context Usage

#### AuthContext
- **State**: `user` (id, email, fullName), `isAuthenticated`
- **Methods**: `login()`, `signup()`, `logout()`
- **Persistence**: JWT stored in localStorage, verified on mount
- **Current Limitation**: Demo auth (accepts any email/password)

```tsx
const { user, isAuthenticated, login, logout } = useAuth();
```

#### ThemeContext
- **State**: `theme` ("dark" | "light")
- **Methods**: `toggleTheme()`, `setTheme()`
- **Persistence**: localStorage `lai-theme`
- **Behavior**: Adds/removes class on `<html>` element for Tailwind dark: variant

```tsx
const { theme, toggleTheme } = useTheme();
```

### 3.3 Page-Level State Management

**DashboardLayout** (parent) manages:
```tsx
const [conversations, setConversations] = useState<Conversation[]>([]);
const [activeConversationId, setActiveConversationId] = useState<string | null>();
```
- Passed to child via `useOutletContext()`
- `refreshConversations()` fetches `/sessions` on mount + after user actions
- Active ID mirrored to localStorage (survives refresh)

**DashboardChat** (child) manages:
```tsx
const [messages, setMessages] = useState<ChatMessageData[]>([]);
const [sessionId, setSessionId] = useState<string | null>(null);
const [isTyping, setIsTyping] = useState(false);
const skipNextRehydrate = useRef(false); // prevent wipe on activeConv change
```
- Rehydrates messages from `/sessions/{activeConversationId}` on conv switch
- skipNextRehydrate prevents clearing thread when app sets activeConversationId post-upload

**ReportDownloadPanel** manages:
```tsx
const [activeReport, setActiveReport] = useState<PersistedReport | null>();
const pollIntervalRef = useRef<number | null>(null);
```
- Polls status every ~2s until done
- localStorage `lai.ddiq.activeReport` survives refresh

---

## 4. API Integration & Data Fetching

### 4.1 Two Separate Backends

#### Backend 1: serve_rag (port 18000)
**Purpose**: Conversational RAG + contract analysis

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/query` | POST | Submit question to RAG system; returns answer + chunks + timings |
| `/upload` | POST (multipart) | Upload document (PDF/DOCX/TXT/CSV/MD); returns chunks count |
| `/analyze-contract` | POST | Extract clauses from uploaded contract; returns issues + missing clauses |
| `/sessions` | GET | List all session summaries (limit=50) |
| `/sessions/{id}` | GET | Fetch full session detail + all messages |
| `/sessions/{id}` | DELETE | Hard-delete session |
| `/sessions/{id}/rename` | POST | Set user-facing title override |
| `/health` | GET | Health check |

**Request Example**:
```bash
POST /query
Content-Type: application/json

{
  "question": "What are the risks in this lease?",
  "session_id": "uuid-from-upload"
}
```

**Response**:
```json
{
  "answer": "...",
  "chunks": [...],
  "mode": "rag" | "chat" | "contract" | "rag+contract",
  "tokens": { "prompt": 450, "completion": 200 },
  "timings": { "embed_s": 0.05, "retrieve_s": 0.1, "rerank_s": 0.15, "generate_s": 2.0, "total_s": 2.3 }
}
```

#### Backend 2: lai-backend/ddiq (port 18001)
**Purpose**: Multi-document due-diligence report generation (cadastral, permits, risks, deadlines)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/ddiq/documents` | GET | List uploaded documents (with status) |
| `/ddiq/documents/upload` | POST (multipart) | Upload document for DDiQ analysis |
| `/ddiq/report/generate/async` | POST | Kick off async report; returns report_id + status |
| `/ddiq/report/{id}/status` | GET | Poll report status (cheap, status fields only) |
| `/ddiq/report/{id}` | GET | Fetch full completed report |
| `/ddiq/reports` | GET | List all past reports (limit=50) |
| `/ddiq/report/{id}` | DELETE | Hard-delete report |

**Async Pattern** (for 30-60 min runs):
```bash
POST /ddiq/report/generate/async
{
  "document_ids": ["doc1", "doc2"],
  "preset": "FULL",
  "project_name": "Windpark XYZ"
}
```

**Response** (immediate):
```json
{
  "report_id": "rpt_123",
  "status": "queued" | "running" | "done" | "failed",
  "cached": false
}
```

**Later, poll status**:
```bash
GET /ddiq/report/rpt_123/status
```

**When done**:
```bash
GET /ddiq/report/rpt_123
```

### 4.2 API Client Architecture

#### ragApi.ts
- **Function naming**: `queryRAG()`, `uploadDocument()`, `analyzeContract()`, `getSession()`, `listSessions()`
- **Error handling**: Custom error objects from response JSON (fallback to status code)
- **Result type**: `SessionFetchResult = { ok: true; session } | { ok: false; reason }`
- **Distinction**: Differentiates "session truly gone (404)" from "backend unreachable (network error)"

```tsx
// Usage
const result = await getSession(convId);
if (result.ok) {
  setMessages(result.session.messages);
} else if (result.reason === "not-found") {
  // Clear stale convId from localStorage
}
```

#### ddiqApi.ts
- **Function naming**: `fetchDocuments()`, `uploadDDiQDocument()`, `generateReportAsync()`, `fetchReportStatus()`, `fetchReport()`, `deleteReport()`, `listReports()`
- **Async polling**: No built-in polling loop; UI manages interval
- **Fingerprint dedup**: Backend deduplicates on sorted doc_ids + preset → same report_id + cached=true

### 4.3 Error Handling Patterns

**No global error boundary currently**. Each API call wrapped in try-catch:

```tsx
const loadDocuments = async () => {
  try {
    const res = await fetchDocuments();
    setDocuments(res.documents);
  } catch (err) {
    console.error("Failed to load documents:", err);
    // UI shows empty state or retry button
  }
};
```

**Graceful degradation**: Failed API calls return empty arrays/null instead of throwing.

```tsx
export async function listSessions(): Promise<SessionSummary[]> {
  try { ... }
  catch { return []; } // Empty list on network error
}
```

### 4.4 Configuration

**Environment Variables** (in `.env`):
```
VITE_BACKEND_URL=http://192.168.178.82:8000    # serve_rag endpoint
VITE_DDIQ_URL=http://192.168.178.82:18001      # lai-backend endpoint (auto-derived if not set)
```

**Why dual URLs?**: The two services run on different ports/might scale independently.

---

## 5. Performance Optimizations

### 5.1 Build & Code Splitting

#### Vite Configuration
```tsx
build: {
  chunkSizeWarningLimit: 5000,  // Warn if chunk > 5KB
  outDir: "dist",               // For Vercel SPA rewrite
}
```

**Code Splitting**:
- **No explicit React.lazy()** in codebase
- Vite's default: entry point + vendor chunk + route chunks
- Routes loaded on demand via React Router

**Bundle Stats** (estimated):
- React + React Router: ~120 KB
- TailwindCSS (JIT): ~80 KB
- Radix UI + markdown: ~60 KB
- App code: ~50 KB
- **Total**: ~310 KB (minified, pre-gzip)

### 5.2 Caching Strategies

#### localStorage-Based Session Cache
```tsx
const SESSION_KEY_PREFIX = "lai.session.";

// After successful /upload or /query, store sessionId
localStorage.setItem(`lai.session.${convId}`, sessionId);

// On mount, skip server rehydration if cached
const cachedSessionId = localStorage.getItem(`lai.session.${convId}`);
```

**Why**: Avoids re-fetching entire message history on every mount. Only clears on confirmed 404.

#### Report Cache
```tsx
const ACTIVE_REPORT_KEY = "lai.ddiq.activeReport";

interface PersistedReport {
  report_id: string;
  status: ReportStatus;
  report?: DDiQReportData;  // Full payload when done
  ts: number;
}

// Save after fetch
localStorage.setItem(ACTIVE_REPORT_KEY, JSON.stringify(report));

// Load on mount
const cached = loadPersistedReport();
if (cached?.status === "done") {
  // Use cached report without refetch
}
```

**Purpose**: Survive 30-60 min report generation + browser refresh. Combined with backend fingerprint dedup = no duplicate GPU burns.

#### Other Caches
- **Theme**: `lai-theme` → parsed on mount, set via context
- **Auth**: `lai-auth-token` → JWT verified on mount, cleared on logout
- **Active Conversation**: `lai.activeConversation` → restored on app load

### 5.3 Rendering Optimizations

#### MarkdownRenderer
- Custom component per HTML element (h1, h2, ul, li, code, etc.)
- Tailwind classes applied directly (no CSS-in-JS)
- Inline rendering (no memoization needed)

#### ChatMessage
- `formatFileSize()` utility (no re-computation)
- Copy feedback state auto-clears after 2s
- Attachments render as chips (flex wrap, max-w)

#### ConversationList
- Grouped by date client-side (reduce)
- **Opportunity**: Could use `useMemo()` on filteredConversations (not currently)
- Search on title + preview (simple includes)

#### TypingIndicator
- Pure CSS animation (`animate-bounce` with staggered delays)
- No JS animation loops

### 5.4 Limitations & Bottlenecks

| Issue | Impact | Potential Fix |
|-------|--------|---------------|
| No WebSocket/SSE | User waits for full response before seeing streaming | Implement streaming `/query` endpoint |
| Fixed 2s polling | Wastes requests at end, overkill early | Exponential backoff or adaptive polling |
| No multi-file chunked upload | Large docs might timeout | Implement resumable upload (TUS protocol) |
| No retry logic | Single network blip = failure | Add exponential backoff to fetch wrapper |
| ConversationList limit=50 | Scales poorly to thousands | Implement pagination + virtual scroll |
| Manual scroll-to-bottom | setTimeout(0) hack fragile | Use Intersection Observer for scroll anchor |
| SessionId localStorage fallback | Doesn't invalidate on server restart | Add version/heartbeat check |

---

## 6. UI/UX Patterns for Report Generation & Answering

### 6.1 Chat Answer Display Flow

```
User Input
    ↓
[ChatInput captures text + attachments]
    ↓
[If attachments] → /upload for each file
    ├─ Show "Uploading..." in TypingIndicator
    ├─ On success, append 📎 + confirmation message to chat
    └─ Sync sidebar, update activeConversationId
    ↓
[If text] → /query or /analyze-contract
    ├─ Show "LAI is thinking..."
    ├─ Backend streams or returns full response
    ├─ Parse response (answer + chunks + timings)
    └─ Append assistant message to chat
    ↓
[ChatMessage rendered]
    ├─ Markdown (h1-3, lists, bold, code, blockquote)
    ├─ Chunks with sources (vector/bm25)
    ├─ Timings breakdown
    ├─ Mode indicator (rag/contract/etc)
    ├─ Copy button + feedback buttons (👍👎)
    └─ Regenerate button (optional)
    ↓
[Auto-scroll to bottom on new message]
```

#### Answer Rendering Details

**Markdown Styling**:
- h1: text-xl font-bold, mb-3 mt-4
- p: text-sm leading-relaxed, mb-2
- code: bg-muted px-1.5 py-0.5 rounded, font-mono, text-xs
- blockquote: border-l-2 border-primary, pl-3, italic, text-muted-foreground
- ul/ol: list-disc/-decimal, list-inside, space-y-1

**Chunk Display** (from ragApi response):
```tsx
{
  text: "...",
  section: "Land Lease Agreement §4",
  law_refs: ["BNatSchG", "BImSchG"],
  sources: ["vector"] | ["vector", "bm25"],
  similarity: 0.87,
  rerank_score: 0.92
}
```

### 6.2 DDiQ Report Generation Flow

```
[User selects documents + preset]
    ↓
[generateReportAsync()] → immediate return
    ├─ report_id + status: queued
    ├─ cached: true/false (fingerprint dedup)
    └─ Save to localStorage (persist across refresh)
    ↓
[UI starts polling /ddiq/report/{id}/status every 2s]
    ├─ status: queued → running → done | failed
    ├─ step: "Analyzing permit...", "Checking risks...", etc
    ├─ percent: 0 → 100
    ├─ started_at, finished_at timestamps
    └─ Show Progress bar + step text + elapsed time
    ↓
[On status=done]
    ├─ /ddiq/report/{id} fetches full payload
    └─ Cache in localStorage
    ↓
[ReportDownloadPanel renders findings]
    ├─ Ausgabeblatt (cadastral summary table)
    ├─ Findings (red/yellow/green items by domain)
    │   ├─ Ampel (traffic light) severity badges
    │   ├─ Legal basis (e.g., "§35 BauGB")
    │   ├─ Evidence chips (📎 source document excerpt)
    │   └─ Quantification (MW affected, €impact, days to deadline)
    ├─ Timeline (extracted deadlines with urgency: urgent/soon/expired)
    ├─ Rückbaubürgschaft (decommissioning bond status)
    └─ WEA Status grid (individual turbines + risk indicator per parcel)
    ↓
[Export buttons] (PDF/JSON/etc — UI ready, backend not)
```

#### Report Component Library

**Ampel Status Indicator**:
- Green (✓ Secured): emerald-500
- Yellow (⚠ Partial): amber-500
- Red (✗ Open): rose-500

**Finding Item Layout**:
```
[Ampel Dot] [Domain] [Kind badge] [Legal basis badge]
[Finding text]
[→ Recommended action (italic)]
[Quant badges: MW, €impact, days]
[Evidence chips: 📎 Doc Name § Excerpt]
```

**Timeline Panel**:
```
[Date] [Kind: Deadline/Milestone/Requirement] [Legal basis] [Urgency: expired/urgent/soon]
[Description]
[Days from now (relative)]
[Evidence chips]
```

---

## 7. Worker Configuration

### 7.1 Purpose & Setup

**Cloudflare Workers** = optional edge computing layer. Currently **minimally used**.

- **wrangler.json**: Declares D1 + R2 bindings (database + storage)
- **src/worker/index.ts**: Minimal Hono routing (can stay empty)
- **Conditional Plugin**: vite.config.ts skips Cloudflare plugin on Vercel

**Use Cases** (not yet implemented):
- Static asset serving (cache control headers)
- Request transformation (auth headers, cors)
- Rate limiting middleware
- Geolocation-based routing

### 7.2 Configuration Details

```json
{
  "name": "019c7b8f-aaea-70d8-8685-8a21f0e5d844",
  "main": "./src/worker/index.ts",
  "compatibility_date": "2025-06-17",
  "compatibility_flags": ["nodejs_compat"],  // Node.js APIs available
  "assets": {
    "not_found_handling": "single-page-application"  // SPA 404 → index.html
  },
  "d1_databases": [...],  // D1 database binding
  "r2_buckets": [...]     // R2 storage binding
}
```

---

## 8. Build Configuration

### 8.1 TypeScript Configuration

**Three separate tsconfig files** (Vite multi-tsconfig pattern):

#### tsconfig.json (root)
```json
{
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.worker.json" }
  ]
}
```

#### tsconfig.app.json (React app)
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true,
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src/react-app"]
}
```

**Strict Checks Enabled**:
- `noUnusedLocals`: Warn on unused variables
- `noUnusedParameters`: Warn on unused function params
- `noFallthroughCasesInSwitch`: Require break/return in switch
- `noUncheckedSideEffectImports`: Warn on non-type imports from modules with side effects

#### tsconfig.app.json (Vite/Node)
- Target: ES2020, CommonJS module for build tools

#### tsconfig.worker.json (Cloudflare Worker)
- Includes worker-configuration.d.ts (minimal type stubs)

### 8.2 Vite Configuration

```tsx
export default defineConfig({
  plugins: [
    react(),
    // Cloudflare plugin only if NOT on Vercel
    ...(process.env.NODE_ENV === "production" && !process.env.VERCEL
      ? [cloudflare()]
      : []),
  ],
  build: {
    chunkSizeWarningLimit: 5000,
    outDir: "dist",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

**Key Points**:
- Path alias `@/*` for cleaner imports
- React plugin handles JSX transformation
- Cloudflare plugin skipped on Vercel (incompatible output structure)
- outDir hardcoded (Vercel SPA rewrite expects dist/)

### 8.3 Build Command

```bash
npm run build
# → tsc -b && vite build

# tsc -b: Builds all three tsconfigs, incremental
# vite build: Minifies, chunks, outputs to dist/
```

---

## 9. Dependency Management

### 9.1 Production Dependencies (13 total)

| Package | Version | Purpose |
|---------|---------|---------|
| react | 19.0.0 | Framework |
| react-dom | 19.0.0 | DOM rendering |
| react-router | 7.5.3 | Client routing |
| react-markdown | 10.1.0 | Answer rendering |
| react-leaflet | 5.0.0 | Map widget |
| leaflet | 1.9.4 | Underlying map library |
| lucide-react | 0.510.0 | Icon library (custom icons also used) |
| radix-ui | 1.4.3 | UI primitives |
| tailwindcss | (implicit) | Styling (only in CSS, not JS) |
| class-variance-authority | 0.7.1 | Component prop-based class generation |
| clsx | 2.1.1 | Conditional class strings |
| tailwind-merge | 3.4.0 | Merge Tailwind conflicting classes |
| zod | 3.24.3 | Schema validation (minimal usage) |
| hono | 4.7.7 | Worker framework (minimal) |
| @hono/zod-validator | 0.5.0 | Hono + Zod integration |

### 9.2 Development Dependencies (17 total)

| Package | Version | Purpose |
|---------|---------|---------|
| vite | 7.1.3 | Build tool |
| typescript | 5.8.3 | Language |
| @vitejs/plugin-react | 5.1.4 | React JSX transform |
| @cloudflare/vite-plugin | 1.12.0 | Cloudflare Workers support |
| tailwindcss | 3.4.17 | Styling framework |
| postcss | 8.5.3 | Tailwind preprocessor |
| autoprefixer | 10.4.21 | CSS vendor prefixes |
| tailwindcss-animate | 1.0.7 | Animation utilities |
| eslint | 9.25.1 | Linting |
| @eslint/js | 9.25.1 | ESLint config |
| typescript-eslint | 8.31.0 | TS linting rules |
| eslint-plugin-react-hooks | 5.2.0 | Hooks lint rules |
| eslint-plugin-react-refresh | 0.4.19 | Fast refresh lint rules |
| wrangler | 4.33.0 | Cloudflare CLI |
| knip | 5.51.0 | Unused code detector |
| globals | 15.15.0 | Global variable definitions |
| cross-env | 10.1.0 | Cross-platform env vars |

### 9.3 Key Decisions

**Minimal & Pragmatic**:
- No Redux/Zustand → useState + contexts
- No React Query → direct fetch() + localStorage
- No UI framework deps → Radix primitives + Tailwind
- Single icon library → Radix icons + custom SVG icons in components/icons.tsx
- TypeScript strict mode → catch errors early

---

## 10. Caching & Optimization Strategies

### 10.1 Client-Side Caching Summary

| Cache Key | Type | TTL | Purpose |
|-----------|------|-----|---------|
| `lai-auth-token` | JWT | 24h | Session identity |
| `lai-theme` | string | ∞ | Dark/light preference |
| `lai.activeConversation` | UUID | ∞ | Last active chat |
| `lai.session.{convId}` | UUID | ∞ | Session ID (avoid rehydrate) |
| `lai.ddiq.activeReport` | JSON | ∞ | Full report payload |

### 10.2 Backend Caching (Deduplication)

**Fingerprint Dedup for Reports**:
- Input: sorted doc_ids + preset + project_name
- Backend hashes fingerprint, checks if report exists or in-flight
- Returns same report_id + `cached: true` → no duplicate GPU compute

### 10.3 Network Optimization

**No Built-In Optimizations Currently**:
- No gzip compression (handled by server)
- No request batching
- No response streaming (full payloads)
- No HTTP/2 push

**Opportunities**:
- Enable streaming responses for /query (chunks as they arrive)
- Implement request coalescing (multiple /query calls → single request)
- Use GraphQL subscriptions for real-time updates

---

## 11. Real-Time Update Mechanisms

### 11.1 Current Approach: Polling

**Report Status Polling**:
```tsx
const pollIntervalRef = useRef<number>();

useEffect(() => {
  pollIntervalRef.current = setInterval(async () => {
    const status = await fetchReportStatus(reportId);
    setStatus(status);
    if (status.status === "done") clearInterval(pollIntervalRef.current);
  }, 2000);
  return () => clearInterval(pollIntervalRef.current);
}, [reportId]);
```

**Limitations**:
- Fixed 2s interval (no backoff)
- Overkill early (when backend is queued/loading model)
- Wastes requests near end (when percent=99)

### 11.2 Missing: WebSocket/SSE

**NOT currently implemented**:
- No Server-Sent Events (SSE) for answer streaming
- No WebSocket for real-time collaboration
- No live updates to sidebar conversations

**Potential Enhancement**:
```tsx
// Streaming /query endpoint
async function* streamQuery(question: string) {
  const res = await fetch(`${BACKEND_URL}/query/stream`, { 
    method: "POST",
    body: JSON.stringify({ question })
  });
  const reader = res.body.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    yield JSON.parse(new TextDecoder().decode(value));
  }
}

// Usage in chat
for await (const chunk of streamQuery(message)) {
  setMessages(prev => [...prev.slice(0,-1), { ...chunk }]);
}
```

---

## 12. Full User Journey

### 12.1 New User Onboarding

```
Landing Page
    ↓
[Signup] → AuthContext.signup(fullName, email, pwd)
    ├─ Generate JWT, store in localStorage
    ├─ Redirect to /dashboard
    └─ AuthContext validates token on mount
    ↓
DashboardLayout
    ├─ Fetch /sessions (empty initially)
    ├─ Show "New Chat" button
    └─ Pass activeConversationId=null to children
```

### 12.2 Chat-Based Analysis (RAG)

```
1. User lands in DashboardChat
   ├─ activeConversationId=null (new chat)
   ├─ No messages displayed
   └─ ChatInput ready for text or attachment

2. User uploads contract.pdf
   ├─ ChatInput.onChange → setAttachments
   ├─ User types question OR clicks "Analyze contract"
   └─ handleSendMessage → uploadDocument()

3. Upload handler
   ├─ /upload contract.pdf → sessionId (new or reuse)
   ├─ Sync sidebar: setActiveConversationId(sessionId)
   ├─ Add 📎 + confirmation messages to chat
   └─ refreshConversations() → fetch /sessions

4. User asks question
   ├─ /query question → answer + chunks
   ├─ Parse response, append ChatMessage
   ├─ Auto-scroll to bottom
   └─ Show timings + chunks + mode indicator

5. On refresh
   ├─ localStorage restores activeConversationId
   ├─ DashboardChat rehydrates /sessions/{id}
   └─ Messages replay, chat continues where left off
```

### 12.3 Report-Based Analysis (DDiQ)

```
1. User navigates to Risk Assessment → DDiQ Reports tab

2. User uploads documents
   ├─ Documents page: drag-drop or file picker
   ├─ /ddiq/documents/upload for each file
   └─ Sidebar shows "Processed: 3 documents"

3. User generates report
   ├─ Select docs + preset (FULL/PERMIT/LAND/etc)
   ├─ generateReportAsync() → report_id + status:queued
   ├─ localStorage saves progress
   └─ Start polling /status every 2s

4. Report processes
   ├─ backend: queued → running → done
   ├─ UI: percent 0 → 100, step text updates
   └─ User can close browser, report continues

5. On completion
   ├─ Fetch full report payload
   ├─ ReportDownloadPanel renders findings
   ├─ User browses Ausgabeblatt, findings, timeline
   └─ Optional: export to PDF/JSON (UI ready)

6. Risk Overview tab
   ├─ Aggregates all findings from completed reports
   ├─ Grouped by domain (Land/Permits/Economics/Regulatory)
   ├─ Red/yellow items elevated for action
   └─ Links back to originating report
```

### 12.4 Project Workspace (Mock Currently)

```
1. User navigates to Projects

2. Creates new project
   ├─ Modal: project name + description
   ├─ Local state: INITIAL_PROJECTS + new entry
   └─ No backend persistence yet

3. Opens project detail
   ├─ Left sidebar: instructions editor, file grid
   ├─ Center: project-specific chat
   ├─ Chat can reference files in project
   └─ Conversations scoped to this project

4. Uploads files to project
   ├─ ProjectSidebar file picker
   ├─ Files display in grid (name, size, type, line count)
   ├─ Can delete files
   └─ Chat input can attach files from project

5. On refresh
   ├─ Local state cleared (no backend)
   ├─ Projects + conversations lost
   └─ Demo only; production needs persistence
```

---

## 13. Identified Bottlenecks & Recommendations

### 13.1 Critical Issues

| Issue | Severity | Impact | Recommendation |
|-------|----------|--------|-----------------|
| No streaming responses | High | User waits for full LLM response before feedback | Implement streaming `/query` endpoint + SSE client |
| No retry logic | High | Single network blip = failure, bad UX | Add exponential backoff to fetch() wrapper |
| Fixed poll interval | Medium | Wastes requests, slow initial feedback | Implement adaptive/exponential backoff (2s → 10s) |
| No error boundary | Medium | Single component crash = blank screen | Add ErrorBoundary wrapper at page level |
| localStorage key collisions | Low | Multiple browser tabs interfere | Add namespace/version prefix to all keys |

### 13.2 Scaling Issues

| Issue | Scenario | Recommendation |
|-------|----------|-----------------|
| ConversationList limit=50 | 500+ chats | Implement pagination + virtual scroll |
| No multi-file upload | 10+ files at once | Chunked/resumable upload (TUS protocol) |
| Full report in localStorage | Huge reports (100+ MB) | Stream findings, lazy-load sections |
| Manual scroll-to-bottom | Long chats (200+ messages) | Intersection Observer for auto-scroll |

### 13.3 Architecture Improvements

| Improvement | Effort | Benefit |
|-------------|--------|---------|
| Add React Query for API caching | Medium | Automatic stale-while-revalidate, deduplication |
| Implement RTK (Redux Toolkit) | High | Predictable state, time-travel debugging (probably overkill) |
| WebSocket for live updates | High | Real-time sidebar refresh, collaborative editing |
| Service Worker + offline mode | High | Work offline, sync when reconnected |
| Semantic versioning for API | Medium | Breaking change detection, better error messages |

---

## 14. Summary: Architecture Strengths & Weaknesses

### Strengths ✅

1. **Minimal State Complexity**: localStorage + contexts only, no Redux
2. **Backend-Driven**: UI fetches on mount, backend is source of truth
3. **Session Persistence**: Survive 30-60 min reports across browser refresh
4. **Type Safety**: TypeScript strict mode, Zod for validation
5. **Component Library**: Reusable Radix-based UI (30+ primitives)
6. **Responsive Design**: Tailwind mobile-first, dark mode built-in
7. **Two Deployment Options**: Vercel (default) + Cloudflare Workers (optional)
8. **Lean Dependencies**: No Redux, React Query, or heavy frameworks

### Weaknesses ❌

1. **No Streaming**: Users wait for full response before seeing feedback
2. **No Retry Logic**: Single network error = failure (bad UX on flaky networks)
3. **No WebSocket**: Polling only, not real-time
4. **Missing Error Boundary**: Component crashes could blank entire page
5. **No Multi-Tab Sync**: localStorage collisions possible
6. **Projects Mock Only**: No backend persistence for project workspaces
7. **Limited Filtering**: ConversationList shows 50, no pagination
8. **JWT Demo Mode**: Auth accepts any email/password (security placeholder)

### Potential Bottlenecks 🔴

1. **Answer Generation** - LLM latency (backend issue, UI can stream)
2. **Report Generation** - 30-60 min GPU processing (asynchronous ✓, but polling slow)
3. **Document Upload** - No chunking for 100+ MB files
4. **Sidebar Refresh** - Full re-fetch on every action (no incremental updates)
5. **Chat Scroll** - Manual setTimeout workaround for long threads

---

## 15. Technology Decisions Rationale

### Why Not Redux?
- App state is mostly read-only from backend
- useState + contexts sufficient for Auth + Theme
- Reduces bundle size, complexity

### Why Not React Query?
- Direct fetch() calls with try-catch
- localStorage handles cache persistence
- DDiQ fingerprint dedup on backend
- Good enough for current scale

### Why localStorage Over sessionStorage?
- Survive browser refresh during 30-60 min report
- Cross-tab awareness (activeConversation synced)
- Simpler than IndexedDB for these data sizes

### Why Separate Backends?
- serve_rag handles conversational RAG (port 18000)
- lai-backend handles resource-heavy DDiQ reports (port 18001)
- Independent scaling + team ownership
- Frontend agnostic to backend architecture

### Why Radix + Tailwind (not Material-UI/Chakra)?
- Radix = unstyled, maximum control
- Tailwind = utility-first, fast iteration
- Smaller bundle than opinionated frameworks
- Custom design tokens in CSS variables

---

## 16. Deployment & DevOps Notes

### Vercel (Primary)
```bash
npm run build  # tsc -b && vite build
# → dist/ folder ready for Vercel
# → vercel.json SPA rewrite handles routing
```

### Cloudflare Workers (Optional)
```bash
npm run build
wrangler deploy  # Uploads dist/ + worker binding
```

### Local Development
```bash
npm install
npm run dev  # Vite dev server on localhost:5173
# .env.local: VITE_BACKEND_URL=http://localhost:18000
```

### Environment Variables
```
VITE_BACKEND_URL       # serve_rag endpoint (default: http://192.168.178.82:8000)
VITE_DDIQ_URL         # lai-backend endpoint (auto-derived or override)
VITE_JWT_SECRET       # Unused in demo (auth is client-side only)
```

---

## Conclusion

The **LAI-UI** is a well-structured, type-safe React SPA designed for **German wind-energy legal due diligence**. It excels at session persistence, responsive design, and minimal state complexity. Key areas for scaling:

1. **Add streaming responses** for real-time answer feedback
2. **Implement retry logic** for network resilience
3. **Migrate to adaptive polling** for reports (exponential backoff)
4. **Add error boundaries** for robustness
5. **Enable backend persistence** for project workspaces
6. **Support pagination** for large conversation lists

The two-backend architecture (conversational RAG + resource-heavy DDiQ) is well-motivated and scales independently. The decision to use localStorage + contexts instead of Redux/React Query is pragmatic for the current data patterns.
