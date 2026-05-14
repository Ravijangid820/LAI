# Contract Analyzer V2 — Design Doc

> **Status:** Draft, 2026-04-27
> **Owner:** rj
> **Supersedes:** the single-pass `/analyze-contract` flow in `lai.api.serve_rag`
> **Goal:** Increase recall and accuracy of contract review at the cost of latency. Target turnaround ≤ 2 min per contract; no per-clause speed budget.

---

## 1. Motivation

The current analyzer (`serve_rag.py:446 analyze_clause`) does one LLM call per clause with Qwen2.5-7B-Legal-FT, returning `{type, summary, issues, citations}`. It works but has three structural gaps:

1. **Reasoning depth.** A 7B instruction-tuned model classifies clauses well but misses cross-clause inconsistencies, missing-clause patterns, and severity judgments that require holding the whole contract in working memory.
2. **No arithmetic verification.** Contracts contain payment schedules, escalation tables, VAT/Umsatzsteuer breakdowns, area-based rents (€/m²·a). The current pipeline never reconciles these — totals that don't add up are silently accepted.
3. **No structured entity extraction.** Parcel data (Gemarkung, Flur, Flurstück) is buried in prose. Without structured extraction, the data cannot feed the parcel-on-map feature.

V2 keeps the existing endpoint contract but replaces the analysis core with a thinking-model-driven multi-pass pipeline plus deterministic table reconciliation and cadastral NER.

## 2. Non-goals

- Speed parity with V1. V2 is explicitly slower; we accept 60–120 s.
- Multi-language support beyond German.
- Replacing the RAG retrieval path. `/query` and `/upload` are unchanged.
- Online learning / feedback loops.

## 3. Architecture

```
                ┌──────────────────────────────────────────────────┐
upload ───►     │ Docling                                          │
                │   ├─ text (per-page + full)                      │
                │   ├─ tables (structured rows)                    │
                │   └─ document tree (sections, headings)          │
                └────────────────┬─────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   Clause Splitter         Table Extractor          Cadastral NER
   (existing, kept)        (Docling rows +          (Qwen3.6 JSON-mode,
                            Python reconciler)       schema-constrained)
        │                        │                        │
        └────────────┬───────────┴────────────────────────┘
                     ▼
              Deep Analysis Pass
              (Qwen3.6-27B with thinking mode,
               whole-contract context window,
               structured-output schema per
               contract type)
                     │
                     ▼
              ContractAnalysis JSON
              (clauses, issues, parcels,
               financial tables, reconciliation
               findings, cross-clause findings)
```

**Single deep pass, not tiered.** No fast-path on the 7B model. Qwen3.6-27B with thinking mode handles classification, issue detection, severity, and rectify-or-ignore guidance in one structured call per scope (whole contract for cross-clause, per clause for detail).

## 4. Model choice — Qwen3.6-27B

**Why this one:**
- Already cached at `/data/projects/lai/LAI/.runtime-cache/hf/hub/models--Qwen--Qwen3.6-27B/`
- Native thinking-mode toggle (`enable_thinking=True` in chat template) — no separate reasoning model required
- Multilingual, strong on German legal vocabulary
- Fits one RTX Pro 6000 (96 GB) at FP16 (~54 GB), with room for KV cache up to ~64k context. Tensor-parallel across both GPUs gives headroom for the longest contracts (Quadra PPA ~50k tokens).

**Serving:**
- Run via vLLM with `--enable-reasoning` (Qwen3 thinking mode) on a dedicated container, e.g. port 8005.
- `serve_rag.py` already supports a remote LLM via `LLM_API_URL`; V2 introduces a second URL `ANALYZER_LLM_API_URL` so the conversational `/query` path keeps using the fast 7B model and analysis uses the 27B thinker. They coexist.
- Inference budget per call: `max_thinking_tokens` ~8k, `max_new_tokens` ~4k, temperature 0.2.

**Fallback:** if the 27B container is down, analyzer falls back to the existing 7B path with a `degraded: true` flag in the response. UI surfaces the degradation.

## 5. The structured-output contract

