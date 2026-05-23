# Analysis of LAI — Two Output Modes Compared

**Analyst:** LAI review · **Date:** 2026-05-22

This review analyses **two different outputs of the LAI system** and gives an
honest, professional assessment of each, plus a head-to-head on the two modes.

| | Output A | Output B |
|---|---|---|
| Mode | **Chat / Q&A section** | **DDiQ Report section** |
| Source document | `05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt` (ground lease) | `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor` (cable easement) |
| File | (pasted German Q&A) | `DDiQ Report.pdf` (15 pp, generated 21:50) |
| Form | Free-text answer with inline citations `[M-1] [C-1] …` | Structured 40-category checklist + maps + action items |

> Note: the two outputs are on **different documents**, so this is a comparison of
> *modes and behaviour*, not a fact-for-fact diff. Each output was verified against
> its own source contract.

---

## PART A — Chat / Q&A output (document 05)

The user asked a deliberately strict question: *analyse only the actual content of
the contract, reproduce only what is expressly in the document, no generic
wind-industry boilerplate.* The answer returned 8 themed sections with citation
markers.

### A.1 Accuracy — verified against the source ✅
Every factual claim checks out against the Nutzungsvertrag:

| Claim in answer | Source clause | Verdict |
|---|---|---|
| Use granted for erecting/operating WEA + transformer + Zuwegung, subject to permits | Präambel + §1 | ✓ |
| 30-year term, from 1 Jan of commissioning year | §6 | ✓ |
| 2×5-year extension options, exercised ≥6 months before end | §6 | ✓ |
| Rent 6,0 % of feed-in revenues + VAT from commissioning ("Einspeisung in die 110-kV-Leitung") | §3 | ✓ |
| Rises to 7,0 % after the 13th operating year | §3 | ✓ |
| Reinstatement: remove WEA + paved access, restore; foundation to 1 m depth | §11 | ✓ |
| Extraordinary termination 12-month notice if WEA can't be operated for economic/public-law reasons | §10.3 | ✓ |
| Transfer to third party without owner consent but with notice; third party must assume §2 liability + removal per §12 | §12.1–12.2 | ✓ |
| Owner pre-emption right on sale of the WEA | §12.3 | ✓ |
| Financing-bank changes pre-approved; term & rent untouched | §13.7 | ✓ |
| Earlier contracts/pre-contracts void on signature | §13.2 | ✓ |
| Written-form requirement; severability with substitute clause | §13.3–13.5 | ✓ |

**Zero factual errors.** Accuracy is essentially perfect.

### A.2 Standout strength — retrieval-contamination control ✅✅
The closing *Hinweis* explicitly **quarantines retrieved-but-irrelevant chunks**:
it identifies that sources `[C-2]/[C-3]` (and partly `[C-1]`) contain *other*
contract versions — a Prowind-Vestas reference deal, "0,9 €/c", "10 years from
09.06.04" — and states these are **not** part of the current Nutzungsvertrag.

This is exactly the discipline you want from a RAG system: it pulled neighbouring
documents, recognised they were a different contract, and refused to let them
contaminate the answer. It also exposes provenance transparently via `[M-1]` (main
document) vs `[C-x]` (context) markers.

### A.3 Weaknesses ⚠️
1. **Completeness / recall.** The answer is precise but selective — it captures
   ~8 themes and **omits several material clauses** that the question's wording
   ("alle tatsächlichen Klauseln … Zahlungen und sonstigen Regelungen") arguably
   called for:
   - §14 **auflösende Bedingung** (contract void unless a valid, uncontested permit
     is obtained) — a genuine DD red flag.
   - §11.3 **Rückbaubürgschaft cascade** (€7.500 → +€2.500/yr after a 4-year
     Ruhezeit → cap €25.000/WEA, Treuhänderin Samtgemeinde Börde Lamstedt).
   - §2 **Haftung** (regulated in a separate 14 March 2007 agreement with a bond).
   - §3 **detailed rent mechanics** (15 % to sites / 85 % to paths, €0,50/m²,
     Mindestpacht €400/ha).
   - §7 Dienstbarkeit / Rangrücktritt, §9 Sicherungsübereignung, §15 Gerichtsstand
     Otterndorf.
2. **Minor provenance slip.** Severability (§13.5) is in the *main* document but is
   tagged `[C-1]` (context) rather than `[M-1]`.

