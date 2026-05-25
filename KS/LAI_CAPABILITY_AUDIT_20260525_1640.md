# LAI Capability Audit — Was LAI kann / nicht kann

**Erstellt:** 2026-05-25 16:40 CET
**Autor:** Claude Code / ks_admin
**Quellen:** LAI_PRODUCTION_ANALYSIS_REPORT.md (Mai 2026), LAI-UI-FRONTEND-ANALYSIS.md, LAI/README.md, LAI/TODO.md, micro-services/{ddiq_report,cadastral_pipeline}.py, KS/-Docs (Jan 2026), live `docker ps`

---

## 1. WAS LAI HEUTE PRODUKTIV KANN

### 1.1 Laufende Container (live, 2026-05-25)

| Container | Zweck | Port |
|-----------|-------|------|
| `lai-backend` | DDiQ-Microservice (async Due-Diligence) | 18001 |
| `lai-worker` | Celery Job-Queue für lange Reports | — |
| `lai_analyzer_llm` | Qwen2.5-72B-AWQ Analyzer (vLLM, tensor-parallel 2 GPUs) | 8005 |
| `lai_embedding` | Qwen3-Embedding-8B (4096-dim) | 8003 |
| `lai-test-reranker` | Qwen3-Reranker-8B (multilingual) | 8004 |
| `lai_postgres_main` | pgvector PG16, halfvec(4096) | 5434 |
| `lai_redis` | Embedding-Cache, Session-Store | 6379 |
| `lai_neo4j` | (registriert, ungenutzt) | 7474/7687 |
| `lai` | Frontend-Build / Static | — |

### 1.2 Conversational RAG (`serve_rag`, :18000)
- **Hybrid Retrieval**: Dense (pgvector HNSW) + BM25 + RRF → Cross-Encoder-Rerank
- **Streaming**: `POST /query/stream` per SSE (live seit Mai)
- **Conversational Memory**: 16 Turns, Prefix-Caching gegen Qwen-Analyzer
- **Bilingual**: DE ⇄ EN per `target_language`-Param
- **Multi-Tenant**: JWT + per-User-Postgres-Schema (im Mai gelandet)
- **Latenz**: ~1.8–5.3 s end-to-end (50ms Query-Analyse → 80–200ms Embedding → 60–100ms Hybrid → 100–300ms Rerank → 1–3s LLM)

### 1.3 DDiQ-Due-Diligence-Microservice (`lai-backend`, :18001)
13-Section-Workflow für Windpark-Verträge:

| Section | Was extrahiert wird |
|---------|---------------------|
| ProjectFacts | name, location, capacity_mw, turbine_count |
| Genehmigungen / BImSchG | Bescheide, Auflagen |
| Pachtverträge | Laufzeit, Pächter, Eigentümer |
| **Rückbau-Bond** | Status, Amount, Bürge |
| **Grundbuch** | Abteilungs-II/III-Lasten |
| **10H-Regel** | Mindestabstand (Bayern) |
| **ALKIS-Kataster** | Parcel-IDs, GeoJSON, Clearance-Zones 500m/1000m |
| Versicherung / EPC / O&M / Grid / EEG / Nachbarschaft / Finanzen / Umwelt | Section-spezifische Prompts |
| Timeline | Deadlines + Urgency (expired/urgent/soon) |
| Findings | Ampel (rot/gelb/grün), Legal Basis, Evidence |

- **Async-Pattern**: `POST /ddiq/report/generate/async` → poll `/ddiq/report/{id}/status`
- **Fingerprint-Dedup**: gleiche Docs+Preset = ein Report-ID
- **ALKIS für alle 16 Bundesländer** (pgvector-Cache, 30 Tage TTL)
- **OCR**: Docling (strukturiert) + Tesseract-Fallback + optional VLM-OCR

### 1.4 Datenpipeline (6 Steps)
- **Step 1**: MinIO-Rohdaten → normalisierte Text-Segmente
- **Step 2**: Segmente → Parent-/Child-Chunks (Postgres)
- **Step 3**: Domain-Klassifikation via Qwen2.5-72B
- **Step 4**: Contextual Enrichment (Anthropic-Ansatz)
- **Step 5**: 200K synthetische Q&A-Samples für Fine-Tune
- **Step 6**: Embeddings → pgvector
- Alle Steps idempotent, `--dry-run`, SIGINT-graceful
- **Docker-frei lauffähig** via SQLite (`processed/pipeline_local.db`, 1 GB Pipeline + 284 GB Embeddings)