The single most important lever for recall is forcing the model to fill a known-shape schema. "Be thorough" prompts dilute attention; required fields with explicit nulls force the model to look.

```python
class Parcel(BaseModel):
    gemeinde: str | None
    gemarkung: str | None
    flur: str | None
    flurstueck: str | None        # e.g. "47/3"
    groesse_m2: float | None
    eigentuemer: str | None
    raw_mention: str              # original text span
    page: int | None

class FinancialTable(BaseModel):
    title: str                    # e.g. "Pachtzins-Staffel"
    rows: list[dict]              # Docling-extracted, normalized
    stated_total: float | None
    computed_total: float | None
    discrepancy: float | None     # stated - computed
    currency: str

class ReconciliationFinding(BaseModel):
    table_title: str
    kind: Literal["sum_mismatch", "vat_mismatch", "escalation_mismatch", "rounding"]
    stated: float
    computed: float
    delta: float
    severity: Literal["info", "low", "medium", "high"]
    note: str                     # human-readable explanation

class Issue(BaseModel):
    severity: Literal[1, 2, 3, 4, 5]   # 5 = blocking
    title: str
    description: str
    affected_clauses: list[str]        # clause IDs
    rectify_or_ignore: Literal["rectify", "ignore", "negotiate"]
    rationale: str                     # required — the model must justify
    suggested_redline: str | None
    legal_basis: list[str]             # § references where applicable

class Clause(BaseModel):
    id: str
    title: str
    text: str
    type: str                          # from contract-type taxonomy
    summary: str
    issues: list[Issue]

class CrossClauseFinding(BaseModel):
    title: str
    involved_clauses: list[str]
    description: str
    severity: Literal[1, 2, 3, 4, 5]
    rectify_or_ignore: Literal["rectify", "ignore", "negotiate"]
    rationale: str

class ContractAnalysis(BaseModel):
    metadata: dict                     # parties, dates, contract_type, jurisdiction
    contract_type: Literal[
        "Pachtvertrag", "Nutzungsvertrag", "Wartungsvertrag",
        "Direktvermarktungsvertrag", "Einspeisevertrag", "PPA",
        "Dienstleistungsvertrag", "Kaufvertrag", "Sonstiges"
    ]
    parcels: list[Parcel]
    financial_tables: list[FinancialTable]
    reconciliation_findings: list[ReconciliationFinding]
    clauses: list[Clause]
    cross_clause_findings: list[CrossClauseFinding]
    missing_required_clauses: list[Issue]   # vs. type-specific playbook
    degraded: bool
    model: str
    thinking_tokens: int
```

Output is enforced via vLLM guided decoding (JSON schema) — not just "please return JSON."

## 6. Per-contract-type playbooks

Each contract type gets a required-clause checklist. If a required clause is missing, that absence becomes a `missing_required_clauses` issue with severity. This is what makes "don't miss any detail" actually work — the model has to either find the clause or explain its absence.

Initial playbooks to implement:

| Contract type | Required clauses |
|---|---|
| Pachtvertrag | Pachtdauer, Pachtzins, Verlängerungsoption, Rückbauverpflichtung, Kündigungsrechte, Untervermietung, Grunddienstbarkeit, Wegerechte |
| Nutzungsvertrag | Nutzungsumfang, Entgelt, Laufzeit, Kündigung, Haftung, Versicherung |
| Wartungsvertrag | Leistungsumfang, Verfügbarkeitsgarantie, Reaktionszeiten, Vergütung, Pönale, Laufzeit, Haftungsbegrenzung |
| Direktvermarktungs-/PPA | Vergütungsformel, Marktprämie, Abnahmeverpflichtung, Bilanzkreis, Force majeure, Curtailment, Laufzeit, Kündigung |
| Einspeisevertrag | Anschlusspunkt, Einspeiseleistung, Vergütung/EEG-Bezug, Mess- und Abrechnungsmodalitäten, Haftung |

Playbooks live in `lai/analyzer/playbooks/<type>.yaml` so legal can edit without touching code.

## 7. Table extraction & arithmetic reconciliation

