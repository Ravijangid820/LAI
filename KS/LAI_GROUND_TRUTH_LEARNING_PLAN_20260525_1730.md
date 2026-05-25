# LAI Ground-Truth Audit + Ehrlicher Lern-Plan

**Erstellt:** 2026-05-25 17:30 CET
**Methodik:** 4 parallele Explore-Agenten haben jeweils Code/Daten gelesen — keine Doc-Claims übernommen
**Reviewer:** Claude Code / ks_admin
**Korrigiert:** Mehrere falsche Behauptungen aus den vorherigen KS-Reports (siehe Korrektur-Tabelle unten)

---

## 1. KORREKTUR meiner früheren Behauptungen

| Mein früherer Claim | Code-/Daten-Realität |
|---|---|
| "8.3M Embeddings" | **33.935.540 child_chunks** + 13.8M parent_chunks in `corpus_child_chunks` / `corpus_parent_chunks` |
| "13 Sections / DDiQ" | **4 Sections** (overview, land, permits, economics) mit **36 Fragen total** + ~10 weitere Extractoren = ~60–70 LLM-Calls/Report |
| "max_tokens=4096 in ddiq_report.py:504" | `max_tokens=4096` ist Default im **LLM-Client `ddiq/llm.py:236`**, NICHT in ddiq_report.py |
| "Thinking-Mode Bottleneck" | **Thinking-Mode ist explizit DISABLED** in `ddiq/llm.py:99` (`thinking_mode_enabled=False`). Meine Production-Report-Diagnose war auf falscher Annahme aufgebaut |
| "Citation-Verification nur Regex-Stub, nicht in /query integriert" | **Vollständig produktiv**: `src/lai/common/citation/validator.py` (243 Z.), 249 Z. Tests, aufgerufen in `serve_rag.py:3452, 3922`. Fabrizierte Citations entfernt, ungrundete Sätze als `(unbelegt)` markiert, Telemetrie `fabricated=…` |
| "Feedback-Endpoint existiert vermutlich" | **DB + API + 183 Z. Tests komplett**: `feedback`-Tabelle in `persistence.py:125-142`, `POST /feedback` (serve_rag.py:4548), `GET /sessions/{id}/feedback` (Z. 4611). **ABER kein Consumer** — niemand liest das für Training oder Eval |
| "Chunking char-basiert 1200/200" | **Sentence-aware** mit 170+ legalen Abkürzungen (Abs., BGH, BauGB, …). Defaults: target=1200, max=2000, min=200, overlap=150. `SECTION_PATTERNS` für §/Art./Tenor existiert, wird im Standard-Chunker aber nicht erzwungen |
| "Fine-Tune shelved" | **LoRA-Adapter ist gemerged + deployed**: `/data/projects/lai/models/qwen25-7b-legal-lora/` (v1+v2+merged = 50+ GB). Best eval_loss 0.553 @ checkpoint-23000. Trainiert 2026-04-22/23. |
| "SaulLM nur Eval-Option" | **Saul-7B-Instruct (27 GB) + Leo-Hessianai-7B (13 GB) liegen on-disk** in `/data/projects/lai/models/`. Wurden aber nicht in Produktion ge-switcht. |
| "WEA-Specs scheitern wegen PyMuPDF" | Scheitern weil **Prompt fragt nach 'datasheet/Erläuterungsbericht'**, aber Source-Docs sind Verträge. `_apply_canonical_specs()` versucht Fallback aus project-canon |
| "ALKIS 16 Bundesländer" | **Bestätigt**: alle 16 BL mit BBox + `is_in_bundesland()` Plausibilitäts-Gate gegen Nominatim-Fehler |
| "R@5 = 66%, MRR = 0.492" (aus README) | **Reproduzierbar: hybrid_rerank R@5 = 0.58, MRR = 0.373** auf 500 queries (`scripts/eval/rag_eval_results/hybrid_rerank_n500.json`). Die 66%/0.492-Zahl aus README ist eine ältere Eval auf anderem val-Sample |