### A.4 Verdict on Chat mode
**~8.5/10.** Excellent accuracy, transparent citations, and best-in-class
contamination handling. Held back only by partial recall — it is a high-precision
*summary*, not an exhaustive clause-by-clause extraction.

---

## PART B — DDiQ Report output (document 06)

This is a regenerated report on the Gestattungsvertrag (cable easement). Compared
to the earlier version of the same report, it shows **clear, measurable progress**
— and a few regressions.

### B.1 Fixed since the previous version ✅✅ (important)
1. **Cadastral Parcels table is now 100 % accurate.** All nine rows match the
   contract's parcel schedule exactly:

   | Report | Source (contract §1 table) |
   |---|---|
   | 62/1 — Heeßel Flur 1 | Heeßel Flur 1, Flst 62/1 ✓ |
   | 78/6 — Heeßel Flur 2 | Heeßel Flur 2, Flst 78/6 ✓ |
   | 114/13 — Heeßel Flur 4 | Heeßel Flur 4, Flst 114/13 ✓ |
   | 115/1 — Heeßel Flur 4 | Heeßel Flur 4, Flst 115/1 ✓ |
   | 112/1 — Heeßel Flur 4 | Heeßel Flur 4, Flst 112/1 ✓ |
   | 16, 5, 2/1, 7 — Warstade Flur 17 | Warstade Flur 17, Flst 16/5/2-1/7 ✓ |

   The previous version garbled this (e.g. "Heeßel Flur 24", "Flur 21" — confusing
   the Zeichn.-Nr. column with Flur). **That bug is gone.**
2. **Fabricated parcel IDs are gone.** The Cable & Access narrative now cites real
   parcels from the route maps (Warstade 74/4, 1, 2/1, 5, 7, 12, 8/1; Heeßel 41/2,
   62/1, 46/2, 54/2, 46/3, 54/3, 63/4, 25/1, 30/1, 33/1, 38/1) — all verifiable on
   the Blatt 19e–28 plans. The old "Flst 7414 / 4572 / Hesdel / Heese"
   hallucinations are gone.
3. **No phantom map pin** — Project Location and Land Security maps render empty,
   honestly.
4. **Accurate date extraction** — signing dates 2007-10-11 (operator) / 2007-10-12
   (owner) and map Stand 2007-08-28 are all correct.
5. **More specific addressee** — "Prepared for: Gemeinde Hemmoor".

### B.2 Persistent / new problems ⚠️
1. **Project Company contradiction — still present.** The row claims the operator
   is an *"unbenannter 'Betreiber'"* (unnamed), yet the Site Control and Investors
   rows correctly state **"Windpark Lamstedt GbR."** The contract names it in full
   (Windpark Lamstedt GbR, Sönke-Nissen-Koog 58, 25821 Reußenköge). This is the
   same self-contradiction as before — **not fixed**.
2. **Reinstatement / Rückbau — regression.** The row now says *"keine Informationen
   hinsichtlich einer Rückbauverpflichtung … keine Rückbaugewährleistung."* This is
   **wrong**: clause 8 of the contract expressly obliges the operator to remove the
   cable, restore the land at its own cost, and delete the Grundbuch entry when the
   line is no longer needed. The *previous* version captured this correctly. The
   report conflates "no §35 BauGB financial bond" (true) with "no removal
   obligation at all" (false).
3. **Location — Gemarkung error.** States *"Gemarkung Hemmoor, Flur 1"*. Hemmoor is
   the municipality, not a Gemarkung; the actual Gemarkung is **Heeßel** — as the
   report's own parcel table correctly shows. Internal inconsistency.
4. **Leaked UUID in output.** The PPA/Off-Take recommendation contains a raw chunk
   ID: *"die vorliegenden Gestattungsverträge `[2de3f023-bef7-4f5b-ab17-e13ca4aad357]`
   regeln …"*. A new flavour of debug leak.
5. **Broken citation placeholders** — "(angegeben in,,,)" appears several times in
   the Location row (empty citation render).
6. **Placeholder "0 ha"** for every parcel area, and "Buffer Zone" status applied
   uniformly.

### B.3 Accurate legal substance ✅
Where it isn't tripped by the above, the legal analysis is strong and correct:
beschränkt persönliche Dienstbarkeit + Vormerkung in Abt. II (clause 4),
Sicherungsübereignung to the financing bank (clause 10), §550 BGB
Gestattungsvertrag-vs-Pachtvertrag distinction, and ~30 correctly-identified
"not found" gaps with sound statutory framing.

