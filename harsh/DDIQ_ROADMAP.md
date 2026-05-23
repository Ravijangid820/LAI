# LAI / DDiQ — Competitive-Readiness Roadmap

**Date:** 2026-05-14
**Trigger:** Smoke-test review of `LAI/docs/smoke_test_report.pdf` (DDiQ report
"Windpower Lamstedt", generated 2026-04-29) + boss feedback ("not ready to
compete", "should answer all user questions", "should learn").
**Decisions taken (from the team):**
- "Answer all questions" = **blended** — fix extraction *and* reach beyond the
  uploaded documents (legal corpus + external registries), phased.
- "Learn" = **feedback loop** — lawyers correct/rate outputs, LAI improves from
  corrections. No GPU retraining for now.
- **Priority surface = the DDiQ report engine** (the smoke-test path).
- No specific competitor benchmark identified yet.

**Ground rule:** every claim below is traced to a file/line or a live probe.
Items marked *(to confirm)* still need verification before implementation.

---

## 1. What the smoke test proved

### Failures (verified from the PDF + confirmed in code)

| # | Failure | Evidence | Confirmed root cause |
|---|---------|----------|----------------------|
| A | **Findings chapter failed entirely** — one line: "Manual review required (findings extraction failed)". ~25 flagged gaps → **0 findings**. | Action Items, p.14 | `generate_findings()` makes ONE batched `llm_json()` call over all flagged rows; any exception/bad JSON → bottom `except` → placeholder. `ddiq_report.py:1543-1648` |
| B | **All 6 turbines geocoded to the city of Bremen** (~60 km off). Map shows Bremen-Überseestadt streets; coords 53.094/8.785 = Bremen, not Lamstedt (≈53.62/9.07). | Project Location Map pp.12-13 | LLM returned the whole location paragraph instead of "municipality, state"; that blob is passed verbatim to Nominatim `q=`, `limit=1`, first hit accepted with **no plausibility/bounding-box check**. `geocode_address`, `ddiq_report.py:571` |
| C | **Cadastral parcels wrong/estimated.** Table shows 3 (10/2, 26/3, 44/5); body references 9 different Flurstücke; "44/5" in neither. | Cadastral table p.12 vs body p.6 | Knock-on from B: ALKIS WFS queried with Bremen coords → empty → fallback to `make_parcel_polygon()` "estimated" rectangles. `ddiq_report.py:1481-1486` |
| D | **Four conflicting turbine counts in one report** — 7/3/10 (text), 11 (capacity math), "10 von 11" (title), 6 (table). | pp.2-3, p.12 | WEA count not reconciled across extraction passes. *(exact line to trace)* |
| E | **Action Items near-empty** — 2 items vs ~25 flagged sections. | p.14 | Direct consequence of A. |
| F | **"Address" column unusable** — full multi-sentence paragraph instead of an address. | WEA table p.13 | Same uncleaned LLM field as B. |

### What it did well (so the plan stays honest)

- Strong legal reasoning over the documents it *did* have: correctly parsed the
  Änderungsgenehmigung, deferred Bestandskraft, the OVG ruling cancelling
  L6/L7/L9 and the resulting Rückbauanspruch, noise/shadow limits, inspection
  rhythms.
- **Caught a real cross-document inconsistency** (turbine type E-79 E4 in the
  permit vs E-70 E4 in the maintenance contract).
- Honest about gaps instead of hallucinating — correct instinct for a DD tool.

### The core architectural finding (verified)

- `search_doc_chunks` (`ddiq_report.py:475`) queries
  `FROM ddiq_doc_chunks … WHERE doc_id = ANY(doc_ids)`. **DDiQ retrieves only
  over the uploaded documents.** It never touches the 672 GB legal corpus.
- The ~25 "Keine Angaben im vorliegenden Kontext" lines are therefore *not a
  bug* — the system is working as built; it can only assess the 4 PDFs.
- **Storage reality:** DDiQ uses Postgres (`ddiq_*` tables); the legal corpus
  lives as a **304 GB SQLite file** (`LAI/processed/db_export/app.db`). The live
  host Postgres `lai_db` (port 5435) is **empty — zero tables**. The two halves
  are siloed *and* on different storage engines.

---

## 2. Phase 0 — Unblock (prerequisite, days)

Nothing below ships to "compete" while the service is down.

| Task | Status | Why | Reference |
|------|--------|-----|-----------|
| Fix the live `lai-backend` outage | **RESOLVED 2026-05-14** — `lai_postgres_main` + full runtime stack came up; `lai-backend` and `serve_rag` healthy | Container had been up but crashed at startup — couldn't resolve `lai_postgres_main`. | `AUDIT.md` C3 (with status note); `RE_VERIFICATION.md` B1 |
| Decide one deployment model (all-Docker vs host-process) and make it authoritative | open | The Docker/host split is what caused the outage and remains a recurrence risk. | `AUDIT.md` H2 |
| Auth + tenant isolation on the DDiQ engine | open | No auth, no `user_id` columns — every user sees every report. GDPR blocker; cannot onboard a second customer without this. | `AUDIT.md` C1, C2 |

---

## 3. Phase 1 — Foundation + reliability (~3–4 weeks, two parallel tracks)

**Re-sequenced.** The deep research changed Phase 1's shape: it now opens with a
cheap consolidation step, then runs **two independent tracks in parallel** — the
DDiQ reliability fixes *and* the keystone migration. They touch different code
(Track A = `ddiq_report.py` logic; Track B = infra + a package refactor), so two
people/streams can run them concurrently. Starting the keystone here — not in
Phase 2 — is the core of the re-sequencing: it is an L-effort long-lead item and
everything in Phase 2 waits on it.

### Phase 1a — Consolidation (the cheap de-risker, ~1 week, do first)
Architecture research's #2 recommendation: this is cheap and makes every later
step smaller and safer.
- **Delete the ~6k-LOC dead stack** (`api/main.py` + `auth/`, `documents/`,
  `extraction/`, `generation/`, `search/{routes,repository,hybrid_search,…}`,
  `infra/`) — it is imported by nothing and confuses every reader.
- **Extract a shared `lai.common`** — one embedding client, one reranker client,
  one PDF/OCR extractor, one chunker, one LLM client, one JSON-salvage helper.
  Today these are duplicated 2–4× across `serve_rag.py`, `api.py`,
  `ddiq_report.py` (~1,500–2,000 LOC). After this, **every fix below lands once,
  not three times.**
- No behaviour change — pure refactor. Needs no Postgres, so it can even overlap
  Phase 0.

### Phase 1b — Track A: DDiQ reliability (the smoke-test failures + deep-research findings)
Goal: the same 4 documents produce a **complete, internally consistent** report.
- **A1 · Findings extraction fault-tolerant** — replace the single batched
  `llm_json()` with per-flagged-row iteration; cheap retry; partial success
  counts (6/8 → 6 findings, not 0). *(`TODO.md` "Per-finding generation")*
- **A2 · Geocoding** — a location-normalization pass returns *structured* fields
  (`gemeinde`/`gemarkung`/`landkreis`/`bundesland`), never a paragraph; a
  **plausibility gate** rejects Nominatim hits outside the named Landkreis bbox;
  low-confidence → mark "unverified" instead of plotting the wrong city.
- **A3 · ALKIS parcels** — run only on a validated location; bounded retry on
  HTTP 530; **fix the `_parse_alkis_feature` inverted control flow** (`:705,712`
  — `break` is in the `except`, so flur/area are essentially never read
  correctly — Critical); stop labelling synthetic polygons as `"ALKIS WFS"`.
- **A4 · Deterministic reconciliation stage** — port the `lai/analyzer/
  reconciler.py` philosophy ("the LLM never does the arithmetic"). Replace
  `parse_wea_count` (`:838`, grabs the first integer) with a real multi-group
  parser; force the four turbine-count derivations into one reconciled value;
  surface contradictions as a finding, not four printed numbers. Fixes failure D
  *and its whole class*.
- **A5 · `llm_json` hardening** — strip `<think>` reasoning traces (the 27B runs
  in thinking-mode and DDiQ never strips them — a prime cause of malformed
  JSON); brace-balanced JSON salvage; **catch the double-failure** so it returns
  a typed empty instead of crashing the phase; per-pass typed fallbacks. This is
  what moves the "~22% reports lose a chapter" rate down (corrected from the
  earlier "1-in-4" figure — see `RE_VERIFICATION.md` B5; recount uses 8 SPOF
  passes, not 10).
- **A6 · Cache TTL + invalidation** — `ddiq_geocode_cache` / `ddiq_parcel_cache`
  currently poison permanently; add `cached_at` TTL + a cache-bust on regenerate.
- **A7 · Transactional soundness** — make `request_fingerprint` index `UNIQUE`
  and close the TOCTOU; `ON CONFLICT` on the aux-table writes; wrap the sync path
  so a mid-pipeline crash marks `failed`, not default-`done`.
- **A8 · WEA spec extraction** — dedicated specs-only prompt / Docling table mode
  for datasheets. *(confirm behaviour on more samples before sizing)*
- **(A-disclaimer) · Strip in-analysis hedge language** — rides along here: the
  new `lai.common` JSON/output layer is where the cleanup pass lives (§6 item A).

**Track A exit:** re-run the Lamstedt smoke test → non-empty Findings chapter,
turbines on the correct location (or flagged unverified), one consistent turbine
count, parcels real-ALKIS or clearly labelled estimated, no reflexive "consult a
lawyer" filler.

### Phase 1b — Track B: The keystone (corpus → pgvector + `lai.retrieval`)
Goal: DDiQ can query the legal corpus with a plain SQL join. **This is the
long-lead item — it starts now, in parallel with Track A, not in Phase 2.**
- **B1 · Step 6 status — RESOLVED (incomplete).** Confirmed in
  `RE_VERIFICATION.md` §C: 9.46M of 50M child chunks embedded; ~40.5M with
  `embedding IS NULL`. ~81% pending. Decision: finish-before-migrate or
  migrate-9.46M-now-and-stream-forward.
- **B2 · Migrate the corpus** from `pipeline_local.db` (350 GB SQLite) into
  Postgres pgvector as `halfvec(4096)` + HNSW. One-time, hours-to-days.
- **B3 · Build the shared `lai.retrieval` package** — dense + BM25 + RRF +
  rerank over the unified store; both serve_rag and DDiQ import it. Replaces the
  eval-harness-doing-double-duty (`search/eval.py`).
- *Optional bridge:* if Track A needs corpus grounding before B2 lands, a
  `/retrieve` endpoint on serve_rag is a temporary stand-in, retired after B2.

**Track B exit:** the corpus is queryable from Postgres; `lai.retrieval` is the
single retrieval path; a cold-restart no longer reloads 155 GB into RAM.

**Phase 1 depends on Phase 0** (Postgres must be alive for Track B; auth before
anything ships). Phase 1a can overlap Phase 0.

---

## 4. Phase 2 — Reach beyond the data room (blended, ~3–6 weeks)

This is the architecture change that makes LAI "answer all questions". The
unifying move: **replace the `rag_context(doc_ids, question)` one-liner with a
context-assembler / retrieval router** in front of every extraction pass. Per
question it decides which sources to pull and assembles a grounded context
*with provenance*.

### 2A. Statutory & case-law grounding (from the legal corpus)
- For each section, after extracting what the uploaded docs say, run a *second*
  retrieval against the legal corpus for the relevant statute text + leading
  cases. The report can then state, for every gap: *"the law requires X under
  §Y; absent documentation the consequence is Z; recommended action: …"* — the
  difference between a checklist and a lawyer-grade memo.

### 2.0 — KEYSTONE: corpus migration + `lai.retrieval` package (effort L)
**Executed in Phase 1b Track B — see §3 and §8.** Documented here because it is
what gates all of Phase 2. **Resolved by research — see `DEEP_RESEARCH.md` Parts A & B.**
- **Q3 (corpus home) — RESOLVED.** The live corpus is `LAI/processed/pipeline_local.db`
  (350 GB SQLite, **9.46M embeddings at 4096-dim**). `app.db` is a stale April
  snapshot, not authoritative. *Open:* confirm whether Step 6 embedding is
  complete — only 9.46M of 50M child chunks are embedded.
- **Q1 (corpus access) — RESOLVED.** Target: **migrate the corpus into Postgres
  pgvector as `halfvec(4096)` + HNSW**, behind one shared `lai.retrieval`
  package both serve_rag and DDiQ import. (`halfvec` supports HNSW to 4096 dims;
  `vector` does not — which is why the README fell back to exact search.)
  Optional interim bridge: a `/retrieve` endpoint on serve_rag that DDiQ calls
  over HTTP, retired once the migration lands.
- **Why it's the keystone:** this one project simultaneously unblocks 2A (DDiQ
  grounding becomes a plain SQL join → effort M), continuous corpus expansion
  (online upserts), the SQLite-as-prod-corpus problem, and horizontal scaling.
  Four problems collapse into one.
