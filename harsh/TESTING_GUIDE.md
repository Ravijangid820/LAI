
# LAI — Single-PDF Testing Guide (WP Lamstedt VDR)

**Purpose:** exercise every chat feature + edge case against real wind-park
due-diligence documents before the demo. Each PDF below is a *separate*
single-document chat session (multi-document Matter is not live until the
next serve_rag restart).

**Frontend:** http://192.168.178.82:5173 (network) · http://localhost:5173 (local)
**Files:** this folder — `LAI/demo-seed/lamstedt/`

**How to test:** start a new chat → upload ONE PDF → run its question block.
Each question lists the **edge case** it probes and what a **good** answer
looks like. DE = German (primary), EN = English (cross-lingual check).

Legend: `[M-n]` = your uploaded document · `[C-n]` = legal corpus ·
`(unbelegt)` = fabrication stripped · 🟠 = jurisdiction warning expected.

---

## PDF 01 — `01_Aenderungsgenehmigung_BImSchG_10von11_2007.pdf`
**What it is:** BImSchG amendment permit (Änderungsgenehmigung) for 10 of 11
turbines, Landkreis Cuxhaven, 2007. The richest single document — covers
permit, location, turbine type, count.

### A · Happy path (expect grounded `[M-n]` answer + clickable chip)
- **DE:** Welcher Genehmigungsstatus ergibt sich aus diesem Bescheid?
  **EN:** What permit status does this document establish?
- **DE:** Wie viele Windenergieanlagen umfasst die Genehmigung, und wie viele waren ursprünglich geplant?
  **EN:** How many turbines does the permit cover, and how many were originally planned?
- **DE:** Welcher Anlagentyp und welche Nennleistung sind genannt?
  **EN:** Which turbine type and rated power are stated?
- **DE:** Welche Behörde hat den Bescheid erlassen und wann?
  **EN:** Which authority issued the permit and when?
✅ *Good:* cites `[M-1]`, click → PDF passage. Should surface "10 von 11", Enercon E-70 E4, Landkreis Cuxhaven, 2007.

### B · Statutory grounding (doc silent → "request from client")
- **DE:** Ist eine Rückbauverpflichtung mit Sicherheitsleistung geregelt?
  **EN:** Is a decommissioning obligation with a financial security regulated?
- **DE:** Welche Pachtlaufzeit ist für die Standortflächen vereinbart?
  **EN:** What lease term is agreed for the site parcels?
✅ *Good:* names the statutory requirement (e.g. § 35 Abs. 5 BauGB) with `[C-n]`, then "nicht in den Unterlagen enthalten → beim Mandanten anfordern".

### C · Jurisdiction trap 🟠 (expect amber warning)
- **DE:** Gilt für dieses Projekt die 10H-Abstandsregelung?
  **EN:** Does the 10H setback rule apply to this project?
- **DE:** Muss der Mindestabstand zur Wohnbebauung nach bayerischem Recht eingehalten werden?
  **EN:** Must the minimum distance to housing under Bavarian law be observed?
✅ *Good:* 🟠 "10H = Bayern (Art. 82 BayBO); Projekt liegt in Niedersachsen → nicht einschlägig."

### D · Hallucination bait (expect NO invented numbers)
- **DE:** Wie hoch ist die im Bescheid genannte Bürgschaftssumme in Euro?
  **EN:** What is the exact guarantee amount in euros stated in this permit?
- **DE:** Nenne das genaue Datum der Bestandskraft des Bescheids.
  **EN:** State the exact date this permit became final and binding.
✅ *Good:* says it's not stated / marks `(unbelegt)`. Must NOT invent a figure.

### E · Cross-lingual
- **EN:** In plain English, summarise what this German permit authorises and any conditions attached.
- **EN:** Which German statute is the legal basis for this permit, and what does it require?
✅ *Good:* English prose, German statute names verbatim (§ 4 BImSchG, 4. BImSchV).

---

## PDF 02 — `02_OVG_Niedersachsen_Urteil_Rueckbau_2017.pdf`
**What it is:** Higher Administrative Court (OVG Niedersachsen) ruling on
decommissioning / permit scope. Tests case-law reasoning + the high-risk finding.

### A · Happy path
- **DE:** Worum ging es in diesem Urteil des OVG Niedersachsen?
  **EN:** What was this OVG Lower Saxony ruling about?
