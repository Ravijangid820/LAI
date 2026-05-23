# DDiQ Report Quality — Consolidated Analysis (3 Reports)

**Analyst:** LAI review · **Date:** 2026-05-22
**System under review:** LAI DDiQ v1 (auto-generated wind-energy due-diligence reports)

This document merges the three individual report reviews into a single picture:
overall scoring, what the engine does well, where it fails, **which specific
problems are holding the scores down**, and a prioritised plan to improve.

---

## 1. Scope — what was reviewed

| # | Report | Source document | Document type | Overall |
|---|--------|-----------------|---------------|---------|
| 1 | DDiQ Report – Wind Energy Project | `01_ENERCON_Betriebsanleitung_E82_196pg.pdf` | Manufacturer operating manual | **~6.5/10** |
| 2 | DDiQ Report – Windpark Lamstedt | `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` | Cable easement (Gestattungsvertrag) | **~7.5/10** |
| 3 | DDiQ Report – Windpark Lamstedt2 | `05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt_10pg.pdf` | Ground lease (Nutzungsvertrag) | **~8/10** |

Each report runs the same ~40-category DDiQ checklist across four sections:
Project Overview, Land Security & Ownership, Permits & Regulatory Conditions,
Economics & Operations — plus map/asset visualisations and an Action-Items table.

---

## 2. Headline finding

**The engine is fundamentally trustworthy: it is grounded, legally literate, and
does not invent due-diligence findings.** Where it has no data it says so, and it
explains *why each gap matters* using the correct German legal frameworks (§35
BauGB, §§4/6 BImSchG, §§44/45 BNatSchG, §9 EEG, 22./32. BImSchV, 10H/Art. 82 BayBO,
§§1090/1091 BGB, §550 BGB, DIBt, UVPG).

Report quality tracks input quality almost linearly, and the worst bugs disappear
in sequence as the source documents get richer:

| | ENERCON (manual) | Lamstedt 06 (easement) | Lamstedt 05 (lease) |
|---|---|---|---|
| Overall | ~6.5/10 | ~7.5/10 | **~8/10** |
| Phantom map pin | **Yes (0,0 Null Island)** | No | No |
| Fabricated parcel IDs | n/a | **Yes (Flst 7414 / 4572)** | No |
| Company identity | n/a | **Wrong ("not named")** | **Correct** |
| Dominant failure mode | Wrong document type | Cadastral-table extraction | Under-surfacing present numbers |

The trajectory is encouraging — but the *persistent* weaknesses (Section 5) are
what currently cap the score around 8/10 even on a clean document.

---

## 3. Strengths (consistent across all three)

1. **No hallucinated findings.** Every "not found / Material Gap" determination
   reviewed was correct for the document type. The system never fabricated a
   permit, lease, or financing fact.