- **Cost / gates:** one-time migration of 9.46M × 4096 vectors + HNSW build is
  hours-to-days of compute, ~80 GB disk. **Blocked on Phase 0** (live Postgres
  is empty) and on confirming Step 6 status.

### 2B. External registry connectors (agentic tool layer)
A tool registry the extraction passes can call; each connector returns
structured data + a provenance record. Prioritised by ROI:

| Connector | Value | Access | Priority |
|-----------|-------|--------|----------|
| **Marktstammdatenregister (MaStR)** | Confirms WEA registration, commissioning dates, capacity — directly fixes the turbine-count problem *and* EEG-status questions | Free public API | **High — do first** |
| **ALKIS WFS** | Real cadastral parcels (already partially wired) | Public WFS, already integrated | High — promote from fallback to primary |
| **Handelsregister / Unternehmensregister** | Project-company verification (the report flagged a missing HRB number) | Public, scrape/API | Medium |
| **EEG award data / Bundesnetzagentur** | Auction round, strike price, Marktwertkorrektur | Public | Medium |
| **Grundbuch** | Title/encumbrances | **Not openly API-accessible** — needs authorised access | Keep as a "request this document" action item, not auto-fetch |

**Note on a hard constraint:** even with all of the above, a DD tool cannot
*invent* lease contracts, financing term sheets, insurance certificates, or
PPAs — those genuinely only exist in the data room. The realistic target is:
fetch what is *publicly verifiable*, ground every *required-but-missing* item in
the statute that requires it, and turn each gap into a precise, cited action
item. That is what "answer all questions" can honestly mean.