### 1.5 Frontend (LAI-UI, separates Repo, React 19/Vite)
- Chat-UI mit Multi-Turn, Markdown-Render, Uploads
- DDiQ-Report-Browser mit Findings-Tabelle + Risk-Overview-Aggregation
- Async-Polling für lange Reports
- Dark/Light, Theme-Persist
- Auth-Context (Demo-Mode, JWT lokal gespeichert)
- Project Workspaces (UI-Mock, Backend noch nicht persistent)

### 1.6 Metriken (gemessen, aktuelle Eval)

| Mode | R@1 | R@5 | R@10 | MRR |
|------|-----|-----|------|-----|
| Dense + Qwen3-Prefix | 31% | 55% | 63% | 0.413 |
| Hybrid + Prefix | 35% | 56% | 66% | 0.434 |
| **Hybrid + Prefix + Qwen3-Reranker-8B** | **37%** | **66%** | **72%** | **0.492** |

- Korpus: **8.3M Embeddings** nach Dedup (`scripts/archive/dedup_phase1_rechunks.py`)
- 672 GB deutscher Legal Corpus (Gesetze, Verträge, DD-Reports, Literatur, HuggingFace-Cases, OpenLegalData)

---

## 2. WAS LAI NICHT KANN — Blocker & Lücken

### 2.1 🔴 Kritische Production-Blocker

1. **DDiQ 60–90 min pro Report** — Root Cause `max_tokens=4096` in `ddiq_report.py:504`. Strukturierte Outputs sind <500 Tokens — 4096 buyt nichts, Thinking-Mode emittiert unsichtbare Reasoning-Traces bis Limit. **Fix = 1-Zeile, → ~20 min**
2. **Citation-Halluzinationen 17–33%** (Stanford-HAI-Studie). Regex-Patterns für deutsche Rechtsquellen (§, Art., Abs., BGH, BVerfG …) existieren als Klassen-Stub, sind aber **nicht in `/query`-Response integriert**
3. **Fine-Tune eingefroren**: erster LoRA-Run lief (eval_loss 0.977 → 0.553, token-acc 76 % → 86 %), aber Audit zeigte **15.8 % fabrizierte §§/Klauseln im Teacher-Output**. Shelved bis Verifikations-Loop steht
4. **DB-Pool = 10** → bricht ab ~10 parallelen Usern (Fix = 30 min, increase auf 50 + pgBouncer)
5. **Analyzer-LLM @ 0.75 GPU-Memory** → max. 2–3 concurrent requests, danach queueing

### 2.2 🟠 Quality-Lücken

- **Chunking**: character-basiert (1200/200), keine Satz-/Klauselgrenzen. Semantic-Chunking spec'd (Jan-2026-Guide), nicht shipped. Benchmark sagt +70 % Accuracy bei Semantic
- **Findings-Extraction**: ~15 % der Findings landen als Placeholder "Manual review required" (Batch-LLM-Call verliert Context). Per-Finding-Retry-Fix existiert nicht (2h Aufwand)
- **WEA-Specs**: `hub_height_m`, `rotor_diameter_m`, `rated_power_kw` häufig NULL — PyMuPDF flattent Spec-Tabellen, Docling tabelliert sie nicht zuverlässig
- **ALKIS-WFS keine Retry**: Niedersachsen (LGLN) liefert oft HTTP 530 (Cloudflare). Fallback ist nur estimated polygons (1h Fix)
- **Streaming-UI nicht da**: Backend streamt (SSE), Frontend wartet auf volle Response

### 2.3 ⚠️ Frontend-Lücken

| Issue | Fix-Aufwand |
|-------|-------------|
| Kein Response-Streaming-UI | 8h |
| Keine Retry-Logik bei Netzfehlern | 2h |
| Fixes 2s-Polling, kein adaptives Backoff | 2h |
| Keine Error-Boundaries (Component-Crash = leere Seite) | 1h |
| Keine Paginierung (Listen cap @ 50) | 4h |

