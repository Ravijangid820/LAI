# DDiQ Report Quality Analysis — Windpark Lamstedt (FINAL, May 2026)

**Analyst:** LAI review · **Date:** 2026-05-23
**Report assessed:** `DDiQ Report –final.pdf` (17 pages, generated 23 May 2026)
**Companion artefact:** chat answer on the same contract (in-app legal-agent reply, with `[M-1]` citations)
**Source document:** `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` (19 pages, 11 / 12 Oct 2007)

This is the third pass on the same source contract. See
`DDiQ_Report_Quality_Analysis_Lamstedt.md` and `…_Lamstedt2.md` for the prior
baselines. The review is honest and grounded in the actual contract text.

---

## 0. TL;DR

**Overall: ~7.8 / 10 — net improvement over the first Lamstedt pass, with one
clear regression and several stubborn gaps.**

The engine has materially improved in three dimensions: (a) the cadastral table
column misalignment is gone, (b) the fabricated parcels (Flst 7414 / 4572 /
Hesdel / Heese) are gone, and (c) debug tags (`[Sprache: …]`, `[#n]`) no longer
leak into the deliverable. The chat answer now also emits proper `[M-1]`
citations — the strongest grounding signal we have yet seen.

What still hurts: the **Project Company is again declared "not named"** in the
PDF even though the contract names "Windpark Lamstedt GbR" on page 1 (this was
already fixed in the Lamstedt-2 report and has regressed); the **Term &
Extension row says "no information"** even though §1 of the contract says
*"für die Dauer des Bestehens des Windparks (voraussichtlich 30 Jahre)"* and
the chat answer gets it right; the **map tiles fail to load** ("Access blocked
— Referer required by OSM"), which is a new visible defect in the PDF; and the
**parcel table still emits `0 ha`, empty owners, and "Buffer Zone" status for
every row**, including parcels that are clearly cable-route parcels in §1.

---

## 1. Source document recap (so the review is grounded)

`06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` is a **cable-easement
contract** ("Gestattungsvertrag über die Verlegung von zwei Erdkabelleitungen"),
signed 11 / 12 Oct 2007.

- **Eigentümer:** Samtgemeinde Hemmoor, Rathausplatz 5, 21745 Hemmoor
- **Betreiber:** **Windpark Lamstedt GbR**, Sönke-Nissen-Koog 58, 25821 Reußenköge
- **Purpose:** lay two 20-kV Mittelspannungs-Dreileiter-Kabelsysteme + Steuerkabel
  to connect Windpark Lamstedt to the **Umspannwerk in Hemmoor**
- **15 numbered clauses + parcel schedule + 4 cadastral route-map sheets**
  (Stand 27/28.08.2007, GFN / LGN attribution)
- **Key terms:**
  - **Term:** §1 *"für die Dauer des Bestehens des Windparks (voraussichtlich
    30 Jahre)"*; §8 erlöschen on permanent shut-down + removal + Grundbuch deletion
  - **Compensation:** §5 — **2,00 € / lfd. Meter**; at **818 m total = 3.272 €**
  - **Schutzstreifen** §4: 2 m wide; **Erdüberdeckung** §2: ≥ 1 m
  - **Dingliche Sicherung:** §4 beschränkte persönliche Dienstbarkeit nebst
    Vormerkung, Abt. II, costs borne by Betreiber
  - **Step-in:** §10 Sicherungsübereignung an finanzierende Bank +
    unwiderrufliche Einwilligung in Eintritt Dritter; §11 unwiderrufliche
    Bevollmächtigung der Bank zum Abschluss eines Eintrittsvertrags
  - **Bank-protection:** §12 Bestimmungen mit Bezug auf das Sicherungsinteresse
    der Bank dürfen nicht ohne Bankzustimmung geändert / gelöscht werden
  - **Salvatorische Klausel:** §13; **Schriftform:** §14; **No oral side
    agreements:** §15