### 2C. Provenance & guardrails
- Every fact in the report carries a source tag: `uploaded-doc` /
  `legal-corpus` / `external-registry:<name>` / `estimated`. Lawyers must be
  able to see *where* each line came from. This is also a competitive
  differentiator and a hallucination guard.

---

## 5. Phase 3 — Feedback loop ("learn", cross-cutting, ~2–4 weeks, can overlap)

No GPU retraining. Architecture:

1. **Editable, addressable outputs.** Every report field/finding gets a stable
   ID; the UI lets a lawyer edit/rate it. Each edit is captured as a
   `correction` record: `(original, corrected, reason, section, doc_context)`.
2. **Correction memory.** Store corrections in pgvector. When a new report runs
   an extraction pass, retrieve the most similar past corrections and inject
   them as few-shot guidance ("on similar documents, lawyers corrected X→Y
   because Z"). The model improves immediately, no retraining.
3. **Eval harness.** Aggregated corrections double as a growing regression/eval
   set — LAI currently has **zero automated tests**; this is how that gap gets
   closed cheaply.
4. **Later:** once corrections accumulate and the synthetic-data
   fabrication problem is solved, the same corpus becomes clean fine-tuning
   data — but that is explicitly out of scope for now.

---

## 6. Product positioning — "replace the lawyer" and removing the disclaimer

**Requirement (from the team):** LAI should stop telling users to contact a
lawyer — it is built to *replace* the lawyer, and the output should read as a
decisive professional, not a hedging assistant.

### Where the "contact a lawyer" language lives (verified)

| Source | Status | What it is |
|--------|--------|-----------|
| `LAI-UI/.../ReportDownloadPanel.tsx:833` & `:2103` | **Live** | DDiQ report footer: *"does not substitute legal review"* / *"…formal legal review"* — the disclaimer on the last page of the smoke test. |
| Base model (Qwen3.6-27B) default behaviour | **Live** | The served model emits hedge phrases ("einen Fachanwalt konsultieren", "ersetzt keine Rechtsberatung") on its own. **No live post-processing strips it** — the old `MAX_DISCLAIMERS` / `REMOVE_AI_REFERENCES` controls exist only in the dead legacy `inference_engine`. |
| `core/constants.py:326` `REFUSAL_LOW_CONFIDENCE` | **Dead code** | "…konsultieren Sie die Originalquellen oder einen Rechtsanwalt." In the `generation/` package wired to nothing. |
| `pipeline/generate.py:84` `REFUSAL_SYSTEM` | **Shelved** | Teaches synthetic *training data* to say "Ich empfehle, einen Fachanwalt zu konsultieren." Only matters if fine-tuning resumes. |

There is **no single switch** — it is frontend copy + model defaults, plus two
inert copies.

### This is an outcome, not a task — two layers

- **(A) Cosmetic — in-analysis hedge language.** Reflexive "consult an attorney",
  "as an AI…", filler caveats inside the answer. For a professional tool this is
  simply bad output. Removing it is **low-risk, prompt-engineering + an output
  cleanup pass**. Can be done anytime; recommended to fold into Phase 1.
- **(B) Substantive — the formal liability disclaimer + the lawyer-replacement
  claim.** The footer is the company's liability shield, not filler. Removing it
  is a business/legal decision and — critically — is **gated by every other
  phase of this roadmap**. You don't get to drop the safety net by editing a
  string; you earn it by making the product decision-grade.

### What (B) actually requires — the dependency chain

| Gate that must hold before (B) | Why | Roadmap phase |
|--------------------------------|-----|---------------|
| **Reliability** | A tool that geocodes turbines to the wrong city and fails findings extraction cannot carry a lawyer's liability. | Phase 1 |
| **Citation integrity** | The README itself flags **15.8 % fabricated §-citations** in the teacher data. A lawyer-replacement cannot invent statutes. | Phase 2C + an audit gate |
| **Grounding / provenance** | Every conclusion must be source-linked and independently verifiable. | Phase 2C |
| **Coverage** | "Keine Angaben im vorliegenden Kontext" ×25 is not replacing a lawyer. | Phase 2 |
| **Demonstrable improvement** | A replacement must measurably get better over time; corrections captured and shown. | Phase 3 |
| **Regulatory clearance** | German RDG — whether LAI may be positioned as a legal-services provider at all. | Counsel — §7 Q5 |

### Recommendation

1. **Fold (A) into Phase 1.** When we revise the system prompts and add the
   output-cleanup / validation layer anyway, strip reflexive hedging at the same
   time. Marginal extra cost; immediate effect on how LAI *feels*.
2. **Revive a proper response-validation layer.** The dead legacy engine's
   `MAX_DISCLAIMERS` / `REMOVE_AI_REFERENCES` was the *right idea in the wrong
   place*. A live post-generation pass is where (A) belongs — and it is the
   same place citation-integrity enforcement should live.
3. **Treat (B) as the roadmap's definition of success, not a step.** LAI has
   "earned" lawyer-replacement positioning when the Phase 1–3 exit criteria are
   met *and* counsel has cleared RDG. Removing the footer is then a one-line
   change — the last 1 %, not the first.

### Risk note (factual — not legal advice)

Two facts to weigh before removing (B): (i) Germany's **Rechtsdienstleistungs-
gesetz (RDG)** restricts who may provide *Rechtsdienstleistung*; a product
positioned as *replacing* a lawyer sits closer to that line than one positioned
as *assisting* one — a question for counsel, asked *before* not after. (ii) The
smoke test proved the output can be materially wrong; "we removed the 'not legal
advice' disclaimer" + "the tool produced a wrong report a client relied on" is
exactly the liability scenario the disclaimer covers. Sequencing (B) *after*
Phases 1–3 is what makes dropping it defensible.

---

## 7. Open decisions needed before building Phase 2

| # | Question | Status / why it blocks |
|---|----------|------------------------|
| ~~Q1~~ | ~~Corpus access~~ | **RESOLVED** — migrate corpus to pgvector `halfvec(4096)`+HNSW behind a shared `lai.retrieval` package; optional serve_rag `/retrieve` bridge. See §2.0 + `DEEP_RESEARCH.md` Part B. |
| ~~Q3~~ | ~~Corpus canonical home~~ | **RESOLVED** — `pipeline_local.db` (350 GB, 9.46M embeddings, 4096-dim); `app.db` is a stale snapshot. See §2.0 + `DEEP_RESEARCH.md` Part A. *Sub-question remains:* is Step 6 embedding complete (9.46M of 50M chunks embedded)? |
| Q2 | Is there budget/authorisation for paid/authorised data sources (Grundbuch access, credit bureaus), or public-only? | Sets the realistic ceiling on "answer all questions". |
| Q4 | Deployment target for "competing" — on-prem GPU box only, or cloud? | Affects Phase 0 deployment-model decision. |
| Q5 | **RDG / regulatory:** may LAI be positioned and marketed as a legal-services provider / lawyer-replacement, and how does removing the liability disclaimer change the company's exposure? | **Counsel decision. Gates §6 item (B).** |
| ~~Q6~~ | ~~Step 6 status~~ | **RESOLVED — incomplete.** 9.46M of 50M embedded; ~40.5M chunks with `embedding IS NULL`. Step 6 SQL targets `WHERE embedding IS NULL` (`cli.py:917-933`) — confirms it's a paused/in-progress job, not a filter-by-design. See `RE_VERIFICATION.md` §C. |

---

## 8. Sequencing summary (re-sequenced around the keystone)

### Dependency graph

```
Phase 0 ─────────────┬──────────────────────────────────────────────► gates everything
 (fix outage,        │
  deployment model,  ├─► Phase 1a (consolidation) ──► makes 1b-A, 1b-B, Phase 2 smaller
  populate Postgres, │        (can overlap Phase 0)
  auth)              │
                     ├─► Phase 1b Track A (DDiQ reliability) ──► smoke test passes
                     │        (needs 1a; independent of Track B)
                     │
                     └─► Phase 1b Track B = THE KEYSTONE ──────► unblocks ALL of Phase 2
                              (corpus → pgvector + lai.retrieval)
                                          │
                                          ▼
                              Phase 2  (grounding 2A, connectors 2B, provenance 2C)
                                          │
Phase 3 (feedback) ── starts as early as 1b, overlaps 2 ─────────────┘
                                          │
                                          ▼
                              (B) drop the liability disclaimer  ◄── + counsel (Q5)
```

### Timeline

```
Phase 0   (days)     Unblock: fix outage, pick deployment model, populate
                     Postgres, add auth + tenant isolation.

Phase 1   (3-4 wks)  1a  Consolidation (~1 wk): delete dead stack, extract
                         lai.common. Can overlap Phase 0.
                     ── then two parallel tracks ──
                     1b-A  DDiQ reliability: findings, geocoding, ALKIS
                           control-flow fix, deterministic reconciler,
                           llm_json hardening, cache TTL, txn soundness,
                           WEA specs, + strip hedge language (item A).
                     1b-B  THE KEYSTONE: corpus → pgvector halfvec+HNSW,
                           shared lai.retrieval package. Long-lead — starts
                           NOW, in parallel, not in Phase 2.

Phase 2   (3-5 wks)  Gated on 1b-B. 2A statutory grounding (now M, not L —
                     a SQL join once the keystone lands), 2B registry
                     connectors (preceded by a lai/connectors/ refactor),
                     2C provenance + citation-integrity enforcement.

Phase 3   (2-4 wks)  Feedback loop: capture is S (lai_feedback table already
                     exists) — start during Phase 1b; correction memory +
                     eval harness overlap Phase 2.
─────────────────────────────────────────────────────────────────────────────
(B)  Remove the formal liability disclaimer + adopt lawyer-replacement
     positioning  →  only once Phase 1-3 exit criteria are met AND counsel
     has cleared §7 Q5. The roadmap's definition of "done", not a task.
```

### What changed in the re-sequencing — and why

1. **The keystone moved out of Phase 2 and into Phase 1b (Track B).** It is an
   L-effort, long-lead migration that *every* Phase 2 feature waits on. Starting
   it in Phase 2 would have serialised the whole program; starting it now, in
   parallel with the reliability fixes, takes it off the critical path.
2. **A new Phase 1a (consolidation) was inserted first.** It is cheap, needs no
   Postgres, and collapses the ~1,500–2,000 LOC of triplicated helpers — so
   every reliability fix in Track A lands once instead of three times, and the
   keystone's `lai.retrieval` package has a clean `lai.common` to sit beside.
3. **Phase 1 is now two parallel tracks, not a linear list.** Track A
   (`ddiq_report.py` logic) and Track B (infra + package refactor) touch
   disjoint code and can run concurrently — Phase 1 stays ~3–4 weeks despite
   absorbing the keystone and the deep-research scope additions.
4. **Phase 2 dropped from "3–6 wks" to "3–5 wks" and 2A from L to M** — because
   the keystone (the L part) is no longer inside it.
5. **Feedback capture pulled earlier** — the `lai_feedback` table already
   exists; capturing corrections from day one means Phase 3's correction-memory
   and the eval harness have real data to work with.

None of it is a rewrite. The existing extraction passes and legal reasoning are
sound. The critical path is now: **Phase 0 → keystone (1b-B) → Phase 2** — with
reliability (1b-A), consolidation (1a), and feedback (Phase 3) running alongside
rather than in series.
