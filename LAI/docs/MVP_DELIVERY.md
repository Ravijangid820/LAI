# LAI MVP — Delivery Summary

**Subject:** LAI MVP — delivery summary

The MVP for the LAI contract-review platform is now deployed and working end-to-end. Below is a summary of what's been built and the capabilities it now offers.

## Core delivery — what works today

- **Conversational legal assistant** for German wind-energy contracts, with retrieval-augmented answers grounded in our internal corpus (BImSchG / BauGB / EEG / contract chunks).
- **Document upload and analysis** — users drop a PDF (signed contracts supported) and the system extracts text via OCR, segments the document into clauses, and produces a clause-by-clause review.
- **Deep contract analysis** powered by a 27B-parameter reasoning model with thinking mode. Output includes:
  - Detected contract type (Pachtvertrag, Wartungsvertrag, Nutzungsvertrag, PPA, etc.)
  - Per-clause issues with severity (1–5), affected paragraphs, and concrete redline suggestions
  - Cited legal basis (§307 BGB, §309 BGB, EEG, AGB-Recht)
  - Missing-required-clause detection against per-contract-type playbooks
  - Cross-clause consistency findings (e.g. one clause references another that doesn't exist)
  - Cadastral parcel extraction (Gemarkung / Flur / Flurstück) ready for plotting on a map
  - Financial-table reconciliation with deterministic German number parsing (catches arithmetic errors a model would miss)
  - Extraction-quality guard that flags low-OCR-confidence so reviewers don't act on incomplete data

## User experience

- **Persistent conversations** — uploaded contracts, chat history, and analysis results all survive page refreshes and server restarts. Stored in a local SQLite database with the original PDFs preserved on disk.
- **Live progress feedback** during the multi-minute analysis run (current clause, percent complete, estimated remaining time).
- **Conversations sidebar** with auto-titles (first user message or filename), rename, delete.
- **Message history rehydration** — every visible bubble in the chat corresponds to a database record, so users can come back to old contracts and see the full review they ran weeks earlier.

## Infrastructure

- Containerized stack (analyzer LLM, embedding service, pgvector database, Redis) orchestrated by a single Docker Compose file.
- Host processes (FastAPI backend, Vite UI) for fast iteration.
- One-command bring-up / tear-down scripts (`scripts/start.sh`, `stop.sh`, `status.sh`).
- Loopback-only by default; VPN-trusted mode supported for remote access via FortiClient.

## Quality engineering done along the way

- Switched OCR engine to Tesseract with German training data, raising extraction yield ~60% on signed/scanned PDFs.
- Bigger segmentation token budget so long contracts don't silently drop their last sections.
- Routing fix so contract-specific questions don't pull in irrelevant chunks from other contracts in the corpus.
- Comprehensive smoke tests across 44-clause contract analyses (Enercon Wartungsvertrag, WP Altmark Nutzungsvertrag, etc.) verifying the analyzer correctly identifies AGB-Verstöße, project-finance risks, and operational red flags.

## Validated example output

Running the analyzer on a real signed Wartungsvertrag produces ~44 clauses with findings such as:

- §307 BGB-style invalidity in Haftungsbeschränkung clauses
- One-sided cost-allocation provisions flagged as Kardinalpflicht violations
- SLA gaps (Mo-Fr 8-17 vs 24/7 wind-park reality)
- Project-finance concerns (refinancing-blocking clauses, EPK-Ablösung gaps)
- Each finding accompanied by concrete redline language

The system is the running MVP. Ready to demo whenever you're available.

---

## Post-MVP enhancements (Apr 28 – Apr 30 2026)

Significant work shipped after the initial MVP delivery. Two themes: **lawyer-grade DDiQ output** (the multi-document due-diligence report pipeline that lives at `LAI/micro-services/`) and **operational hardening** (incremental persistence, dedup, async flows). These are independent of the conversational chat above; the chat side also picked up real conversational memory.

### DDiQ — multi-document due-diligence report pipeline

**Schema additions** (Pydantic models + JSONB persistence):
- `Evidence` — `{doc_id, doc_filename, page, excerpt, clause}` attached to every Finding / TimelineEntry / Grundbuch / Rückbau check, so a lawyer can click through to the source PDF and verify the LLM's claim.
- `Quantification` — `{mw_affected, eur_impact_estimate, days_until_deadline, rationale}` materiality scorecard per finding. Lets the UI sort / triage by impact instead of skimming text.
- `TimelineEntry` — date-bound milestones (BImSchG-Bestandskraft, Pacht-Laufzeit, Bürgschaft-Ablauf, EEG Inbetriebnahme-Frist, §70 VwGO Widerspruchsfrist) with urgency labels (`expired | urgent | soon | future`).
- `GrundbuchCheck` — per-parcel lessor-vs-registered-owner consistency check + encumbrance list.
- `RueckbauBond` — §35 Abs. 5 BauGB decommissioning-bond extraction (amount, provider, beneficiary, validity, instrument type).
- `Finding` extended with `evidence`, `quantification`, `legal_basis`, `recommended_action`, and `kind` (`section | cross_document | deadline | grundbuch | rueckbau | regulatory`).
- `WEAStatus` extended with `hub_height_m`, `rotor_diameter_m`, `rated_power_kw`, `manufacturer`, `model`, `status_code` (`errichtet | genehmigt | geplant | abgenommen`), `permit_ref`, `warranty_end`.
- `DDiQReportData` extended with `timeline`, `crossDocFindings`, `grundbuchChecks`, `rueckbauBond`, `documentMap`.

**Section prompts rewritten** with German statutory anchors. Every question in `SECTION_QUESTIONS` now cites the specific framework it's grounded in: BImSchG §§4 / 6 / 10 / 15 / 70 VwGO, BauGB §35 (Außenbereichsprivileg + §35 Abs. 5 Rückbau), BNatSchG §§44 / 45 (Verbotstatbestände, Ausnahme), UVPG §§7-9, EEG (Marktwert §23a, Marktprämie §20, BNK §9), TA Lärm + 22./32. BImSchV, AVV Kennzeichnung, BGB §550 Schriftform / §873 Eigentum, GewStG §29, UStG §15a. Each section row carries the anchor and the supporting evidence chunks.

**New extraction passes** in `_generate_report_core`:
- `extract_timeline()` — pulls every date-bound milestone, tags urgency, auto-promotes `expired` / `urgent` entries into Findings with `kind="deadline"`.
- `check_cross_doc_consistency()` — runs after sections+WEAs+parcels are extracted with the full fact set; flags contradictions (turbine count differs across BImSchG / Pacht / EEG, lessor inconsistency, secured-parcel-count < WEA count, missing core document type).
- `extract_rueckbau_bond()` — recurring DD red flag under §35 Abs. 5 BauGB. Missing or insufficient bond auto-promotes to a red finding with `legal_basis="BauGB §35 Abs. 5"` and a concrete `recommended_action`.
- `check_grundbuch_match()` — compares Pachtvertrag-Verpächter against registered Eigentümer per Grundbuch on the top 25 secured parcels; mismatches become red findings under BGB §873.

**10H rule** in `cadastral_pipeline.clearance_radius_for_wea`: for Bayern / Hessen, clearance = `10 × (hub_height + rotor/2)` per BayBO Art. 82 instead of a flat 2000 m. Each `ClearanceZone` records its `radius_source` for UI display ("10H rule (BayBO Art. 82) · hub 167m + rotor/2 65m").

**Async report flow + dedup + persistence:**
- `POST /ddiq/report/generate/async` returns `{report_id, status:"queued"}` immediately; pipeline runs in a server-side `ThreadPoolExecutor` (`REPORT_WORKERS`, default 2). Frontend polls `GET /ddiq/report/{id}/status` for progress.
- **Request-fingerprint dedup**: `sha256(sorted doc_ids, preset, project_name)` indexed on `ddiq_reports.request_fingerprint`. Re-clicking Generate on the same input returns the cached row instantly with `cached:true`. Also reuses a queued/running row younger than 2 hours.
- **Incremental persistence**: `_persist_report_jsonb(rid, ...)` runs after every major phase (metadata → sections → WEAs → infrastructure → cadastral → findings → timeline → cross-doc → rückbau → grundbuch → final). A mid-pipeline crash leaves a usable partial report in `ddiq_reports.report_data` instead of nuking 30-90 min of GPU compute.
- **Connection pool**: `psycopg2.ThreadedConnectionPool` wired into FastAPI startup/shutdown; existing `conn.close()` call sites release back to the pool via a `_PooledConn` wrapper (no call-site refactor needed).
- **Sync handlers**: `async def` removed from endpoints that only do sync I/O (psycopg2 + requests + `file.read()`); these now run in FastAPI's threadpool instead of blocking the event loop.
- **Orphan reaper**: on startup, marks any `queued` / `running` rows from before the restart as `failed` with `error="orphaned: backend restarted mid-job"` — UI no longer polls phantom jobs forever.

**Past Reports browser:**
- `GET /ddiq/reports?limit=50` returns lightweight summaries (id, project_name, status, created_at, doc_count, finding_count, preset) — no full report_data so listing hundreds of historical reports stays cheap.
- `DELETE /ddiq/report/{id}` cascades cleanup across `ddiq_classified_parcels`, `ddiq_contracts` (cascading to `ddiq_contract_parcels` via FK), `ddiq_project_areas`, then `ddiq_reports`. All in one transaction.
- Frontend renders the list as click-to-load cards in the DDiQ Reports tab with a trash button per card; deleting the currently-active report clears localStorage and drops the panel back to the select-docs view.

### Frontend layout & UX polish

- **Frontend split** into its own repo: [LAI-UI](https://github.com/Ravijangid820/LAI-UI). The backend repo no longer ships UI code; the runtime scripts (`start.sh` / `stop.sh` / `status.sh`) point at a sibling clone at `/data/projects/lai/LAI-UI/` (override via `LAI_UI_DIR`).
- **Persistence across refreshes**: chat — `lai.activeConversation` mirrors active conversation id to localStorage so a refresh restores it (with stale-id cleanup after the next sidebar fetch). DDiQ — `lai.ddiq.activeReport` holds the in-flight report id + cached payload; refresh during a 30-90 min run resumes polling against the same report instead of starting over.
- **Demo data removed.** The static "Risk Assessment" tab (6 hardcoded fake risk areas), `DEMO_PARCELS` (12 Tostedt fixtures with names like Hofmann/Meier/Kroeger), `DEMO_REPORT` (Windpark Nordheide / 49.6 MW), and `DEMO_DOCUMENTS` are gone. Risk Assessment now defaults to the DDiQ Reports tab.
- **PDF download**: replaced the misleading HTML-as-PDF "download" with browser-native print-to-PDF via `window.print()` on a Blob URL. DOCX and XLSX format aliases removed (they were HTML-as-`.doc` and CSV-as-`.csv`).
- **Format picker moved**: now appears in the Export step after clicking *Export Report* (was previously in the Configure step before the report even existed). Killed the fake 3-second progress animation.

### Conversational memory (chat side)

- `_load_history(session_id)` in `serve_rag.py` loads the last 16 user/assistant turns from `sessions.db` (clipped to 4000 chars/msg), filters non-chat roles, returns OpenAI message format.
- Wired into all four query modes (`chat`, `rag`, `contract`, `rag+contract`) so coreference ("tell me more about it") and sticky preferences ("from now on reply in English") work.
- **vLLM `--enable-prefix-caching`** on the analyzer container: turn N reuses turn N-1's KV cache for the shared conversation prefix. Big speedup on multi-turn chats.

### Known gaps (deferred)

- **No user scoping** on either backend. The frontend `AuthContext` is currently a demo that accepts any credentials and self-signs a JWT; the backend never validates it. Sessions, documents, and reports are all globally visible. The `users` table column on `sessions` exists but is unused. Real auth (bcrypt + JWT signed with shared secret + WHERE user_id = current_user on every query) is scoped at ~4 hours of work and is on the to-do list before any shared deployment.
- **DDiQ runtime is slow** (~60-90 min on a single doc, 90+ min on 4 docs) because Qwen3.6-27B in thinking-mode with `max_tokens=4096` per call dominates wall time. Two knobs (`HISTORY_MAX_MESSAGES`, `MAX_HIST_CHARS_PER_MSG` for chat; `max_tokens` for DDiQ structured-extraction prompts) are sized conservatively; tightening them would bring DDiQ to ~15-20 min without changing the model.
- **LLM occasionally returns empty content** on the findings prompt — the retry-with-stricter-system-prompt path also returns empty, so the call falls through to a "Manual review required" placeholder. Per-finding (instead of batch) generation would fix this; ~30-line change.
