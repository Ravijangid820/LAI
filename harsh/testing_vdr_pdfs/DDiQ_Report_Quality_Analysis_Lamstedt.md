# DDiQ Report Quality Analysis — Windpark Lamstedt

**Analyst:** LAI review · **Date:** 2026-05-22
**Report assessed:** `DDiQ Report – Windpark Lamstedt.pdf` (15 pages)
**Source document:** `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` (19 pages)

---

## 1. What the source document is

A genuine **cable easement agreement (Gestattungsvertrag über die Verlegung von
zwei Erdkabelleitungen)** — not a technical manual.

- **Eigentümer (owner):** Samtgemeinde Hemmoor, Rathausplatz 5, 21745 Hemmoor
- **Betreiber (operator):** Windpark Lamstedt GbR, Sönke-Nissen-Koog 58,
  25821 Reußenköge
- **Purpose:** lay **two 20 kV underground cable systems** (plus control cable) to
  connect Windpark Lamstedt to the substation (Umspannwerk) in Hemmoor
- **Signed:** 11 / 12 Oct 2007
- Contains a parcel schedule (Gemarkung / Flur / Flst / Zeichn.-Nr. / Länge),
  15 clauses, and ~16 cadastral route maps (Stand 27./28.08.2007, source LGN /
  Niedersächsische Vermessungs- und Katasterverwaltung)
- **Key commercial terms:** route length **818 m**; compensation **€2 / lfd. m =
  €3,272** (clause 5); right secured for the duration of the windpark
  (~30 years, clause 1)

Because this is a real DD document, the report is **far richer and substantially
more accurate** than the ENERCON report — but it carries real extraction errors.

---

## 2. Accurate, well-grounded extractions ✅

- **Location** — Niedersachsen / Landkreis Cuxhaven / Gemeinde Hemmoor /
  Gemarkungen Heeßel + Warstade, sourced to the "Kabelanbindung Windpark Lamstedt"
  plans (27./28.08.2007) with LGN attribution. All correct; LK Cuxhaven even
  matches the handwritten note on page 1 of the contract.
- **Reinstatement / Rückbau** — correctly extracts clause 8 (operator must remove
  cables, restore the land, delete the Grundbuch entry at own cost) and correctly
  flags the missing §35 Abs. 5 BauGB Rückbaubürgschaft.
- **Land Registry / Cable Easement** — beschränkte persönliche Dienstbarkeit +
  Vormerkung in Abteilung II, operator bears notary/registration cost,
  Sicherungsübereignung to the financing bank. Accurate.
- **Lease Defects** — correctly distinguishes Gestattungsvertrag from Pachtvertrag
  (§550 BGB) and maps Schriftform (§14) / transferability (§9–11). Legally
  sophisticated and correct.
- All ~30 "not found" determinations (BImSchG permit, PPA, financing, species
  protection, noise/shadow, etc.) are **correct** for this document type.
- **No phantom map pin** — the Project Location Map is correctly blank (no turbine
  coordinates), and the Land Security map shows no fabricated WEA row. A clear
  improvement over the ENERCON report.

---

## 3. Real accuracy problems ⚠️

1. **Project Company — factual error + self-contradiction.** The row states the
   operator is "nicht namentlich benannt" and that the legal form is not given. But
   the contract names **"Windpark Lamstedt GbR"** with full address, and **GbR is
   the legal form**. The very next row (Investors) correctly identifies "Windpark
   Lamstedt GbR." The two rows contradict each other.

2. **Garbled / hallucinated parcel IDs.** The Cable & Access narrative cites
   "Gemarkung Warstade Flur 17 **Flst. 7414**", "Gemarkung **Hesdel** Flur 4
   **Flst. 4572**", and "Gemarkung **Heese**." None exist — the real Gemarkung is
   **Heeßel** (not Hesdel/Heese) and those Flst numbers (7414, 4572) appear nowhere
   in the document. Only "519/365" is genuine. Likely OCR corruption or invented
   values.