**Was ich gar nicht erwähnt hatte und entscheidend ist:**
- **`_guardrail.py`** in micro-services: bereinigt LLM-Output (Defensive-AI-Floskeln → kanonisch, Hedge-Wörter stripped, Sprach-Detection)
- **`_reconcile.py`**: Cross-Source-Konfliktauflösung mit Präzedenz `cadastral > llm > regex > fallback`, loggt Divergenzen >2 %
- **`/query/stream` SSE-Endpoint** in serve_rag.py:3713 — real
- **`/analyze-contract`-Pipeline** mit Progress-Polling (Z. 4384–4414) als separater Klausel-Workflow
- **org_id-Phase-B-Multi-Tenancy**: org_id-Spalten überall, `session_owned_by()`-Check vor Feedback-Insert
- **52 DDIQ-Dokumente bereits verarbeitet**, **39 DDIQ-Reports generiert** (4 davon Mai 2026)
- **18 handmade DD-Reports** (DOCX 2020-2021) liegen on-disk → Gold-Standard für Eval/Training
- **9 Windpark-VDRs** (nicht 5): Altmark, Tostedt, Sebbenhausen, Zodel, Hudehatten, Lamstedt, Beppener Bruch, "33:34", Butterberg — total 6.3 GB
- **22.000+ PDFs** in `Library/` (5.5 GB deutsche Windenergie-Rechts-Sammlung)
- **PostgreSQL aktiv: 920 GB**, SQLite-Cache: 764 GB, Models: 98 GB

---

## 2. GROUND-TRUTH ZUSTAND LAI (Mai 2026)

### 2.1 Was läuft produktiv

**Container:** `lai-backend` (DDiQ :18001), `lai-worker` (Celery), `lai_analyzer_llm` (Qwen2.5-72B-AWQ :8005), `lai_embedding` (Qwen3-Embedding-8B :8003), `lai-test-reranker` (:8004), `lai_postgres_main` (:5434, 920 GB), `lai_redis`, `lai_neo4j` (ungenutzt).

**Embedding-Korpus:** 33.9M child + 13.8M parent chunks, Qwen3-Embedding-8B 4096d halfvec, hybrid (dense+BM25+RRF) + Qwen3-Reranker-8B Rerank.

**DDiQ-Pipeline:** 4 Sections × ~9 Fragen + Extractoren (Findings, Timeline, Rückbau-Bond, Grundbuch, Cross-Doc-Consistency, WEA-Specs/Status, Infrastructure, Metadaten) = ~60–70 LLM-Calls/Report. ALKIS für alle 16 BL, 10H-Regel `radius = 10 × (hub + rotor/2)`, Clearance-Defaults pro BL.

**Bereinigungs-Layer:** `_guardrail.py` (Defensive-AI strippen, Hedge-Wörter, Sprache) + `_reconcile.py` (Konflikt-Präzedenz cadastral>llm>regex) + `validator.py` (Citation-Fabrication-Filter).

**Feedback-Infra:** DB-Schema + Endpoints + Tests — aber kein Consumer.

**Fine-Tune deployed:** Qwen2.5-7B-Legal-LoRA, eval_loss 0.553, trainiert auf 190k synthetischen Samples.

### 2.2 Echte Metriken (reproduzierbar)

| Mode | R@1 | R@5 | R@10 | MRR | Quelle |
|---|---|---|---|---|---|
| hybrid_rerank (n=500) | 0.232 | **0.58** | 0.70 | **0.373** | `scripts/eval/rag_eval_results/hybrid_rerank_n500.json` |
| hybrid_prefix (n=100) | 0.35 | 0.56 | 0.66 | 0.434 | README-Tabelle |
| dense_prefix (n=100) | 0.31 | 0.55 | 0.63 | 0.413 | README-Tabelle |

**Best-Eval LoRA:** 0.553 eval_loss, 0.874 mean_token_accuracy bei step 23000.

### 2.3 Was es NICHT gibt (verifiziert, keine Behauptung)

- ❌ DPO / KTO / RLHF / Reward Model
- ❌ Embedding-Modell-Fine-Tune (nur HTTP-Client)
- ❌ Hard-Negative-Mining / Triplet-Loss / Contrastive
- ❌ GraphRAG / LightRAG / Knowledge Graph (nur 12 Domain-Tags via Step 3)
- ❌ HHEM / Patronus / Cleanlab / Vectara-Integration
- ❌ LLM-as-Judge Eval (nur R@k + MRR auf gold-parent)
- ❌ Automatischer Eval-Trigger / Continuous Quality Monitoring
- ❌ Feedback → Training-Daten-Pipeline (Lawyer-Signale gehen in DB und stagnieren)
- ❌ Audit gegen 15.8% Citation-Fabrication-Behauptung (kein `audit_training_data.py` verifiziert)
- ❌ DDiQ-Tests (1 Test-Datei für VLM-OCR, 0% Coverage für Pipeline-Logik)

