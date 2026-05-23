# DDiQ Report Quality Analysis — Windpark Lamstedt (Document 05)

**Analyst:** LAI review · **Date:** 2026-05-22
**Report assessed:** `DDiQ Report – Windpark Lamstedt2.pdf` (16 pages)
**Source document:** `05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt_10pg.pdf` (10 pages)

---

## 1. What the source document is

The actual **ground-use agreement (Nutzungsvertrag zum Errichten und Betreiben von
Windenergieanlagen)** — the project's core land lease.

- **Grundeigentümer (owner):** Gemeinde Lamstedt, Schützenstraße 20, 21769 Lamstedt
- **Anlagenbetreiber (operator):** DWP GmbH & Co. Windkraft E+V KG,
  Sönke-Nissen-Koog 58, 25821 Reußenköge (eingetragen im Handelsregister des
  Amtsgerichts)
- **Signed:** 02.07.2007 · **Gerichtsstand:** Amtsgericht Otterndorf
- 15 clauses plus an appendix listing the Wegeflächen (Barackenweg / Heuweg /
  Kampenweg / Weg 4 / Weg 5 = **3,41285 ha / 34.128,5 m²**) and Ausgleichsmaßnahmen
  (compensation) plans, Stand 27.02.2003
- **Key terms:** 30-year term + 2×5-year options (§6); rent 6→7 % of feed-in
  revenues (§3); Rückbau bürgschaft €7.500 → cap €25.000 per WEA (§11.3);
  beschränkt persönliche Dienstbarkeit at ready rank (§7); Sicherungsübereignung
  and bank step-in (§9); auflösende Bedingung — valid, uncontested permit (§14)

This is the strongest report of the three reviewed, because it is built on the
richest, most structured DD document.

---

## 2. Accurate, well-grounded extractions ✅

- **Term & Extension** — 30-year base term from 1 Jan of the commissioning year +
  2×5-year options exercisable ≥6 months before end (max 40 years). Matches §6.
- **Reinstatement / Rückbau** — bürgschaft cascade **€7.500 → +€2.500/yr after a
  4-year Ruhezeit → cap €25.000 per WEA**, payable to Samtgemeinde Börde Lamstedt as
  Treuhänderin, foundation removed to 1 m depth. Matches §11.3 precisely; the
  judgment that €25.000 may undershoot real reinstatement cost is sound.
- **Cable & Access Easements / Land Registry** — beschränkt persönliche
  Dienstbarkeit at ready rank, Rangrücktrittserklärungen, Rücktrittsrecht if rank
  not reached by Baubeginn. Matches §7.
- **Total Capacity row** — captures the 6→7 % of feed-in-revenue rent and the §14
  permit condition.
- **Insurance** — correctly cites §2 and the separate 14 March 2007
  Haftung / Bürgschaft agreement.
- **Project Company — now correct.** Identifies the legal form (GmbH & Co. KG) and
  notes the missing HR number. This **fixes the error from the document-06 report**,
  which wrongly claimed the operator was not named.
- All ~30 "not found" determinations are correct.
- **No phantom map pin and no fabricated parcels** — empty maps are rendered
  honestly, and (correctly) no Cadastral Parcels table appears since the contract
  lists only Weg names, not Flurstücke.

---

## 3. Real weaknesses ⚠️

1. **Location — wrong "can't verify Gemarkung" claim.** §1 explicitly states
   "Gemarkung Lamstedt"; the report says the Gemarkung cannot be verified. It is
   named in the document.

2. **Unsurfaced concrete data.** §1 gives the leased area **3,41285 ha**
   (= 34.128,5 m² in the appendix) — not surfaced under Site Control. Likewise the
   **110-kV feed-in line** (§3) under Grid Connection, and the §4.3 250 m
   building-free radius under Setback.

3. **Missed appendix content.** The annex contains the Wegefläche schedule (named
   Wege) and **Ausgleichsmaßnahmen / Kompensation plans** (Stand 27.02.2003). The
   report treats Environmental Impact / Species Protection as wholly absent without
   noting these compensation-measure plans exist.

4. **Clause-citation slips.** "§ 550 BGB" cited for the written-form clause where
   the document says **§ 127 BGB**; the rank-Rücktrittsrecht attributed to
   "§ 2 Abs. 2" is actually **§ 7 Abs. 2**; financing-consent attributed to "§4" is
   **§13.7 / §9**. Substance is right; cross-references are imprecise.

5. **Step-in tension.** The Securities row says step-in rights are not mentioned,
   but §9 is effectively a step-in / direct-agreement mechanism — and the Financing
   row acknowledges the bank's Eintrittsrecht.

6. **Output hygiene.** Debug tags (`[Sprache: …]`, `[#1]`, `[#2]`, `[#4]`) still
   leak; some rows render fully in English (Number of WEA, Type & Capacity, Project
   Company) while the rest are German.

---

## 4. Scores

| Dimension | Rating | Note |
|---|---|---|
| "Not found" determinations | **10/10** | All correct |
| Contract-term extraction | **9/10** | Term, rent, Rückbau, Dienstbarkeit excellent; minor clause-citation slips |
| Completeness (surfacing present data) | **7/10** | Missed Gemarkung, 3,41285 ha area, 110 kV, appendix compensation plans |
| Internal consistency | **8/10** | Step-in tension; Project Company now correct |
| Map / asset rendering | **9/10** | Clean, honest empty maps; no phantom pin; no fabricated parcels |
| **Overall** | **~8/10** | The strongest of the three reports |

---

## 5. Trend across all three reports

| | ENERCON (manual) | Lamstedt 06 (cable easement) | Lamstedt 05 (ground lease) |
|---|---|---|---|
| Overall | ~6.5/10 | ~7.5/10 | **~8/10** |
| Phantom map pin | **Yes (0,0)** | No | No |
| Fabricated parcels | n/a | **Yes (Flst 7414 / 4572)** | No |
| Company identity | n/a | **Wrong ("not named")** | **Correct** |
| Dominant failure mode | Wrong document type | Cadastral-table extraction | Under-surfacing present numbers |

As input quality rises, report quality rises and the bugs disappear in sequence —
phantom pin, then fabricated parcels, then the company-identity error.

**Persistent weaknesses across all three:**
- (a) Not always surfacing concrete figures that *are* in the document (areas,
  percentages, voltages).
- (b) Minor German legal clause cross-reference imprecision (§ numbers).
- (c) Leaked debug tags / mixed DE-EN output.

---

## 6. Recommended follow-ups

- Add a numeric-fact extraction pass so present figures (leased area, rent %,
  bürgschaft amounts, line voltage) are always surfaced, not just narrated as
  gaps.
- Tighten German clause cross-referencing (cite the § that actually appears in the
  source; distinguish §127 vs §550 BGB, §7 vs §2).
- Reconcile the Securities/Financing rows on step-in rights when §9-type clauses
  exist.
- Scan appendices/annexes for compensation (Ausgleich/Kompensation) and Wegefläche
  schedules before declaring environmental content absent.
- Continue stripping internal `[Sprache: …]` / `[#n]` metadata tags and normalise
  the report language (currently mixed DE/EN per row).