Docling already returns structured tables (commit `6a6424d`). V2 adds a deterministic reconciliation step before the LLM ever sees a "math" question:

1. **Normalize rows** — strip currency symbols, parse German decimal (`1.234,56` → `1234.56`), unit-aware columns (€, €/m²·a, %).
2. **Detect totals** — last row labeled `Summe / Gesamt / Total` or visually-distinct row from Docling.
3. **Recompute** — sum, percentage, VAT (19% / 7% Umsatzsteuer), escalation formulas (annual % uplift).
4. **Flag discrepancies** with absolute and relative delta, classified into severity buckets:

   | Severity | Threshold (whichever triggers first) | Treatment |
   |---|---|---|
   | `info` | abs ≤ €0.50 **and** rel ≤ 0.1% | Logged, not surfaced in UI by default — almost always rounding |
   | `low` | abs ≤ €5 **or** rel ≤ 0.5% | Surfaced; likely rounding or OCR drift |
   | `medium` | abs ≤ €100 **or** rel ≤ 1% | Surfaced; LLM judges rounding vs. real |
   | `high` | abs > €100 **or** rel > 1% | Surfaced; LLM must classify and justify |

   Both axes are checked; the higher of the two wins. Currency-free tables (e.g. percentages in escalation schedules) use only the relative axis.
5. **Feed findings to the analyzer prompt as observations**, not as questions. The LLM decides whether each is a rounding artifact, an OCR error, or a real defect — but it never does the arithmetic itself.

This is the only way to get correct math at scale. LLM arithmetic is ~85% reliable on multi-row sums and silently wrong on the rest.

## 8. Cadastral NER for parcel mapping

Two-step extraction:

1. **Candidate detection** — regex + Docling layout cues find spans likely to be cadastral references. Patterns include `Flurstück\s+(?:Nr\.?\s*)?[\d/]+`, `Gemarkung\s+\w+`, `Flur\s+\d+`, `Parzelle\s+\d+`, plus address patterns. Cheap, high-recall.
2. **Structured extraction** — Qwen3.6 with strict JSON schema (the `Parcel` model above) on each candidate window plus surrounding context. The LLM disambiguates ("Flurstück 47/3 in Flur 2 der Gemarkung Schweringen") and resolves co-reference within the document.

**Geocoding (out of scope for V2.0, listed in V2.1):** parcels feed a geocoding worker that hits ALKIS / BKG to resolve to coordinates. The map UI consumes coordinates from the worker, not from the analyzer directly.

## 9. Prompt structure

Two prompts, both system-level:

**P1 — Per-clause deep analysis** (called per clause, with full-contract context provided as reference):
- Role: senior German energy-law specialist, 20 years' experience.
- Task: classify clause type, summarize, identify issues with severity 1–5, decide rectify/ignore/negotiate, suggest redline.
- Output: `Clause` schema.
- Thinking mode: ON.

**P2 — Whole-contract pass** (called once, sees all clauses + tables + parcels):
- Task: cross-clause consistency, missing required clauses (against the playbook for the detected type), reconciliation interpretation.
- Output: `metadata`, `cross_clause_findings`, `missing_required_clauses`.
- Thinking mode: ON, larger budget.

Prompts live in `lai/analyzer/prompts/` as plain `.md` files so they can be iterated without code changes.

## 10. API & response shape