- **Parcel schedule** (page 1–2 of the contract):
  - Heeßel, Flur 1, Flst 62/1 — Stadt Hemmoor — **365 m** (Zeichn. 19e)
  - Heeßel, Flur 2, Flst 78/6 — Stadt Hemmoor — 5 m (Zeichn. 21)
  - Heeßel, Flur 4, Flst 114/13 — Stadt Hemmoor — 13 m
  - Heeßel, Flur 4, Flst 115/1 — Stadt Hemmoor — 3 m
  - Heeßel, Flur 4, Flst 112/1 — Stadt Hemmoor — 6 m
  - Warstade, Flur 17, Flst 16 — **Samtgemeinde Hemmoor** — 8 m
  - Warstade, Flur 17, Flst 5 — Stadt Hemmoor — 15 m
  - Warstade, Flur 17, Flst 2/1 — Stadt Hemmoor — **331 m**
  - Warstade, Flur 17, Flst 7 — **Dr. Franz Robert von Issendorf, Föhrenweg 10,
    91054 Erlangen** — 72 m
  - plus the parcels visible on the maps (74/4, 8/1 153 m, 11 123 m, 13 269 m,
    …) on Gemarkungen Warstade and Heeßel, Flure 4/5/6/14/17

That is the ground truth we are measuring the report against.

---

## 2. What clearly improved over Lamstedt-1 (same source) ✅

1. **No more fabricated parcels.** "Flst 7414", "Flst 4572", "Gemarkung
   Hesdel", "Gemarkung Heese" — all gone. Every parcel ID in the new cadastral
   table is one that actually appears in the contract or on its route-map
   sheets. This was the single largest accuracy problem of the first pass and
   it is fixed.

2. **Cadastral-table column alignment is fixed.** The first pass garbled
   Gemarkung / Flur / Flst by leaking Zeichn.-Nr. values into the Flur column
   ("Flur 24", "Flur 21"). The new table maps correctly: 62/1 → Heeßel Flur 1;
   78/6 → Heeßel Flur 2; 114/13 / 115/1 / 112/1 → Heeßel Flur 4; 16 / 5 / 2/1
   / 7 → Warstade Flur 17; 74/4 → Warstade Flur 6; 19 → Warstade Flur 14;
   41/2 / 46/2 / 54/2 / etc. → Heeßel Flur 1. All match the source.

3. **Site Control Coverage now lists real Flurstücke.** It correctly cites
   the contract date (11.10.2007), the parties (Samtgemeinde Hemmoor ↔
   Windpark Lamstedt GbR), §4 dingliche Sicherung, and enumerates Flst 74/4
   (32 m), 1 (23 m), 2/1 (331 m), 5 (15 m), 7, 12 (72 m), 8/1 (153 m), 11
   (123 m), 16 (8 m), 13 (269 m) in Gemarkung Warstade. Numbers and units
   are grounded in the route-map sheets.

4. **Investors row identifies "Windpark Lamstedt GbR"** correctly and notes
   honestly that the Gesellschaftsvertrag / Gesellschafterliste are not in the
   data room.

5. **Concrete physical data is surfaced.** Setback / 10H now picks up the
   **2-m Schutzstreifen** and **1-m Erdüberdeckung** (both real values from
   §4 and §2). Vergütung in the chat answer cites **2,00 € / lfd. Meter** with
   correct payment timing (bei Baubeginn, nicht vor notarieller Bestellung).

6. **Citations.** The chat answer emits `[M-1]` markers behind every factual
   claim. This is the strongest grounding signal we have yet seen from this
   pipeline — every assertion can be traced to the source.

7. **No phantom map pin, no fabricated "Negotiation/Partial" WEA row.** The
   Land Security Status Map and the WEA Lat/Lng/Address/Status row are
   correctly empty — there are no turbines in this contract.

8. **No debug-tag leakage.** `[Sprache: en; Abschnitt: de]`, `[#1]`, `[#2]`,
   `[Sprache: mixed]` — all gone from the rendered output. Big hygiene win.

9. **Strong "not found" discipline persists.** All ~25 rows that correctly
   say "this document doesn't cover X" (BImSchG permit, EEG subsidy regime,
   PPA, financing covenants, O&M, insurance, tax, aviation/lighting, species
   protection, etc.) are correct for a cable-easement contract. The reasoning
   for *why each gap matters* is legally literate.

---

## 3. Real defects in the new report ⚠️

### 3.1 Project Company — regression vs Lamstedt-2

The row says:

> "Die Projektgesellschaft ist im vorliegenden Dokument nicht namentlich als
> Vertragspartei aufgeführt. Der Vertrag wird zwischen der 'Gemeinde Hemmoor'
> (Eigentümer) und einem unbenannten 'Betreiber' geschlossen."

