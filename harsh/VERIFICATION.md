# LAI — Verification Pass: Market Research + Smoke-Test Critique

**Date:** 2026-05-14
**Task:** Re-check everything against source — the actual PDF, the actual code,
and (via web search) the competitor/regulatory claims in the "LAI Market
Research" document and the smoke-test critique. Brutally honest: that includes
flagging where **the documents you were given are themselves wrong**, and
confirming where my own prior reports hold up.

**Verdict in one line:** both documents are *directionally correct* (LAI is not
client-ready; the competitors are real; the compliance gaps are genuine) — but
each contains **specific factual errors** that would damage credibility if taken
into a pitch or planning meeting verbatim.

---

## PART 1 — The smoke-test critique ("Ouch…"), point by point

Re-verified by re-reading `LAI/docs/smoke_test_report.pdf` (pages 2, 12, 13, 14)
and the code.

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | AI explicitly admits failure — "Manual review required (findings extraction failed)" | **CONFIRMED** | Visible in the Action Items table; root cause traced to `generate_findings()` single batched `llm_json()` call, `ddiq_report.py:1543-1648`. |
| 2 | "CSV Bleed" — tables render as raw quoted CSV (`"Category ","Status / Details "`) | **NOT CONFIRMED — does not match this file** | Re-read pages 2, 12, 13: **every table renders as a clean, properly-aligned table.** No raw quotes, no commas dumped on the page. The PDF is generated client-side via `window.print()` from React HTML (`ReportDownloadPanel.tsx:1019`), which emits proper `<table>` markup. The quote-comma text the critique quotes is almost certainly the artifact of **select-and-copy out of a PDF table** (PDF copy-paste mangles tables into CSV-ish text) — not the PDF generator. **Either the critique writer mistook clipboard output for rendered output, or reviewed a different/older file.** If a CSV-bleed version exists, we need that exact file. |
| 3 | "Denglisch" — English headers + German content, mixed mid-sentence | **CONFIRMED** | Category labels are English ("Number of WEA", "Project Status", "Type & Capacity"); content is mostly German; some content is English mid-sentence ("Formal permit status: An Änderungsgenehmigung…"). Visible pp.2-3. |
| 4 | "Severe hallucinations & repetition" | **PARTIALLY CONFIRMED — one sub-claim misdiagnosed** | **Repetition: CONFIRMED** — the identical owner blob repeats 6× (p.13 Owner column) and the identical "Bundesland: Niedersachsen…" blob 6× (WEA address table). **"Map prints a raw chaotic list of labels": MISCHARACTERIZED** — the map *does* render as a proper OpenStreetMap tile with WEA pins. "Großmarkthalle / P / Überseestadt / Hinter der Kranbahn" are the **OSM base-map's own labels for the city of Bremen** — part of the rendered map, not LAI dumping a list. The real bug is not a rendering failure; it is that the map shows **Bremen instead of Lamstedt** (the geocoding failure). The critique misdiagnosed the symptom. |
| 5 | "Defensive I-don't-know responses" — "Die vorliegenden Kontextausschnitte enthalten keine Angaben…" | **CONFIRMED** | Appears across ~20+ sections. The UX fix (red-flag icon / "Data Not Provided" instead of a paragraph) is sound. |