---

## 3. EHRLICHER LERN-PLAN (basierend auf Ground Truth)

### Prinzip
LAI hat **massiv mehr Substanz** als ich initial dachte — 33.9M Embeddings, gemerged-LoRA, vollständige Citation-Validierung, Feedback-DB. Was fehlt ist **nicht Aufbau, sondern Schliessen von Loops**:
1. Feedback-DB → Training-Daten
2. Citation-Validator → Training-Daten-Audit
3. 18 handmade DD-Reports → Gold-Standard-Eval-Set
4. 39 generierte DDIQ-Reports → Quality-Bewertung-Korpus

Das ist ein **Brownfield-Plan**, kein Greenfield.

### Phase 1 — Feedback-Loop schliessen (2–3 Wochen, höchster Hebel)

**Heutiger Zustand:** DB-Tabelle `feedback(rating, reason, comment, session_id, message_id)` + Endpoints existieren, niemand liest sie.

**Was bauen:**
1. **Export-Job** `scripts/training/export_feedback_to_dpo.py`:
   - Liest `feedback` JOIN `messages` JOIN `corpus_child_chunks` (retrieved chunks aus Session-History)
   - Generiert DPO-Pairs: `(query, chosen=answer mit rating=+1, rejected=answer mit rating=-1)` für selbe oder ähnliche Queries
   - Fallback bei wenig Daten: `(query, chosen=answer mit thumbs-up, rejected=GPT-paraphrasiertes-Schlecht-Beispiel)`
2. **Frontend-Erweiterung** (LAI-UI separate repo): Pro Antwort-Block + pro Finding-Block:
   - 👍/👎 Buttons
   - Optional: Inline-Korrektur ("falsche Citation: §X korrekt: §Y")
   - Schreibt via `POST /feedback` (existiert bereits)
3. **DPO-Training-Run** mit TRL `DPOTrainer` auf Qwen2.5-7B-Legal-LoRA-merged (existiert):
   - Mindestens 1000 Pairs gewünscht, 200 reichen für ersten Sanity-Check
   - 1 GPU-Tag auf RTX Pro 6000
4. **A/B-Eval** vs. aktueller Fine-Tune-Adapter auf `multi_model_compare.md`-Schema

**Erwarteter Effekt:** Citation-Quality & Stil-Anpassung an eure Anwälte. Kein Magic — DPO braucht ehrliche Signal-Qualität.

**Daten die du brauchst:** 500+ bewertete Antworten von echten Anwälten. Aus den **39 bereits generierten DDIQ-Reports** lässt sich ein **kalter Start** machen (Lawyer schaut jeden Report durch, bewertet jede Finding-Zeile).

---

### Phase 2 — Retrieval-Tuning mit Hard-Negatives (3–4 Wochen)

**Heutiger Zustand:** Embedding-Modell ist Standard-Qwen3-Embedding-8B (HTTP-Client only), kein Fine-Tune-Code. Eval läuft auf 500 queries: R@5 = 0.58 (Ziel SOTA 2026: ≥80%).

**Was bauen:**
1. **Triplet-Mining-Script** `scripts/training/mine_hard_negatives.py`:
   - Input: 190k existierende `training_samples` mit `parent_id`
   - Generate für jede Query: positive = gold parent_chunk, hard_negative = top-ranked aber wrong parent (BM25-retrieval-Tricks)
   - Filter: nur Triplets wo `cosine(query, hard_neg) > 0.7` UND `parent_id != gold_parent`
2. **Embedding-Fine-Tune** mit `sentence-transformers` MultipleNegativesRankingLoss:
   - Base: Qwen3-Embedding-8B (oder kleineres Modell für ersten Run, Qwen3-Embedding-0.6B)
   - 5k–20k Triplets, 4–8 GPU-Stunden
3. **Eval erneut** mit `lai.search.eval --mode hybrid_rerank --n 500`
4. **Replace embedding-server** falls R@5 ≥ +10 Punkte