### B.4 Verdict on DDiQ mode
**~7.5/10.** Real, measurable engineering progress — the cadastral-extraction and
parcel-hallucination bugs (the worst defects of the prior version) are fixed. But a
**Rückbau regression**, the **unresolved company contradiction**, a **Gemarkung
error**, and **new debug leaks (UUID)** keep it from climbing higher.

---

## PART C — Chat vs DDiQ: head-to-head

| Dimension | Chat / Q&A | DDiQ Report |
|---|---|---|
| Factual accuracy | **9.5/10** — zero errors found | 8/10 — Rückbau + company + Gemarkung errors |
| Completeness / coverage | 7/10 — selective | **9/10** — full 40-category sweep |
| Provenance transparency | **9/10** — inline `[M-1]/[C-x]` | 4/10 — no clean citations; leaks UUIDs |
| Contamination control | **9.5/10** — explicitly quarantines other contracts | not visibly tested |
| Structure / scannability | 7/10 — prose | **9/10** — tables, maps, action items |
| Output hygiene | **9/10** — clean | 5/10 — UUID + "(,,,)" leaks, "0 ha" |
| Internal consistency | **9/10** | 6/10 — company row vs other rows |

### What this tells us about LAI
- **The two modes have opposite strengths.** Chat is **high-precision, transparent,
  contamination-aware, but selective**. DDiQ is **comprehensive and well-structured,
  but more error-prone and leakier**.
- **The retrieval core is sound.** The chat answer's explicit rejection of the
  Prowind-Vestas reference chunks shows the underlying RAG can distinguish the
  target document from neighbours — strong evidence the grounding logic is healthy.
- **The DDiQ pipeline is the weaker link, but improving.** It loses the chat mode's
  citation transparency, and its post-processing (table building, status labels,
  report rendering) is where errors and debug leaks creep in. Crucially, the
  parcel-table fix proves these are tractable engineering bugs, not model
  limitations.

---

## PART D — Honest overall review

LAI is a **genuinely capable, grounded due-diligence system**. It does not
hallucinate substantive legal findings, it reasons correctly in German
real-estate/permitting law, and — as the chat output proves — it can keep a
multi-document data room from cross-contaminating a single-document answer. That is
the hard part, and LAI does it well.

The gap between LAI and a polished product is **not intelligence — it's
engineering discipline in the DDiQ rendering pipeline**:
- one consistent fact per project (kill the company-identity contradiction),
- don't lose extractions between versions (the Rückbau regression),
- never leak internal IDs/citation placeholders into a client PDF,
- bring the chat mode's citation transparency *into* the DDiQ report.

If the DDiQ report adopted the chat mode's precision + provenance while keeping its
breadth, LAI would be a clearly best-in-class tool.

### Scores
| Output | Score |
|---|---|
| Chat / Q&A (doc 05) | **~8.5/10** |
| DDiQ Report (doc 06) | **~7.5/10** |
| **LAI overall** | **~8/10 and trending up** |

---

## PART E — Prioritised recommendations

**P0 — correctness**
1. Fix the **Rückbau extraction regression** — clause-level removal duties
   (clause 8 here) must not be dropped; separate "contractual removal duty" from
   "§35 BauGB financial bond".
2. Resolve the **Project Company contradiction** — if any row names the party
   (Windpark Lamstedt GbR), no row may call it "unnamed". Add a cross-row
   fact-reconciliation step.
3. Fix the **Gemarkung field** — don't substitute the Gemeinde (Hemmoor) for the
   Gemarkung (Heeßel); reuse the (now-correct) parcel-table values.

**P1 — output hygiene**
4. Strip internal IDs (`[2de3f023-…]`) and empty citation placeholders
   ("(angegeben in,,,)") from rendered reports.
5. Replace placeholder "0 ha" / blanket "Buffer Zone" with real values or an
   honest "n/a".

**P2 — bring chat strengths into DDiQ**
6. Add **inline provenance** to the DDiQ report (the chat mode's `[M-1]/[C-x]` is
   exactly the model to copy).
7. Carry over the **contamination-quarantine behaviour** explicitly when a report
   spans multiple documents.

**P3 — completeness for chat**
8. For "exhaustive clause" requests, increase recall so material clauses (auflösende
   Bedingung, bürgschaft cascade, Gerichtsstand) aren't omitted.

**P4 — regression safety**
9. Build a gold-fact regression set from docs 05/06 (term, rent, parcel list, party
   names, removal duty) so fixes like the cadastral table don't regress and the
   Rückbau drop is caught automatically.