- **DE:** Wie hat das Gericht zur Rückbauverpflichtung entschieden?
  **EN:** How did the court rule on the decommissioning obligation?
- **DE:** Welche Windenergieanlagen waren von der Entscheidung betroffen?
  **EN:** Which turbines were affected by the decision?
✅ *Good:* `[M-1]` citation; should reflect that the permit was annulled for specific turbines (the 🔴 DD finding).

### B · Risk framing (a lawyer's read)
- **DE:** Welches Risiko ergibt sich aus diesem Urteil für einen Käufer des Windparks?
  **EN:** What risk does this ruling create for a buyer of the wind park?
- **DE:** Ist die Genehmigung für alle Anlagen bestandskräftig?
  **EN:** Is the permit final and binding for all turbines?
✅ *Good:* flags the partial-annulment / bestandskraft risk plainly, cited.

### C · Statutory grounding
- **DE:** Auf welche gesetzliche Grundlage stützt das Gericht die Rückbaupflicht?
  **EN:** On what statutory basis does the court ground the decommissioning duty?
✅ *Good:* cites § 35 Abs. 5 BauGB / relevant provision via `[C-n]` if the judgment doesn't quote it in full.

### D · Hallucination bait
- **DE:** Wie lautet das Aktenzeichen und das genaue Verkündungsdatum?
  **EN:** What is the case number and exact date of judgment?
✅ *Good:* gives only what's in the text; no invented Aktenzeichen.

---

## PDF 03 — `03_Enercon_Wartungsvertrag_2019.pdf`
**What it is:** Enercon full-maintenance (O&M) contract, signed 2019. Tests
commercial clause extraction.

### A · Happy path (clause Q&A)
- **DE:** Welche Verfügbarkeitsgarantie sieht der Wartungsvertrag vor?
  **EN:** What availability guarantee does the maintenance contract provide?
- **DE:** Wie lange läuft der Vertrag, und gibt es Verlängerungsoptionen?
  **EN:** What is the contract term, and are there extension options?
- **DE:** Welche Leistungen sind in der Vollwartung enthalten?
  **EN:** What services are included in the full-maintenance scope?
- **DE:** Welche Pönalen gelten bei Unterschreitung der Verfügbarkeit?
  **EN:** What penalties apply if availability falls short?
✅ *Good:* `[M-1]` citations to the specific clauses; availability typically 97–98 %.

### B · Statutory / standard grounding
- **DE:** Welche Gewährleistungsrechte hat der Betreiber nach BGB-Werkvertragsrecht?
  **EN:** What warranty rights does the operator have under German works-contract law?
✅ *Good:* §§ 631 ff. BGB via `[C-n]` framing where the contract is silent.

### C · Hallucination bait
- **DE:** Wie hoch ist die jährliche Wartungspauschale in Euro?
  **EN:** What is the annual maintenance fee in euros?
✅ *Good:* states the figure only if present; otherwise `(unbelegt)` / not stated.

---

## PDF 04 — `04_VRB_Darlehensvertrag_6Mio_2019.pdf`
**What it is:** €6 m bank loan agreement (Volksbank Raiffeisenbank). Tests
financing / security extraction.

### A · Happy path
- **DE:** Welche Darlehenssumme und welcher Zinssatz sind vereinbart?
  **EN:** What loan amount and interest rate are agreed?
- **DE:** Welche Sicherheiten verlangt die Bank (Grundschuld, Abtretung, Verpfändung)?
  **EN:** What collateral does the bank require (land charge, assignment, pledge)?
- **DE:** Welches Tilgungsprofil und welche Laufzeit hat das Darlehen?
  **EN:** What repayment profile and term does the loan have?
- **DE:** Gibt es Covenants wie einen Mindest-DSCR?
  **EN:** Are there covenants such as a minimum DSCR?
✅ *Good:* `[M-1]` to the financing/security clauses.

### B · Statutory grounding
- **DE:** Was bedeutet die Bestellung einer Grundschuld rechtlich für den Grundstückseigentümer?
  **EN:** What does granting a land charge legally mean for the landowner?
✅ *Good:* §§ 1191 ff. BGB (Grundschuld) via `[C-n]`.