**Voraussetzung:** Citation-Validator als Filter VOR Triplet-Mining — sonst lernt Embedding fabriziertes Gold (siehe Punkt 3.5).

**Erwarteter Effekt:** R@5 +10–20 Punkte (Kanon-2-Referenz auf Legal RAG Bench: +34 Retrieval-Acc).

---

### Phase 3 — Citation-Audit der 200k Training-Samples (1–2 Wochen)

**Heutiger Zustand:** README erwähnt "15.8% fabricated", aber Audit-Script `audit_training_data.py` wurde NICHT verifiziert. Wir wissen nicht ob die merged-LoRA auf gefilterten oder ungefilterten Daten trainiert wurde.

**Was bauen:**
1. **Verifikations-Script** `scripts/audit/verify_training_citations.py`:
   - Lädt `training_samples.messages` (190k train)
   - Für jede Antwort: extrahiert Citation-Handles via `extract_citations()` (existiert!)
   - Prüft gegen `parent_chunks` ob die zitierten §§/Klauseln wirklich im Source-Chunk existieren
   - Output: `audit_results.jsonl` mit `(sample_id, fabricated_pct, sentences_flagged)`
2. **Filter**: nur Samples behalten mit `fabricated_pct == 0`
3. **Re-Training** Qwen2.5-7B-Legal-LoRA-v3 auf gefilterten Daten

**Erwarteter Effekt:** Reduziert Citation-Halluzination des Fine-Tunes nachweisbar. Wir können erstmals den 17–33%-Stanford-Wert für LAI seriös messen.

**Daten die du brauchst:** Existieren bereits (`training_samples`, `parent_chunks`, `Citation-Validator`).

---

### Phase 4 — Gold-Standard-Eval-Set aus handmade DD-Reports (1–2 Wochen)

**Heutiger Zustand:** 18 handmade DOCX-Reports (Altmark, Hudehatten, Sebbenhausen, Tostedt, Zodel, Beppener Bruch, Butterberg, "33:34") aus 2020–2021. **Nicht im Eval-Korpus**.

**Was bauen:**
1. **Parser** für die handmade Reports:
   - Extrahiert pro Report: Finding-Liste mit (Ampel, Beschreibung, Beleg-§§, Risiko-Quantifizierung)
   - Schreibt nach `eval/gold_standard/handmade_findings.jsonl`
2. **End-to-End-Vergleich**: lasse DDiQ-Pipeline auf gleichen VDR-Docs laufen, vergleiche generierte Findings mit handmade
3. **Metriken**: Precision/Recall pro Finding-Kategorie, Citation-Match-Rate, Ampel-Übereinstimmung

**Erwarteter Effekt:** Erstmals harte End-to-End-Qualitätsmessung. Bisher misst die Eval nur Retrieval (R@k), nicht ob die finalen Findings den anwaltlichen Standard erreichen.

**Daten die du brauchst:** Existieren bereits.

---

### Phase 5 — DDIQ-Coverage / Continuous Eval (1 Woche, niedrige Risiko)

**Heutiger Zustand:** 1 Test-Datei für VLM-OCR, 0% Coverage für Pipeline-Logik. Eval läuft manuell.

**Was bauen:**
1. **Pytest-Suite** für `_guardrail.py`, `_reconcile.py`, `validator.py`, `extractors/{findings,rueckbau,grundbuch,timeline}.py`
2. **Smoke-Test** `scripts/ops/smoke_ddiq.py`: lädt ein Lamstedt-VDR, generiert Report, vergleicht mit gespeichertem Erwartungs-Output (snapshot test)
3. **CI-Hook** für Continuous Eval: nightly run von `lai.search.eval --mode hybrid_rerank --n 500`, Alarm wenn R@5 < 0.55

**Erwarteter Effekt:** Regressionen werden sichtbar, vor allem nach Phase 1/2/3.

---

## 4. PRIORISIERUNG nach ROI