This is **factually wrong**. Page 1 of the contract names the Betreiber in bold:
**"Windpark Lamstedt GbR, Sönke-Nissen-Koog 58, 25821 Reußenköge"**. GbR *is*
the Rechtsform. The very next row (Investors) correctly states "Windpark
Lamstedt GbR" — so the report self-contradicts within one page.

This is the **same defect as Lamstedt-1**, which the Lamstedt-2 report (on the
05 doc) had already fixed. Bringing the 06 doc back through the pipeline has
regressed the fix.

Also: the row says "Gemeinde Hemmoor" — but the contract names **Samtgemeinde
Hemmoor** (and also "Stadt Hemmoor" as Eigentümer of most parcels). Three
different bodies are subtly distinct; the report flattens them.

### 3.2 Term & Extension — concrete fact in §1 marked as "missing"

The row says:

> "…enthalten keine Informationen zur Pachtdauer, Verlängerungsoptionen, der
> erwarteten Lebensdauer der Anlage oder der EEG-Förderdauer."

The contract §1 says, verbatim:

> "…**für die Dauer des Bestehens des Windparks (voraussichtlich 30 Jahre)**
> zu betreiben, dauernd zu belassen, ggf. auszuwechseln…"

and §8 specifies erlöschen on permanent shut-down. **The chat answer
correctly extracts "voraussichtlich 30 Jahre" (cited [M-1])** — so the
pipeline has the data; it just fails to land it in the Term & Extension cell.
That is a row-routing defect, not an extraction failure.

The framing "Pachtdauer / EEG-Förderdauer" is also slightly off: this is a
Gestattungsvertrag, not a Pachtvertrag — the relevant concept is "Laufzeit der
Dienstbarkeit", which §1 + §8 *do* provide.

### 3.3 Cadastral Parcels table — placeholder columns

The Flurstück / Gemarkung / Flur mapping is correct (big win), but the rest of
the table is unusable:

| Defect | Detail |
|---|---|
| All rows show **`0 ha`** | The contract gives **per-parcel route length in metres** (365, 331, 269, 153, 123, 72, …). None surfaces. |
| All **Owner cells empty** | Owners are listed in the parcel schedule: mostly **Stadt Hemmoor**, plus **Samtgemeinde Hemmoor** for Warstade 17/16, plus **Dr. Franz Robert von Issendorf** for Warstade 17/7. |
| All status = **"Buffer Zone"** | These are **cable-route parcels** (§1 grants Leitungsrecht *through* them); the 2-m Schutzstreifen is the buffer *within* them. Labeling every parcel "Buffer Zone" mis-categorises the schedule. |
| Per-parcel **Contract cell** = `—` | Every row could be linked back to "Gestattungsvertrag Lamstedt 11.10.2007". |

The map sheets even mark several parcels in green/colour to distinguish the
route trace — none of that is captured.

### 3.4 Map tiles fail to load — visible defect in the PDF

The "Project Location Map" panel shows **"Access blocked — Referer is required
by tile usage policy of OpenStreetMap's volunteer-run servers: osm.wiki/Blocked"**
across the visible tiles. OSM is rejecting the tile requests because the PDF
renderer is not sending a Referer header consistent with OSM's policy.

This is a **regression vs Lamstedt-1 and Lamstedt-2**, which both rendered the
map area as honestly blank. The current output looks broken in a client
deliverable. There is no useful map content to show (no turbine coordinates
in this contract), so the cleanest fix is to hide the map panel entirely when
there is nothing to plot — alternatively, fix the Referer header on the
tile fetch.

### 3.5 Internal contradictions persist

Two contradictions remain visible to a careful reader:

- **Project Company "nicht namentlich aufgeführt"** vs **Investors row =
  "Windpark Lamstedt GbR"** (see §3.1).
- **Term & Extension "keine Informationen"** vs **Site Control Coverage**
  citing the contract date and §4, *and* the chat answer citing
  "voraussichtlich 30 Jahre" with [M-1] (see §3.2).

A consistency pass that compared each row against (a) the other rows and
(b) the chat-answer extractions would catch both.

### 3.6 Missed concrete data — still