### C · Hallucination bait
- **DE:** Nenne die genaue IBAN des Darlehenskontos.
  **EN:** State the exact IBAN of the loan account.
✅ *Good:* refuses / not stated — never invents an IBAN.

---

## PDF 05 — `05_EWE_Netzanschlussvertrag_2008.pdf`
**What it is:** EWE Netz grid-connection contract, 2008. Smallest doc —
tests grid / technical terms.

### A · Happy path
- **DE:** Wer ist der Netzbetreiber, und welche Anschlussleistung ist vereinbart?
  **EN:** Who is the grid operator, and what connection capacity is agreed?
- **DE:** Wo liegt der Netzverknüpfungspunkt?
  **EN:** Where is the grid connection point?
- **DE:** Welche Pflichten treffen den Anlagenbetreiber beim Netzanschluss?
  **EN:** What obligations does the operator have for the grid connection?
✅ *Good:* `[M-1]` to the relevant clauses; EWE Netz as operator.

### B · Statutory grounding (EEG / EnWG)
- **DE:** Welcher Anspruch auf Netzanschluss besteht nach EEG für Windenergieanlagen?
  **EN:** What grid-connection right do wind turbines have under the EEG?
✅ *Good:* EEG/EnWG provisions via `[C-n]`.

---

## Cross-PDF edge cases (run on ANY uploaded PDF)

### F · Corpus-only (knowledge base, no document needed)
- **DE:** Ab welcher Gesamthöhe ist eine WEA nach BImSchG genehmigungspflichtig?
  **EN:** Above what total height does a turbine require a BImSchG permit?
- **DE:** Welche Lärm-Immissionsrichtwerte gelten nachts nach TA Lärm?
  **EN:** What night-time noise limits apply under TA Lärm?
- **DE:** Welche artenschutzrechtlichen Prüfungen verlangt das BNatSchG?
  **EN:** What species-protection checks does the BNatSchG require?
✅ *Good:* `[C-n]` corpus answer (the > 50 m / 4. BImSchV Nr. 1.6 chain).

### G · Chat-mode / no-RAG (expect plain reply, no chips, fast)
- **DE:** Hallo, was kannst du? · Danke, fasse das bitte kürzer.
  **EN:** Hi, what can you do? · Thanks, make that shorter.
✅ *Good:* conversational, no citation chips.

### H · Conversation memory (follow-ups)
1. Ask any **A** question. 2. Then: **DE:** Und was bedeutet das für den Käufer? / **EN:** And what does that mean for the buyer?
3. Then: **DE:** Antworte ab jetzt auf Englisch. / **EN:** Answer in English from now on.
✅ *Good:* follow-up resolves against the prior turn; language switch sticks.

### I · Streaming + feedback (UX)
- Ask an **A** question → answer **streams** word-by-word, then snaps to the chip version.
- Hover the answer → 👍 / 👎 → reload page → vote persists.

### J · Robustness / stress
- **Multi-part:** Fasse den Status zusammen, nenne den Anlagentyp und erkläre die kritischen Auflagen.
- **Out-of-domain:** Was sagt das Steuerrecht zur Abschreibung von Photovoltaik? *(graceful — may be thin / `(unbelegt)`)*
- **Minimal input:** a single `?` or one word.
- **Very long paste:** paste a paragraph of the contract and ask "Erkläre diese Klausel."
✅ *Good:* no crash, no fabrication, sensible handling.

---

## Performance checklist (grade before demo)

| Check | Target |
|---|---|
| First streamed token | ~1–3 s |
| Full grounded answer | ~5–15 s |
| Every `[M-n]`/`[C-n]` chip opens a real source | 100 % |
| Hallucination bait (D) never invents numbers | 100 % |
| Jurisdiction trap (C) shows 🟠 warning | yes |
| Feedback persists across reload | yes |
| Chat-mode (G) returns no chips, fast | yes |

## Known limitation during this test window
- **One PDF per chat.** Uploading a 2nd PDF to the same chat won't yet fan
  out to `[M-2]` — multi-document Matter goes live on the next serve_rag
  restart (commit `8d0c376`). Test each PDF in its own chat for now.
- **Large PDFs** (the multi-MB Gutachten) parse slower on upload (Docling
  OCR). The 5 staged here are chosen to be reasonably sized.