| # | Maßnahme | Aufwand | Erwartetes Resultat | Risiko |
|---|---|---|---|---|
| 1 | Citation-Audit der 190k Samples (Phase 3) | 1–2 W | Mess-Baseline für Halluzination, gefilterte Re-Train-Daten | sehr niedrig |
| 2 | Gold-Standard-Eval aus 18 handmade Reports (Phase 4) | 1–2 W | Erste E2E-Qualitätszahlen | niedrig |
| 3 | Feedback-Loop UI + Export (Phase 1, Step 1+2) | 2 W | Lawyer-Signale fließen | niedrig |
| 4 | DPO-Run auf 500+ Pairs (Phase 1, Step 3+4) | 1 W (nach Daten) | LoRA-v3 mit Stil-Anpassung | mittel |
| 5 | Hard-Negative-Triplet-Embedding-Tune (Phase 2) | 3–4 W | R@5 → potenziell 0.70+ | mittel |
| 6 | DDiQ-Coverage-Tests (Phase 5) | 1 W | Regression-Schutz | niedrig |

**Kritischer Pfad:** 1 → 3 → 4 (Citation-Audit muss vor jedem weiteren Training stehen, sonst lernen wir fabriziertes Gold). Phase 2 + Phase 4 + Phase 5 können parallel laufen.

---

## 5. WAS DU EINSPEISEN SOLLTEST (Daten-Wishlist)

**Sofort verfügbar (im System):**
- 33.9M Corpus-Embeddings — Quelle für Hard-Negatives
- 190k synthetische Q&A — Quelle für DPO + Re-Filter
- 39 DDIQ-Reports + 18 handmade — Quelle für Gold-Eval
- 9 VDRs als Source-of-Truth

**Was eure Anwälte beisteuern müssen:**
1. **500+ bewertete DDIQ-Findings** (👍/👎 + optional Korrektur-Text) — kalter Start für Feedback-Loop
2. **20–50 "perfekte" DD-Antworten** als few-shot-Anker (was wäre die ideale Antwort gewesen?)
3. **Liste typischer Anwalts-Korrekturen** (z.B. "wir nennen das nicht 'Pacht', sondern 'Dienstbarkeit'" — Stil-Glossar)
4. **Anonymisierte interne DD-Reports**: hochwertiger als die handmade 2020-er, falls vorhanden

**Was eine Kanzlei-eigene Domäne wirklich differenziert (über das hinaus was Public-Vendors haben):**
- Eure Risiko-Schwellen ("ab welchem Pacht-Restlaufzeit-Wert wird das gelb/rot?")
- Eure Standard-Empfehlungen pro Finding-Typ
- Eure Mandanten-Briefings (anonymisiert)

---

## 6. WAS LAI SCHON 2026-SOTA-NAH KANN

Damit ich fair bleibe — nicht alles ist Lücke:

- **Citation-Validator produktiv** (handle-basiert, fabricated-stripped, `(unbelegt)`-Markierung) — das haben viele Tier-1-Vendors nicht so explizit
- **`_guardrail.py` + `_reconcile.py`** — Output-Hygiene + Cross-Source-Reconciliation: das ist sonst meist nicht offen dokumentiert
- **ALKIS für alle 16 BL + 10H-Berechnung** — keine Konkurrenz bei Public-Vendors für DE-Windkraft
- **9 VDRs voll ingested + 39 Reports generiert** — gelebte Praxis, kein Demo
- **Phase-B-Multi-Tenancy** mit `session_owned_by()`-Check vor Mutations — kein "Demo-Mode-Auth"
- **LoRA-Fine-Tune merged + deployed** auf 190k legalen Samples — die LLM-Anpassung ist real, nur die Daten-Qualität ist nicht audited
- **Sentence-aware Chunking** mit 170+ legalen Abkürzungen — besser als der typische 1200-char-Splitter

---

## 7. OFFENE FRAGEN AN DICH

1. **Wer audited die Anwalts-Bewertungen?** Brauchen wir mehr als einen Anwalt pro Finding (Inter-Annotator-Agreement)?
2. **Wann darf LAI Mandantendaten zu Training-Daten machen?** Anonymisierungs-Pipeline existiert nicht. Brauchen wir vor Phase 1 eine SOP?
3. **Soll der Re-Train-LoRA-v3 die merged-v2 ersetzen, oder parallel deployen für A/B?**
4. **Phase 4 (Gold-Eval) hat höchste Aussagekraft, aber niemand hat die handmade Reports geparst — wer macht das?**
5. **Frontend-Devs verfügbar?** Ohne UI-Erweiterung kein Feedback-Loop.

---

*Generiert auf Basis von 4 parallelen Ground-Truth-Audits durch Explore-Agenten (Code-Read, nicht Doc-Read), 2026-05-25 17:30 CET.*