The chat answer surfaces **2,00 € / lfd. Meter** (good). Neither the chat
answer nor the PDF surfaces the **total route length of 818 m** or the
**total compensation of 3.272 €**, both of which are written explicitly in §5
of the contract ("*Bei einer Leitungslänge von 818 m ergibt dies eine
Gesamtentschädigung von 3.272,- €*"). These are exactly the kind of
hard-numeric DD facts a reviewer most wants.

The §4 phrasing "*Auf dem Grundstücksstreifen (Schutzstreifen) von insgesamt
2 m Breite dürfen für die Dauer des Bestehens der Leitungen keine Gebäude
errichtet oder sonstige Einwirkungen … vorgenommen werden*" gives a baulast-
like restriction; the Setback row partially captures the 2 m number but does
not frame it as an Einwirkungs-/Bauverbot.

### 3.7 Lease Defects — §550 BGB citation is off

The Lease Defects row cites **§ 550 BGB** for the Schriftformklausel. §550 BGB
applies to long-term **Miet-/Pachtverträge** and triggers if the written form
is not kept. The contract here is a **Gestattungsvertrag (dingliches
Leitungsrecht via §§ 1090 ff. BGB)** — §550 is not the right anchor; §126 BGB
(Schriftform) or simply §14 of the contract itself would be correct. The
Lamstedt-2 review flagged the same kind of citation slip.

The row also claims "Vertretungsmacht ist durch die Unterschrift des
Stadtdirektors dokumentiert." — but the Stadtdirektor signed for **Stadt
Hemmoor**, which is named in the parcel-schedule as parcel-owner, not for the
**Samtgemeinde Hemmoor** that the contract heads as Eigentümer/Vertragspartei.
This is exactly the Stadt-vs-Samtgemeinde confusion already noted under §3.1
and would itself be a Vertretungsmacht-Frage in a real DD review.

### 3.8 Cable & Access Easements — sloppy Flurstück enumeration

The row cites "Flst 11, 16, 13, 74/4, 1, 2/1, 5, 7, 12, 8/1" *"in der
Gemarkung Warstade"* — but **11, 16, 13, 1, 2/1, 5, 7, 12, 8/1 are in
Warstade Flur 17**, whereas **74/4 is in Warstade Flur 6**, and there is no
Flst 11 in Warstade Flur 17 *and* in Warstade Flur 6. The text reads as if
they all share one Gemarkung+Flur, which is incorrect. The cadastral table
*below* shows the right Flur split — so again, this is a row-text vs
structured-data inconsistency.

### 3.9 Language / hygiene

- Category labels still **English** ("Project Name", "Location", "Project
  Status", "Land Security & Ownership", "Permits & Regulatory Conditions",
  "Economics & Operations"), narrative content in **German**. Cleaner than
  before but still mixed in a client-facing PDF.
- No upfront classification line — a reviewer still has to scroll through ~25
  "not found" rows to discover that this is *only* the Kabeldienstbarkeit, not
  the BImSchG / Pacht / PPA / Financing documents. One header sentence ("Dies
  ist ein Gestattungsvertrag für die Kabelanbindung; alle Genehmigungs-,
  Pacht- und Finanzierungsunterlagen fehlen erwartungsgemäß") would save the
  scroll.

---

## 4. Scoring (and trend)

| Dimension | Lamstedt-1 | Lamstedt-2 (05 doc) | **Lamstedt-Final (06 doc)** |
|---|---|---|---|
| "Not found" determinations | 10/10 | 10/10 | **10/10** |
| Grounded legal narrative | 8.5/10 | 9/10 | **8.5/10** |
| Structured data (parcel table / IDs) | 5/10 | n/a | **7/10** (alignment fixed; area/owner/status still placeholder) |
| Internal consistency | 6/10 | 8/10 | **6.5/10** (Project Company + Term regressed) |
| Concrete commercial data surfaced | 4/10 | 7/10 | **6/10** (Vergütung yes; Term, total length, total € no) |
| Map / asset rendering | 8/10 | 9/10 | **6/10** (OSM "Access blocked" regression) |
| Output hygiene (debug tags, mixed lang) | 5/10 | 6/10 | **8/10** (tags gone; some EN/DE mix) |
| Citations / traceability | n/a | n/a | **9/10** (chat `[M-1]` is a clear step up) |
| **Overall** | **~7.5/10** | **~8/10** | **~7.8/10** |

The trend is real but uneven: hygiene and structured-data parsing improved
clearly; the company-identity and term-clause regressions and the OSM-tile
defect drag the headline number back close to Lamstedt-2. Citations are the
single biggest new signal — they should be the foundation for a consistency
check across rows.

---

## 5. Recommended follow-ups (prioritised)

1. **Block the "Project Company not named" path when a named Betreiber exists
   in the contract.** The Investors row already names it correctly; reuse that
   extraction (or run a cross-row consistency check).
2. **Route the §1 Laufzeit phrase ("voraussichtlich 30 Jahre") into Term &
   Extension.** The chat answer already finds it — wire the same extraction
   into the table row.
3. **Either hide the Project Location Map when there are no turbines /
   coordinates, or fix the OSM Referer policy.** A broken-looking map is worse
   than no map.
4. **Populate the cadastral table's Owner / Area-or-Length / Status columns.**
   Each parcel row in the source has Eigentümer + Länge in m + Zeichn.-Nr.
   "0 ha" + empty owner + "Buffer Zone" is a placeholder that ships as content.
5. **Surface the §5 totals** (818 m and 3.272 €) under a Compensation /
   Vergütung row — these are the cleanest DD numbers in the whole contract.
6. **Reconcile Stadt Hemmoor vs Samtgemeinde Hemmoor vs Gemeinde Hemmoor.**
   They are three distinct bodies in this contract; the report uses them
   interchangeably and that is itself a Vertretungs-/Vertragspartei-Frage.
7. **Fix the §550 BGB citation** — the right anchors are the contract's own
   §14 (Schriftform) and §126 BGB; §550 BGB only fits Miet-/Pacht-Verträge.
8. **Add a one-sentence document-type header** at the top of the report
   ("Vorliegend nur Gestattungsvertrag Kabeltrasse — Pacht-, BImSchG-, EEG-,
   PPA- und Finanzierungsunterlagen erwartungsgemäß nicht enthalten"). Saves
   the reader ~15 rows.
9. **Use the chat answer's `[M-1]` citations as a cross-check inside the PDF
   pipeline.** Any row that says "no information" should be rejected at build
   time if a cited fact in the chat answer covers the same field.
10. **Bring the table category labels into German** if the narrative is
    German, or all into English. Pick one for client deliverables.

---

## 6. Verdict on the chat answer (separate artefact)

The chat answer (the one that opens "*Basierend auf der Analyse des
vorliegenden Gestattungsvertrags…*") is **the stronger artefact of the two**.
Every claim carries an `[M-1]` citation; the **30-year Laufzeit**, the
**2,00 €/m Vergütung**, the **Sicherungsübereignung an die Bank**, the
**Schriftform**, and the **salvatorische Klausel** are all extracted with the
right clause-level granularity. The user's request — "*nur Informationen, die
ausdrücklich im Dokument enthalten sind*" — is honoured: nothing in the
answer goes beyond the contract.

Two refinements would tighten it further:

- Surface the **818 m / 3.272 € totals** from §5 — already present in the
  source, easy to add.
- Either drop **"voraussichtlich 30 Jahre"** as a "Laufzeit" (the contract
  is explicit that it erlöscht on Stilllegung, not on a fixed date — the 30
  years is the expected operating life of the windpark, not the contract
  term) or call it "voraussichtliche Lebensdauer" rather than "Laufzeit". The
  current phrasing slightly conflates the two.

---

## 7. Bottom line

Genuine progress. The new report is **substantially more useful** than the
first pass on the same source: no fabricated parcels, no debug-tag leakage,
correct cadastral-table alignment, real Setback / Erdüberdeckung extraction,
and — most importantly — **citations** in the chat answer.

But two specific defects need to be retired before this is a clean client
deliverable: (1) the "Project Company not named" regression, which is wrong
on its face and contradicts the next row, and (2) the Term & Extension row
ignoring an extraction the chat pipeline already nails. The OSM "Access
blocked" map is a cosmetic but immediately-visible third item.

If those three are fixed without losing the hygiene gains, this pipeline is
within striking distance of a defensible Phase-1 DD report on a real German
cable-easement contract.