3. **Cadastral Parcels table — column misalignment.** Correct rows exist (62/1
   Heeßel Flur 1; 112/1 Heeßel Flur 4; 16 / 2-1 / 7 Warstade Flur 17), but "Heeßel
   **Flur 24**" and "Flur 21" do not — the source has Flure 1/2/4/5, and "24/25" are
   values from the **Zeichn.-Nr. column**, misread as Flur. Roughly half the rows
   are garbled, and all show a placeholder "0 ha" area.

4. **Site Control Coverage — wrong claim.** It states the Flurstücksnummern are
   "redigiert oder nicht aufgeführt." They are clearly listed (contract schedule
   pages 1–2, the route maps, and the report's own parcel table). Internal
   contradiction.

5. **Missed concrete data.** Route length **818 m** and compensation
   **€2/m = €3,272** (clause 5) — a clean, valuable DD datapoint — appear in no
   category.

6. **Minor issues.**
   - Term & Extension claims no expected lifetime, but clause 1 states "für die
     Dauer des Bestehens des Windparks (voraussichtlich 30 Jahre)."
   - Securities cites "(§4)" for the Sicherungsübereignung that is actually
     clause 10.
   - Spelling "Hackenühlen" vs the document's "Hackemühlen"; missed Gemarkung
     Nordahn.
   - Debug tags (`[Sprache: …]`, `[#1]`, `[#2]`, `[#5]`) and mixed DE/EN narrative
     still leak into the output.

---

## 4. Scores

| Dimension | Rating | Note |
|---|---|---|
| "Not found" determinations | **10/10** | All ~30 correct for this document type |
| Grounded legal extraction (narrative) | **8.5/10** | Rückbau, Dienstbarkeit, step-in, §550 distinction all excellent |
| Structured data accuracy (parcel table / IDs) | **5/10** | Column misalignment + garbled / invented Flst numbers |
| Internal consistency | **6/10** | Company "not named" vs "Windpark Lamstedt GbR"; Site Control vs parcel table |
| Map / asset rendering | **8/10** | No phantom pin; blank-but-honest maps |
| **Overall** | **~7.5/10** | Genuinely useful and mostly accurate; errors concentrated in parcel data and company identity |

---

## 5. ENERCON vs Lamstedt — comparison

| | ENERCON E-82 (manual) | Windpark Lamstedt (Gestattungsvertrag) |
|---|---|---|
| Source fit for DD | Wrong document type (manual) | Real DD document |
| Useful findings | ~5% (mostly "not found") | High — location, easement, Rückbau, step-in |
| Hallucinated findings | None | A few parcel IDs (Flst 7414 / 4572, Hesdel/Heese) |
| Map rendering | **Phantom pin at 0,0** + fake "Partial/Negotiation" | Clean — blank maps, no fake rows |
| Internal consistency | Good | Two contradictions (company, site control) |
| Overall | ~6.5/10 | ~7.5/10 |

**Takeaway:** Fed a real contract, the engine produces strong, legally literate,
grounded output and drops the phantom-pin bug. The dominant remaining failure mode
shifts from *"wrong document type"* to **structured-data extraction** — German
cadastral tables (Gemarkung / Flur / Flst / Zeichn.-Nr. columns) get misaligned and
a few parcel IDs appear hallucinated.

---

## 6. Recommended follow-ups

- Harden cadastral-table parsing: map the Gemarkung / Flur / Flst / Zeichn.-Nr. /
  Länge columns explicitly so Zeichn.-Nr. and Länge values stop leaking into the
  Flur / Flst fields.
- Constrain parcel IDs to values actually present in the source (guard against
  OCR-garbled / invented Flst numbers); cross-check Gemarkung spellings
  (Heeßel ≠ Hesdel / Heese).
- Reconcile the "Project Company" logic with named contract parties — if a party is
  named (e.g. "Windpark Lamstedt GbR"), do not report it as "not named."
- Surface concrete commercial terms (route length, per-meter compensation, total
  €) when present.
- Continue stripping internal `[Sprache: …]` / `[#n]` metadata tags from rendered
  output.