2. **Strong legal reasoning and gap framing.** Each gap is tied to the relevant
   statute and explains the DD consequence (e.g. "no §35 Abs. 5 BauGB
   Rückbaubürgschaft → reinstatement cost not secured"). This is genuinely useful
   reviewer guidance.

3. **High-fidelity contract-term extraction (on real contracts).** Examples that
   matched the source precisely:
   - Lease term 30 years + 2×5-year options, max 40 (Nutzungsvertrag §6).
   - Rent 6 → 7 % of feed-in revenues after the 13th year (§3).
   - Rückbau bürgschaft cascade €7.500 → +€2.500/yr after 4-yr Ruhezeit → cap
     €25.000/WEA, Treuhänderin Samtgemeinde Börde Lamstedt, foundation to 1 m
     (§11.3).
   - Beschränkt persönliche Dienstbarkeit at ready rank, Rangrücktritt,
     Rücktrittsrecht (§7); Sicherungsübereignung + bank step-in (§9).
   - Gestattungsvertrag Rückbau clause 8; Abteilung-II Dienstbarkeit + Vormerkung.

4. **Sound DD judgments.** E.g. flagging that a €25.000/WEA reinstatement bond may
   undershoot real demolition cost; that the §14 auflösende Bedingung (valid,
   uncontested permit) is a red flag.

5. **Correct document-type discipline.** On the ENERCON manual it correctly
   concluded the document is not DD material (even if it never said so at the top —
   see 5.6).

6. **Improving map/asset hygiene.** The two later reports dropped the phantom pin
   and the fabricated-parcel rows, rendering empty maps honestly instead.

7. **Self-correction over time.** The company-identity error in report 2 was not
   repeated in report 3, which correctly identified the operator's legal form.

---

## 4. Weaknesses (per report)

### Report 1 — ENERCON manual (~6.5/10)
- **Phantom map pin** at 0.0000 / 0.0000 (Null Island) with a marker and
  "Negotiation (1)" legend — contradicts the report's own "no location" text.
- **Turbine model treated as a project asset** — "ENERCON E-82 E2" listed as a WEA
  with fabricated status "Partial / Negotiation".
- **Capacity under-claim** — nameplate 2300/2000 kW is on the cover but reported as
  unverifiable.
- **No top-level "wrong document type" verdict** — reader must infer it from ~40
  repetitive "not found" rows.
- Practical usefulness low (~95 % "not found") because the input was a poor fit.

### Report 2 — Gestattungsvertrag (~7.5/10)
- **Factual error + self-contradiction:** "Project Company: not named" vs the
  "Investors" row that correctly names "Windpark Lamstedt GbR".
- **Garbled / hallucinated parcel IDs:** "Warstade Flur 17 Flst. 7414", "Gemarkung
  Hesdel Flst. 4572", "Heese" — none exist (real Gemarkung is Heeßel); only 519/365
  is genuine.
- **Cadastral-parcel table column misalignment** — Zeichn.-Nr. values (24, 25) read
  as Flur ("Heeßel Flur 24" does not exist; real Flure are 1/2/4/5); all rows show
  placeholder "0 ha".
- **Site Control contradiction** — claims Flurstücksnummern are "redacted/not
  listed" while the contract schedule, the route maps, and the report's own parcel
  table all list them.
- **Missed concrete data** — route length 818 m and compensation €2/m = €3,272
  (clause 5) appear nowhere.

### Report 3 — Nutzungsvertrag (~8/10)
- **Wrong "can't verify Gemarkung" claim** — §1 explicitly states "Gemarkung
  Lamstedt".
- **Unsurfaced present data** — leased area 3,41285 ha (= 34.128,5 m²), the 110-kV
  feed-in line (§3), the 250 m building-free radius (§4.3).
- **Missed appendix content** — Wegefläche schedule and Ausgleichsmaßnahmen /
  Kompensation plans (Stand 27.02.2003) ignored when declaring environmental
  content absent.
- **Clause-citation slips** — §550 BGB cited where the document says §127 BGB;
  rank-Rücktrittsrecht attributed to "§2 Abs. 2" instead of §7 Abs. 2; financing
  consent to "§4" instead of §13.7 / §9.
- **Step-in tension** — Securities row says step-in rights absent, but §9 is a
  step-in mechanism (acknowledged in the Financing row).

---

## 5. What is actually holding the score down (root-cause themes)

These are the cross-cutting problems. Fixing them is what moves the ceiling above
8/10.

### 5.1 Structured-data / table extraction (highest impact)
German cadastral tables (Gemarkung / Flur / Flst / Zeichn.-Nr. / Länge) get
misaligned: the engine reads neighbouring columns (Zeichn.-Nr., Länge) into the
Flur/Flst fields, and a few parcel IDs come out garbled or invented. This produced
both the fabricated parcels and the "0 ha" placeholders in report 2.
**Impact:** directly corrupts the Land Security section — the highest-value part of
land DD. **Severity: High.**

### 5.2 Under-surfacing data that IS present
The engine is good at flagging absent data but inconsistent at *surfacing present
numeric facts*: leased area (3,41285 ha), rent percentages, route length (818 m),
compensation (€3,272), line voltage (110 kV), bond amounts. It often narrates "X is
missing" while a concrete figure sits in the same clause.
**Impact:** reports read as more empty than the documents actually are; reviewers
lose quick-reference numbers. **Severity: High** (this is the main cap on report 3).

### 5.3 Map / asset rendering on sparse data
The phantom 0,0 pin and the fabricated "Partial / Negotiation" WEA row (report 1)
are rendering bugs: an absent location is coerced into a coordinate and a status.
Later reports show the fix path exists, but report 1 proves it can still leak into
the PDF export.
**Impact:** undermines client trust instantly (a map pin in the Gulf of Guinea).
**Severity: High when it occurs** (intermittent).

### 5.4 Internal consistency between rows
The same fact is judged differently in different rows: company "not named" vs named
(report 2); step-in "absent" vs acknowledged (report 3); Site Control "no parcels"
vs a populated parcel table (report 2).
**Impact:** erodes credibility; a careful reader spots the contradiction.
**Severity: Medium.**

### 5.5 Legal clause cross-reference precision
Statute and clause numbers are sometimes wrong even when the legal substance is
right (§550 vs §127 BGB; §2 vs §7; §4 vs §13.7). For a legal deliverable, wrong
citations are disproportionately damaging.
**Impact:** a lawyer chasing the cited clause finds the wrong text.
**Severity: Medium.**

### 5.6 Missing top-level verdict / triage
No report opens with a one-line classification ("this is a manufacturer's manual,
not DD material" / "this is a cable easement covering only the grid connection").
The reader must reconstruct it from 40 rows.
**Impact:** slows the reviewer; hides the single most useful insight.
**Severity: Medium.**

### 5.7 Output hygiene — debug tags & mixed language
Internal artefacts leak into the client PDF: `[Sprache: en; Abschnitt: de]`,
`[Sprache: mixed]`, `[#1]`, `[#2]`, `[#4]`. Language is inconsistent — some rows in
English, most in German, within one report headed "Prepared for: Client".
**Impact:** looks unfinished; trivial to fix; high cosmetic cost.
**Severity: Low technically, High for perceived polish.**

### 5.8 Single-document scope vs project-level DD
Each report analyses one document, so project-level questions (turbine count, total
capacity, full parcel coverage) are structurally unanswerable. This is expected,
but the reports don't make the single-document scope explicit, which can read as a
data gap rather than a scoping choice.
**Impact:** "Material Gap" verdicts that are really "out of scope for this file".
**Severity: Medium** (resolved once multiple docs are ingested per project).

---

## 6. Improvement plan (prioritised)

### P0 — correctness bugs (do first)
1. **Harden cadastral-table parsing.** Parse the German parcel table by explicit
   column mapping (Gemarkung / Flur / Flst / Zeichn.-Nr. / Länge) so Zeichn.-Nr. and
   Länge stop bleeding into Flur/Flst. Validate Flst/Flur tokens against a pattern.
2. **Constrain parcel IDs to the source.** Reject any Flurstück/Gemarkung not
   literally present in the document text (guards against OCR-garbled / invented
   IDs); normalise Gemarkung spelling (Heeßel ≠ Hesdel / Heese).
3. **Kill the phantom map pin in the PDF path.** When no location/coordinate is
   extracted, emit null projectCenter and render no marker / no status — confirm the
   `null projectCenter` fix is actually wired into the PDF export, not just the web
   view.
4. **Suppress fabricated asset rows.** Never emit a WEA row or a "Partial /
   Negotiation" status when no asset or coordinate was identified (don't treat a
   turbine *model name* as a project asset).

### P1 — accuracy & completeness
5. **Add a numeric-fact extraction pass.** Always surface present quantities: area
   (ha/m²), rent %, term/option years, bond amounts, compensation €, line voltage,
   distances. Render them in the relevant rows instead of only flagging absence.
6. **Tighten legal clause cross-referencing.** Cite the § that actually appears in
   the source; separate "the document's clause number" from "the governing
   statute". Add a check that a cited § exists in the source span.
7. **Cross-row consistency check.** Before finalising, reconcile facts that recur
   across rows (named parties, step-in/Eintrittsrecht, parcel presence) so two rows
   can't contradict each other.
8. **Scan appendices/annexes** for compensation (Ausgleich/Kompensation), Wegefläche
   schedules, and plans before declaring environmental/location content absent.

### P2 — usefulness & presentation
9. **Add a top-of-report triage block:** document classification + a 2–3 line
   executive verdict ("cable easement covering only the 20-kV grid connection; does
   not evidence permits, turbine specs, or the WEA land lease").
10. **Strip debug metadata** (`[Sprache: …]`, `[#n]`) from rendered output.
11. **Normalise report language** — pick one output language per report (or per
    client) instead of mixing DE/EN row by row.
12. **Make single-document scope explicit** — label "Material Gap" vs "Out of scope
    for this document" so single-file analyses aren't misread as project-wide gaps.

### P3 — evaluation harness
13. **Build a regression set** from these three documents with known-good answers
    (gold facts: term, rent, bond cascade, parcel list, party names) and assert the
    engine reproduces them — so the cadastral-table and numeric-fact fixes don't
    regress.

---

## 7. One-line takeaway

The DDiQ engine is honest and legally sharp; its score is capped not by
hallucination but by **structured-data extraction (German cadastral tables),
under-surfacing of figures that are present, intermittent map-rendering artefacts,
and unpolished output**. Fixing the P0/P1 items should move clean-document reports
from ~8/10 toward 9+/10.