**Critique scorecard: 3 confirmed, 1 not confirmed (#2), 1 partially confirmed
with a misdiagnosed sub-point (#4).** The critique's *conclusion* — "this looks
like a backend debugging log, not a client deliverable" — is **correct**. But it
is built partly on a claim that doesn't match the file and a misdiagnosis, which
is exactly the kind of thing that, unflagged, becomes a hallucination passed
downstream.

---

## PART 2 — The "LAI Market Research" document

### 2.1 Competitor facts — verified via official sources

| Competitor | Verdict | Notes |
|-----------|---------|-------|
| **Luminance** (UK) | **ACCURATE** | Contract review, multi-agent architecture, **works inside Microsoft Word**, raised **$75M Series C in early 2025**, 1,000+ enterprises across 70+ countries. |
| **Harvey** (US) | **ACCURATE** | US-based, strong Germany presence. **Deutsche Telekom adopted Harvey in early 2024** — the entire Law & Integrity team uses it. "Harvey Agents" are real (end-to-end workflow agents). **Note for LAI:** data encrypted and **stored in Germany** was Deutsche Telekom's non-negotiable condition — directly relevant to LAI's local-model pitch. |
| **Bryter** (Germany) | **ACCURATE** | Frankfurt HQ (also NY/London), no-code legal/compliance automation, ~201 employees, ~$66M raised, launched "Hybrid Agents", "Cool Vendor 2025". Clients incl. McDonald's, ING, Linklaters. |
| **Leverton** (Berlin) | **WRONG / OUTDATED** | **Leverton was acquired by MRI Software in July 2019** — over 6 years ago. It is **not an independent competitor**; it is now "MRI Contract Intelligence", and it is focused on **real-estate / lease abstraction**, not general legal DD. Listing it as a current independent Berlin player "very similar to your DDiQ" is factually stale. (Ironically it *is* closest to LAI's cadastral/parcel extraction work — but the framing in the doc is wrong.) |
| **Legartis** (Switzerland) | **ACCURATE — but the doc under-weights it** | Swiss, since 2017. Critically: **hosts all data AND all LLMs locally in Switzerland/Europe, GDPR-compliant, ISO 27001-certified**, handles German/Swiss/Austrian + English. **This is a direct competitor to LAI's headline differentiator.** LAI's "local models = unique" pitch is weaker than the doc assumes — Legartis already ships the sovereign-AI story. |
| **Spellbook** (Canada) | **ACCURATE** | Native Microsoft Word add-in for drafting/redlining/review, GPT-4o-based, 4,000+ legal teams in 80+ countries. |

**Competitor scorecard: 5 of 6 accurate. Leverton is wrong (acquired 2019,
pivoted to real estate). Legartis is under-weighted — it is a sharper threat to
LAI's data-sovereignty pitch than the doc conveys.**

### 2.2 Compliance claims

- **GDPR / BDSG** — the *technical basis* (no real auth, data globally visible)
  is **confirmed** by our own audit (`AUDIT.md` C1/C2; the project's own
  `TODO.md` admits it). Whether that literally "does not abide by the law" is a
  legal conclusion for counsel — but the underlying fact is real and serious.
  Directionally correct.
- **EU AI Act** — timeline **verified against the European Commission**:
  **2 August 2026** is when the bulk of obligations apply (high-risk Annex III
  systems, Article 50 transparency); GPAI obligations have already applied since
  **August 2025**. So "coming into full effect in 2026" is roughly right.
  **Nuance the doc misses:** whether a commercial legal-DD assistant is an Annex
  III "high-risk" system is genuinely debatable (Annex III's justice category
  concerns AI used *by judicial authorities*, not commercial tools for law
  firms). LAI more likely faces **Article 50 transparency** obligations +
  GPAI-downstream considerations. Asserting "the Act requires strict audit
  trails [and LAI lacks them, therefore non-compliant]" overstates the
  certainty — **this is a counsel question** (same bucket as the RDG question in
  `DDIQ_ROADMAP.md` §7 Q5). The doc's *action items* (audit logging, retention
  policy, soft-delete) are good practice regardless of how that question lands.

### 2.3 The "fix" recommendations — two are wrong as stated

- **"Drop max_tokens 4096→1024, a one-line config change at line 504"** —
  **IMPRECISE, AND THE ADVICE IS PARTLY WRONG.** Verified in code: line 504 is
  `llm_call` with default `max_tokens=2048` (not 4096); the `4096` is at lines
  **517 and 521** inside `llm_json` (two lines, not one). More importantly — our
  deep research found the real cause is **thinking-mode reasoning traces**
  consuming the budget, not the budget being too large. Naively cutting to 1024
  risks **truncating legitimate long JSON** (39-question section arrays,
  all-turbines lists) and making extraction *worse*. The correct fix is per-row
  iteration + `<think>`-trace stripping + disabling thinking-mode for structured
  calls (`DDIQ_ROADMAP.md` Phase 1b Track A, items A1/A5). The "one-line fix /
  90→20 min" is a tempting oversimplification.
- **"Fuzzy Citation Verification — your regex rules are too strict, refusing
  verifiable answers"** — **PREMISE IS WRONG.** Verified: the citation verifier
  (`generation/citation_verifier.py`) lives in the **dead code stack** — it is
  **not imported by the live `serve_rag.py` or `ddiq_report.py`**. There is **no
  live citation verification at all**; the live paths just take whatever
  `citations` list the LLM returns (`serve_rag.py:726`) or instruct the prompt
  "never fabricate citations" (`ddiq_report.py:890`). So it is not "strict regex
  causing refusals" — it is *nothing verifying citations*. Fuzzy matching is
  still a good idea, but the diagnosis does not match the code.
- **SSE streaming** — **CONFIRMED accurate.** No `StreamingResponse` /
  `text/event-stream` / `EventSource` in any live path; `sse-starlette` is a
  declared-but-unused dependency. The recommendation is valid.
- **Prometheus / Grafana observability** — **CONFIRMED accurate.**
  `Docker/monitoring/` is configured but not deployed (matches `AUDIT.md`).
- **Semantic "Find Similar Contracts"** — feasible; 4096-dim embeddings exist.
  Fair recommendation.
- **Word add-in, multilingual, workflow automation, workspaces/permissioning** —
  all are real gaps vs. the verified competitor set; fair.

---

## PART 3 — Re-check of our own prior reports (do they still hold?)

| Prior claim | Status |
|-------------|--------|
| 6 smoke-test failures (A findings, B geocoding-to-Bremen, C parcels, D counts, E action items, F address column) | **All still hold** — re-confirmed against the PDF this pass. |
| Geocoding placed turbines in Bremen | **Confirmed** — map tile on p.13 is unambiguously Bremen-Überseestadt. |
| `app.db` vs `pipeline_local.db` corpus location | **Holds** — corrected in `DEEP_RESEARCH.md`; the live corpus is `pipeline_local.db`. |
| "No live citation verification" | **Newly confirmed this pass** — strengthens, doesn't contradict, prior work. |
| We never claimed "CSV bleed" | **Correct** — we never observed it, so never asserted it. Our restraint there was right. |

Our prior reports do not contain a claim contradicted by this verification pass.
The one imprecision that *was* floating around — "max_tokens at line 504" — came
from the **pre-existing `LAI_*.md` docs**, and we had already flagged it as
imprecise in `AUDIT.md`. It resurfaced in the market-research doc; corrected
again above.

---

## Bottom line

Both documents are **useful and directionally right** — do not dismiss them. But
before either is used in a pitch, a board update, or a planning session with
Arne and Kristian, correct these five specific errors, or they will undermine
credibility exactly the way the smoke test does:

1. **CSV bleed** — not present in the actual file; likely a copy-paste artifact.
2. **"Map prints a list of labels"** — misdiagnosed; the map renders, it is just
   the wrong city (geocoding bug).
3. **Leverton** — acquired by MRI Software in 2019; not an independent
   competitor; now real-estate-focused.
4. **"max_tokens one-line fix at line 504"** — wrong line, wrong remedy; could
   make extraction worse.
5. **"Citation regex too strict"** — there is no live citation verification at
   all; the premise is wrong.

And one strategic correction: **Legartis already ships LAI's headline
differentiator** (local models, data in-country, GDPR/ISO-certified). "Local =
unique" is not a moat — execution quality and the German-wind-energy domain
depth are the real defensible ground.

---

## Sources (web-verified)

- Luminance — https://www.luminance.com/ ; https://www.luminance.com/press/luminance-launches-new-legal-ai-with-institutional-memory-addressing-enterprise-amnesia-and-giving-legal-teams-30-of-their-time-back/
- Harvey / Deutsche Telekom — https://www.harvey.ai/customers/deutsche-telekom ; https://www.harvey.ai/customers/deutsche-telekom-gleiss-lutz-collaboration ; https://www.harvey.ai/blog/harvey-strengthens-local-presence-in-germany
- Bryter — https://bryter.com/ ; https://bryter.com/press-releases/bryter-named-a-2025-cool-vendor/
- Leverton / MRI Software acquisition — https://www.mrisoftware.com/news/mri-software-acquires-ai-real-estate-pioneer-leverton-turn-unstructured-data-business-insights/ ; https://www.crunchbase.com/acquisition/mri-software-acquires-leverton--deaeefcc
- Legartis — https://www.legartis.ai/ ; https://www.legartis.ai/ai-contract-review
- Spellbook — https://www.spellbook.legal/
- EU AI Act timeline — https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai ; https://artificialintelligenceact.eu/implementation-timeline/