**Endpoint:** `POST /analyze-contract` (unchanged URL, replaces V1 body with V2's `ContractAnalysis`).

**Backwards compatibility:** the existing UI consumes `clauses[].issues[]`. V2's schema is a superset — UI keeps working without changes; new fields render where the UI is extended.

**Versioning:** response includes `"analyzer_version": "2.0"`. Old V1 path remains accessible via `?version=1` for A/B during the rollout window.

## 11. Evaluation

We need a real benchmark or this whole thing is vibes. Plan:

- **Gold set:** 5 contracts from `/data/projects/lai/VDRs/` covering Pachtvertrag, Wartungsvertrag, DV/PPA, Nutzungsvertrag, Einspeisevertrag.
- **Annotation:** legal reviewer marks per contract: parties, parcels, required clauses present/absent, table totals, top 5 real issues. Stored as YAML in `LAI/eval/contracts/`.
- **Metrics:**
  - Required-clause recall (per-type playbook)
  - Issue recall vs. annotated top-5 (loose match: same clause + same direction)
  - Parcel extraction F1 (exact match on Gemarkung+Flur+Flurstück tuple)
  - Table reconciliation precision (false positives are expensive — flagging rounding as "high severity" erodes trust)
  - Latency p50/p95
- **Baseline:** current V1 analyzer on the same set.

Eval harness lives at `LAI/scripts/eval/eval_analyzer.py` and reuses the pattern from `multi_model_compare.md`.

## 12. Rollout plan

1. **Stand up Qwen3.6-27B** in vLLM container, verify thinking mode + JSON-guided decoding on toy inputs. (~½ day)
2. **Build cadastral NER** end-to-end against `WP Altmark UW-Nutzungsvertrag` (smallest, focused). Iterate the schema and prompt until F1 ≥ 0.9 on that one document. (~1 day)
3. **Build table reconciler** against the Quadra PPA (richest tables). Pure Python, no LLM. (~1 day)
4. **Integrate into `/analyze-contract`** behind a feature flag. (~½ day)
5. **Annotate the 5-contract gold set** with legal reviewer. (blocked on legal availability)
6. **Run eval harness**, compare V2 vs V1 across all five metrics. Iterate prompts.
7. **Flip default** to V2 if metrics are equal-or-better on every dimension.

## 13. Decisions on previously-open questions

Resolved in this revision so implementation is unblocked. Revisit only if eval results force it.

- **Long-context handling.** Hybrid. Default to full context up to 48k tokens. If a contract exceeds 48k after Docling normalization, run a section-level summarization pass (same Qwen3.6, no thinking, deterministic) producing per-section condensates; the whole-contract pass then sees `[full headers + section summaries + verbatim text of every clause flagged with ≥medium issues]`. Per-clause passes always see the verbatim clause. This protects whole-contract reasoning from attention dilution without losing fidelity on the parts that matter.
- **Confidence scores.** Not in V2.0. The severity 1–5 scale already encodes most of what a confidence score would, and adding a separate confidence field invites mis-calibration. Revisit only if eval reveals systematic over-flagging.
- **Disagreement signal.** Not in V2.0. Doubles cost without a clear product surface. Park.
- **Persistence.** Out of scope. Sessions remain in process memory. Tracked as a separate workstream — file an issue if the larger V2 responses cause real memory pressure.
- **Playbook content.** The Section 6 playbooks are committed as V2.0 baseline. Legal review can amend the YAMLs in-place without code changes; treat the initial set as a working hypothesis, not a contract with the legal team.

## 14. Files & touch points

| Path | Change |
|---|---|
| `LAI/src/lai/api/serve_rag.py` | New `analyze_contract_v2`, V1 kept; routing via `version` flag |
| `LAI/src/lai/analyzer/` | New package: `playbooks/`, `prompts/`, `reconciler.py`, `cadastral_ner.py`, `schema.py` |
| `Docker/llm-analyzer.docker-compose.yml` | New vLLM container for Qwen3.6-27B on port 8005 |
| `LAI/eval/contracts/` | Gold set YAML + 5 PDFs (already in VDRs/) |
| `LAI/scripts/eval/eval_analyzer.py` | New eval harness |
| `LAI/docs/INFRASTRUCTURE.md` | Document the new analyzer service |

## 15. Estimated effort

| Workstream | Effort |
|---|---|
| Qwen3.6-27B serving + smoke test | 0.5 d |
| Cadastral NER | 1.0 d |
| Table reconciler | 1.0 d |
| Schema + playbooks (5 types) | 1.5 d |
| Prompt engineering + thinking-mode tuning | 1.5 d |
| Endpoint integration + UI compat | 0.5 d |
| Gold-set annotation (legal-reviewer-blocked) | 1.0 d external |
| Eval harness + iteration | 1.5 d |
| **Total dev** | **~7.5 d** + 1 d external |