### 2.4 ⚠️ Architektur-Limits

- **Single-Instance Postgres** (kein Replica) → SPOF
- **Redis Single Instance** (kein Sentinel) → Cache-Miss → +5–10s
- **kein Audit-Trail / Soft-Delete / Retention-Policy** → GDPR-Lücke
- **kein verteiltes Tracing** (Prometheus + Grafana sind im Mai gelandet, OpenTelemetry fehlt)

### 2.5 State-of-the-Art aufgeschoben (Jan-2026-Roadmap nicht umgesetzt)
- SaulLM-Integration (legal-spezifisch) — nur als Eval-Option, nicht deployed
- Multi-Round RAG (+20 % Recall) — spec'd, nicht shipped
- German Legal NER (PER/ORG/LOC/NRM/DAT/MON) — nur Klassen-Stub
- Confidence Scoring — Framework spec'd, nicht in API
- Knowledge Graph für Cross-References — gar nichts
- BGE-M3-Fine-Tune auf Legal Corpus — nicht erfolgt (statt dessen Modellwechsel zu Qwen3)

---

## 3. WAS SICH SEIT JAN 2026 GEÄNDERT HAT

| Bereich | Damals (Jan 2026) | Heute (Mai 2026) |
|---------|-------------------|------------------|
| Embedding | BGE-M3 (1024d) | **Qwen3-Embedding-8B (4096d, halfvec)** |
| Reranker | MiniLM-EN (Plan) | **Qwen3-Reranker-8B multilingual** |
| LLM | LLaMA-3 / Qwen | **Qwen2.5-72B-AWQ + Qwen2.5-7B-LoRA** |
| DDiQ | nicht existent | **µ-Service @ :18001, 13 Sections, async** |
| ALKIS | nicht da | **alle 16 Bundesländer, GeoJSON, Clearance-Zones** |
| OCR-Fallback | nur Docling | **+ Tesseract + optional VLM-OCR** |
| Auth | global sichtbar | **echtes JWT + Tenant-Isolation** |
| Streaming | nein | **SSE-Streaming** |
| Monitoring | nein | **Prometheus + Grafana (9 Panels)** |
| Feedback | nein | **Lawyer-Thumbs persistiert** |
| Mehrsprachig | nein | **DE ⇄ EN** |
| Fine-Tune | geplant | **gelaufen, dann shelved** wegen 15.8 % Citation-Halluzinationen |
| Codebase | data_processing/ + LAI/ | **lai.common-Paket: auth/chunk/citation/connectors/llm/embedding/reranker mit mypy + 85 % Coverage** |

---

## 4. TIME-TO-MARKET (Production Report Mai 2026)

| Phase | Dauer | Liefert |
|-------|-------|---------|
| **Phase 1 — Critical Fixes** | 2 Wochen | Auth-Scoping, Token-Fix, Pool-Tuning → **Beta-fähig für 20–50 User** |
| **Phase 2 — Hardening** | 4 Wochen | Streaming-UI, Citation-Verification, Observability → **GA-fähig für 100 User** |
| **Phase 3 — Scale** | Ongoing | Multi-GPU, Postgres-Cluster, Fine-Tune-Retry → **500+ User** |

### Top-3 Quick Wins (je 30 Min)
1. `max_tokens 4096 → 1024` in `ddiq_report.py:504` → **−50 min/Report**
2. DB-Pool `10 → 50` in `config.py:37` → **+5× concurrent users**
3. ALKIS-WFS-Retry (20 Zeilen) → **95 % → 99 % Cadastral-Success**

---

## 5. RISIKEN

- **Datenleck**: Bis Phase-1-Auth-Fix sieht jeder User Daten anderer User (GDPR-Verletzung)
- **Citation-Fabrication**: 17–33 % der Antworten enthalten misgrounded Citations → Anwalts-Haftungsrisiko
- **Cascading Failures**: ALKIS-Down → DDiQ-Halt (nur geschätzte Polygone); LLM-Timeout (600s) → Orphan-Reaper, aber User sieht "failed"

---

*Generiert auf Basis von Explore-Agent-Recherche, 2026-05-25 16:40 CET.*
