# LAI — Final Pilot Test Plan & Verdict

**Date:** 2026-05-23 (original)
**Audience:** Project lead pre-Kristian demo
**Status:** Pilot-ready with two declared open-mouth edges (ALKIS external; LLM multi-park recall)
**Build:** post-`b6421b6` (Phase-B org-tenancy complete) + this session's UI fixes

---

## Update 2026-06-03 — pilot is still pending, prep kit now available

This doc was written 2026-05-23 to evidence pilot-readiness for an
internal demo. Since then:

* **Engineering side has continued maturing** — 5 production fixes
  shipped + live since 2026-06-02 22:41 restart (UI/meta router, file-
  access router, German language detector, BM25 v5 retune, persistence
  RLock). 4 routing/lang fixes verified live via direct probes (4/4).
* **Retrieval ceiling honestly measured** — Recall@30 = 0.49 over 200
  real BImSchG val questions. Six retrieval-tuning experiments across
  four layers; one positive shipped (BM25 v5, ~14 % faster) and five
  documented negatives. Full table at
  [`rj/blueprint/2026-06-02-retrieval-tuning-results.md`](../../rj/blueprint/2026-06-02-retrieval-tuning-results.md).
* **Two production audits closed** — the ks/as session audit's
  citation-validation question is answered (validator works for
  fabricated handles; off-topic-with-real-cites is closed at the
  routing layer, not citation), and a wider audit of session
  `cd5a4a1b…` surfaced a NEW failure family that's now fixed +
  tested. Details at
  [`rj/blueprint/2026-06-03-citation-validation-audit.md`](../../rj/blueprint/2026-06-03-citation-validation-audit.md)
  and
  [`rj/blueprint/2026-06-03-wider-sessions-audit.md`](../../rj/blueprint/2026-06-03-wider-sessions-audit.md).
* **Pilot prep kit drafted** — 5-doc bundle at
  [`rj/pilot-prep/`](../../rj/pilot-prep/) — pitch one-pager, target-
  firm shortlist with fit-scored tiers, German cold-email template,
  4–8 week free-pilot offer terms with 3 variants, and a Day-1 /
  Day-30 / Day-90 readiness self-check covering what boss IS safe to
  claim versus what NOT to claim. Status remains 🔄 (in progress)
  until a firm signs on for week 1.

**The test plan content below is still accurate at the engineering
layer.** The pilot itself remains 🔄 pending the relational outreach
the prep kit was designed to enable. See
[`harsh/PROGRESS_V2.md#L38`](../../harsh/PROGRESS_V2.md) for the
canonical 2.4 status row.

---

---

## 1. Verdict in one line

> **The system can no longer produce a fact it cannot defend.** Remaining
> edges are *informativeness* edges (less detail), never *misleading-output*
> edges. That's pilot-ready for a supervised legal context.

---

## 2. Market positioning & pain point

### Pain point we solve

A German M&A or project-finance lawyer doing due diligence on a wind-park
acquisition spends **3–5 days** of an associate's time digesting a 200-doc
VDR (BImSchG permits, Pachtverträge, Wartungsverträge, Gutachten,
Genehmigungen) just to produce the first-pass red-flag memo. The work is
**80 % rote retrieval** — finding the lease term, the Bürgschaft amount,
the §44 BNatSchG findings, the WEA-by-WEA Genehmigungsstand — and 20 %
legal judgment. The retrieval part is what kills throughput and burns
associate hours.

### LAI's four USPs (that survive scrutiny)

1. **Self-hosted.** German VDRs are NDA-bound and often privileged. No SaaS.
   Air-gapped if the firm wants.
2. **Grounded in a 350 GB German legal corpus** — BImSchG, BauGB, BNatSchG,
   EEG, OVG case law, BauOs of all 16 Bundesländer — **and** in the
   uploaded VDR docs. Every assertion clickable to its source paragraph
   via `[C-n]` (corpus) or `[M-n]` (matter).
3. **Honest unknowns.** When the corpus is silent, the system refuses to
   fabricate. Verified live for multi-park contamination — the lawyer
   gets "nicht enthalten" instead of plausible nonsense.
4. **Two surfaces, one engine.** Chat for fast Q&A on a matter; DDiQ
   report for the shareable red-flag memo. Same retrieval, same citations.

### Positioning line

> *"The legal corpus your associate would build if they had two years.
> Self-hosted, citation-grounded, German-native."*

### Target buyer

Mid-size German M&A / project-finance boutiques with 5–30 lawyers doing
recurring wind-energy or infrastructure DD. Kristian's segment exactly.

### Competitive frame

| Competitor | What they ship | Why LAI wins |
|---|---|---|
| Harvey | US chatbot on US corpus, SaaS | No German wind law; no self-host |
| ContractPodAi | CLM (contract lifecycle), not DD | Doesn't read a VDR |
| Kira | OCR + clause classification | Not generative; no Q&A |
| ChatGPT / Claude | Generic LLM | Hallucinates German statutes; no grounded retrieval |
| **LAI** | German legal-DD agent, self-hosted | All of the above, plus citations |

---

## 3. What was shipped this session

| Layer | Before | Now |
|---|---|---|
| **DDiQ accuracy** | Composite "20 MW · 10 turbines" facts on a Zodel-subject report (false confidence) | Per-park breakdown; multi-park triggers honest unknown header with German contextual notes naming peer parks |
| **Findings attribution** | Findings could leak across parks | `Finding.park` set by LLM; "nicht Gegenstand dieses Berichts" surfacing for unattributed |
| **Tenancy** | `user_id` scoping only | Phase-B `org_id` end-to-end (`_assert_in_org` everywhere) |
| **OCR** | Tesseract dropping pages under GPU load | VLM-OCR with retry, page-by-page progress, fail-loud |
| **Chat answers** | Plain text, "stuck at 99 %", lying green check, hallucinated "[M-8] failed" notes | GFM tables, markdown structure, 3000-token cap; honest progress with decimals; failed rows hidden from manifest |
| **Uploads** | Sequential single-file at a time, 50 MB cap, no cancel, vanished on tab switch | Parallel ×3, 100 MB cap, retry with backoff, per-row cancel, state survives all dashboard navigation |
| **Drop zone UX** | Floating queue above tabs | Smart drop zone hosts upload rows inline, status-tinted, "+ Add more" inline |
| **Project files** | Stuck "Warteschlange" forever on session drift; stale "error" lies | Backend wins; "Nicht in dieser Sitzung — erneut hochladen" honest state |
| **Build health** | — | `tsc` + `vite build` clean, no warnings on our changes |

---

## 4. Document inventory — what's available to upload

Staged in **[LAI/demo-seed/stress-vdr/](../demo-seed/stress-vdr/)** — each
file chosen to stress a specific part of the system.

### Core matter set (clean Lamstedt project)

| # | File | Pages | Size | Type | Stresses |
|---|---|---|---|---|---|
| 01 | `01_ENERCON_Betriebsanleitung_E82_196pg.pdf` | 196 | 18 MB | **scan** | VLM-OCR at scale (~196 vision calls), GPU saturation |
| 02 | `02_SDL-Gutachten_Lamstedt_156pg.pdf` | 156 | 15 MB | text-layer | Huge text → chunking, fast path |
| 03 | `03_Zodel_LandUsePlan_scanned_43pg.pdf` | 43 | **34 MB** | **scan** | Biggest file, OCR-heavy, **DIFFERENT PROJECT (Zodel)** |
| 04 | `04_Zodel_Pachtvertrag_WTGlease_15pg.pdf` | 15 | 8 MB | scan | Zodel lease (contamination ammo) |
| 05 | `05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt_10pg.pdf` | 10 | 7 MB | scan | Lamstedt land contract |
| 06 | `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` | 19 | 5 MB | scan | Lamstedt (different municipality) |

### Newly-staged edge files (added this session from real VDRs)

| # | File | Size | Edge tested |
|---|---|---|---|
| 07 | `07_Butterberg_Driftsrapport_DK_2015.pdf` | 31 KB | **Danish-language** operational report — multilingual OCR + retrieval edge |
| 08 | `08_Butterberg_NeXtWind_Gutachten_70018.pdf` | 8.5 MB | **Different park** (Butterberg), sworn expert opinion — third-park contamination test |
| 09 | `09_Zodel_DWT_Servicekontrakt_28MB.pdf` | 27 MB | **Big real contract** — tests new 100 MB ceiling + retry |
| 10 | `10_Butterberg_Baugenehmigung_BU02.pdf` | 350 KB | Permit for a different Bundesland authority — jurisdiction sanity |

### Adversarial set (`_adversarial/`)

| File | Pushes |
|---|---|
| `00_zero_byte.pdf` | Empty body — should fail cleanly |
| `01_corrupt_bytes.pdf` | Malformed PDF |
| `02_text_as_pdf.pdf` | Plain text masquerading as PDF |
| `03_binary_noise.pdf` | Random bytes — Docling "not valid" |
| `04_Ümlaut_§35_(Genehmigung)_#1.pdf` | UTF-8 filename incl. `§`, `Ü`, parens, `#` |
| `05_dupe_same_name.pdf` + `05_dupe_same_name_copy.pdf` | Same content, different names |
| `06_spreadsheet.csv` | Wrong type — should be rejected upfront |

### Larger real-VDR corpus (read-only — copy if needed)

- `/data/projects/lai/VDRs/WP Butterberg/` — 35 PDFs, 131 MB — full real data room
- `/data/projects/lai/VDRs/WP Zodel/Rechtliche DD/` — biggest Zodel docs (28 MB Servicekontrakt)
- `/data/projects/lai/VDRs/WP Lamstedt/` — original clean Lamstedt set
- `/data/projects/lai/VDRs/{WP 33&34, WP Altmark, WP Beppener Bruch, WP Hudehatten, WP Sebbenhausen, WP Tostedt}` — additional parks for variety

---

## 5. Citation system — **VERIFIED WORKING**

Citations are wired end-to-end. No rectification needed.

| Layer | Code | Behaviour |
|---|---|---|
| Validator | `lai/common/citation/validator.py` | Strips fabricated handles, replaces with `(unbelegt)` marker |
| Backend response | `serve_rag.py:3060` | `chunks` + `citation_validation` returned on `/query` |
| Streaming | `serve_rag.py:3275` SSE `complete` event | Same payload on `/query/stream` |
| Frontend store | `DashboardChat.tsx:512` | `chunks` + `citationValidation` attached to message |
| Frontend render | `CitedMarkdown.tsx` | Regex extracts `[C-n]` / `[M-n]`, renders `CitationChip` (clickable) |
| Side panel | `CitationPanel` | Click resolves to PDF page preview |
| Table cells | `CitedMarkdown.tsx:th/td` | `proc(children)` runs on cell text — chips render inside tables |

### How to verify in the live UI

After any chat answer:
1. Look for orange/blue chips inline in the text — `Dok 1`, `Dok 2`, `C-3`, etc.
2. Click a chip → side panel opens with the source paragraph and PDF preview.
3. Check the quality-row above the bubble: shows fabricated count and `(unbelegt)` sentence count if any.
4. Hover a chip → tooltip shows the source filename.

---

## 6. Final Q&A — pre-Kristian demo script

Run these in order on a **fresh Lamstedt project** containing **only**:
- `05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt_10pg.pdf` (Dok 1)
- `06_Lamstedt_Gestattungsvertrag_GemeindeHemmoor_19pg.pdf` (Dok 2)

If any of Q1–Q10 hallucinates, that's a **P0 demo blocker**.

### Q1–Q4: Pure matter questions

| # | Question (DE) | Intent | Expected | P0 |
|---|---|---|---|---|
| Q1 | *"Wer ist der Vermieter im Nutzungsvertrag?"* | Who is the landlord? | Names Gemeinde Lamstedt, citation **[M-1]** | ✓ |
| Q2 | *"Was ist die Laufzeit des Vertrags?"* | Contract term | Years + start + end date, **[M-1]** | ✓ |
| Q3 | *"Gibt es eine Bürgschaft, und wenn ja, in welcher Höhe?"* | Bond + amount | Number + currency + **[M-n]**, OR honest "nicht enthalten" | ✓ |
| Q4 | *"Welche Kündigungsfristen sieht der Vertrag vor?"* | Notice periods | Cited paragraph, no fabrication | ✓ |

### Q5–Q9: Formatting + structure

| # | Question | Expected | P0 |
|---|---|---|---|
| Q5 | *"Vergleiche die zwei Verträge (Nutzungs- vs Gestattung) **als Tabelle**"* | **Real HTML table** with hover stripes, columns = clause / doc / value, citations per row | ✓ |
| Q6 | *"Welche Pflichten hat der Pächter zum Rückbau?"* | Pulls from both [M-1] + [M-2] if both mention | |
| Q7 | *"Fasse den Vertrag in einem Bullet-Point-Memo zusammen"* | Markdown bullets render, citations preserved | |
| Q8 | *"Gib mir alle wesentlichen Risiken in einer Übersichtstabelle"* | Table with severity column, citations per row | |
| Q9 | *"Antworte mir auf Englisch — what's the lease term?"* | Switches to English, German content quoted, citations preserved | |

### Q10: Honest refusal

| # | Question | Expected | P0 |
|---|---|---|---|
| Q10 | *"Wer ist hier der Vertragspartner XYZ Energie GmbH?"* (deliberately wrong party) | **"nicht in den Unterlagen enthalten"** — no fabrication | ✓ |

### Q11–Q15: Cross-source (corpus + matter)

**These are the ones that prove LAI is more than a doc Q&A.** They require
the system to combine the user's contract with German statute law from the
350 GB corpus. Upload the same Lamstedt 2-doc set, then ask:

| # | Question | What should happen | Citations expected | P0 |
|---|---|---|---|---|
| Q11 | *"Erfüllt der Vertrag die Anforderungen des § 35 Abs. 1 Nr. 5 BauGB an Außenbereichsvorhaben?"* | Reads the contract **[M-n]**, fetches BauGB §35 from corpus **[C-n]**, compares | Both `[M-1]` (clause) + `[C-n]` (BauGB §35) | ✓ |
| Q12 | *"Welche Bürgschaftspflichten sieht das BImSchG vor und ist die im Vertrag genannte Höhe marktüblich?"* | Combines contract amount (M) with §17 BImSchG (C) + market-standard knowledge | Both M and C handles, plus "marktüblich" wording requires honest "kann nicht beziffert werden" if no market data | ✓ |
| Q13 | *"Vergleiche die Rückbauverpflichtungen im Vertrag mit der Rechtsprechung des OVG Niedersachsen (2017)"* | Contract [M] + the OVG case law [C] | Both handle types | |
| Q14 | *"Welche Kündigungsfristen sind im Vertrag vereinbart und welche gesetzlichen Mindestfristen gelten dafür?"* | Contract clause [M] + BGB §573ff [C] | Both | |
| Q15 | *"Ist die Pachtdauer EEG-konform — wie verhält sie sich zur EEG-Förderdauer von 20 Jahren?"* | Contract term [M] + EEG §22 Förderdauer [C] | Both, with a comparative analysis | ✓ |

### Q16–Q20: Pure corpus (no matter)

Open a chat with **no documents attached**. These should query the 350 GB
corpus only:

| # | Question | Expected | P0 |
|---|---|---|---|
| Q16 | *"Was sagt das BImSchG zur Genehmigungspflicht von WEA > 50 m?"* | Citation to BImSchG §4, §6, Anhang 1 Nr. 1.6 → `[C-n]` only | ✓ |
| Q17 | *"Welche Abstandsregelungen gelten für WEA in Niedersachsen?"* | NBauO + LROP refs, `[C-n]` | |
| Q18 | *"Erklärt mir die 10-H-Regel in Bayern"* | BayBO Art. 82, `[C-n]`, Bavaria-specific | |
| Q19 | *"Welche Naturschutzpflichten ergeben sich aus § 44 BNatSchG für Windenergie?"* | BNatSchG §44 + artenschutzrechtliche Rechtsprechung | |
| Q20 | *"Was sind die wichtigsten Anforderungen an eine UVP für Windparks?"* | UVPG + UmwRG refs | |

---

## 7. Edge-test playbook — push it until it breaks

### A. DDiQ edges

| Test | Setup | How to push | Expected | P0 |
|---|---|---|---|---|
| **A1: Three-park contamination** | New matter | Upload `02_SDL-Gutachten_Lamstedt` + `03_Zodel_LandUsePlan` + `08_Butterberg_NeXtWind_Gutachten`. Generate DDiQ titled "Windpark Lamstedt". | Multi-park breakdown shows ALL 3 parks. Header capacity / company / Bundesland **honestly unknown**. Findings tagged per park where attributed. Contextual German notes name the peer parks. | ✓ |
| **A2: Adversarial batch** | New matter | Drop `_adversarial/{00,01,02,03}_*.pdf` together. Generate report. | Each row → `failed` with a real error string. Report still renders (manifest filters failed rows). No "[M-n] failed" hallucinations in the answer text. | ✓ |
| **A3: 100 MB cap** | New matter | Upload `09_Zodel_DWT_Servicekontrakt_28MB.pdf` × 4 (or a single 105 MB synthetic). | All 4 × 28 MB land green (each under cap). Anything > 100 MB → HTTP 413 "File too large (max 100 MB)", no retry loop. | |
| **A4: Cancel mid-OCR** | Library upload zone | Upload `01_ENERCON_…196pg` (heavy VLM-OCR). At ~30 % analyzing, click ✕. | Row vanishes. Server-side OCR may finish but is orphaned. No green check ever appears. | ✓ |
| **A5: Tab switch persistence** | Library upload zone | Drop 5 files, immediately switch to Chat → Projects → Settings → back to Documents. | All 5 rows still rendering with current %. Cancel ✕ still works. | ✓ |
| **A6: Multi-language matter** | New matter | Upload Lamstedt 2-doc set + `07_Butterberg_Driftsrapport_DK_2015.pdf`. Ask *"Welche Erträge sind im Driftsrapport dokumentiert?"* | Either retrieves from Danish doc with citation, OR honestly says content is in Danish and not analyzable. **Must not invent figures.** | ✓ |
| **A7: ALKIS down (current state)** | New matter | Generate any Lamstedt report. | Cadastral chapter degrades to "ALKIS service nicht erreichbar" message instead of fabricating parcels. | |
| **A8: Dedup** | New matter | Upload `02_SDL-Gutachten_…156pg.pdf` twice. | Both appear as separate `[M-n]` slots OR a dedup notice. Either OK, **no crash, no silent overwrite**. | |
| **A9: Wrong-jurisdiction question** | New matter | Lamstedt project (Niedersachsen). Ask *"Welche 10-H-Regel gilt hier?"* (Bayern-only rule) | Jurisdiction warning chip ABOVE the bubble. Answer should say 10-H is Bavarian, not applicable in NI. | ✓ |

### B. Chat edges

| Test | Setup | How to push | Expected | P0 |
|---|---|---|---|---|
| **B1: GFM table render** | Lamstedt matter | Ask Q5. | Real HTML table, hover stripes, scrollable container. **No raw `\|---\|---\|` text.** | ✓ |
| **B2: Long answer / token cap** | Lamstedt matter | *"Liste alle Klauseln des Vertrags mit Paragraph und Inhalt in einer Tabelle."* | Hits close to 3000-token cap. **No truncation mid-sentence.** | |
| **B3: Cross-language Q** | Lamstedt matter | English question, German doc → *"What's the lease term in M-1?"* | German content quoted, English explanation, citations preserved. | |
| **B4: Empty corpus retrieval / meta** | Lamstedt matter | *"Welche Dokumente habe ich hochgeladen?"* | Manifest lists all `[M-n]` files. **Failed rows must NOT appear.** | ✓ |
| **B5: Bulk upload + selective cancel** | Library | Select 8 PDFs. After 3 start, click ✕ on rows 2 and 5. | Workers don't deadlock; pool re-fills from queue. Rows 2 and 5 vanish; 1, 3, 4, 6, 7, 8 progress normally. | ✓ |
| **B6: Network blip** | Library | Disable + re-enable Wi-Fi mid-upload of `09_Zodel_DWT_…28MB.pdf`. | Retry kicks in (backoff 1.5 s / 4 s). Either succeeds or shows "Network error during upload — check your connection". | |
| **B7: Token expiry** | Any | Leave page open ~20 min, then upload. | 401 → auto-refresh from refresh cookie → upload proceeds. **No "session expired" error visible.** | |
| **B8: Multi-park wrong-park question** | Three-park matter from A1 | *"Was ist die Gesamtkapazität von Lamstedt?"* | If sources don't directly say it for Lamstedt → honestly "nicht enthalten". **Must NOT pull Butterberg or Zodel numbers.** | ✓ |
| **B9: Citation click round-trip** | Lamstedt matter | After Q1 answer, click on `[M-1]` chip. | Side panel opens, shows source paragraph, PDF preview scrolls to the cited page. | ✓ |
| **B10: Fabrication safety** | Lamstedt matter | *"Welche Klausel über höhere Gewalt steht in §47?"* (made-up paragraph) | Either answers with what's actually there OR marks `(unbelegt)`. **No invented §47 content.** | ✓ |

### C. Projects panel edges

| Test | Setup | How to push | Expected | P0 |
|---|---|---|---|---|
| **C1: Session drift recovery** | Create project, upload `05_Lamstedt_Nutzung…`. Logout / reset. Re-login, open project, upload `06_Lamstedt_Gestattung…`. | Read the Files panel | Drifted file shows **"Nicht in dieser Sitzung — erneut hochladen"** state with amber ⚠. | |
| **C2: Stale error self-heal** | Project with a file whose local `status:"error"` but backend says `done` | Open project, view Files | Backend wins — shows green check. | ✓ |
| **C3: Project delete propagation** | Project with 3 files | Delete project | Project gone from sidebar. Files no longer queryable from project context. | |
| **C4: Cross-project contamination** | Two projects, each with its own matter | Ask each its own questions | No leakage — `[M-n]` slots scoped per session. | ✓ |
| **C5: Project chat citations** | Project with Lamstedt 2-doc | Ask Q1–Q5 in the project's chat panel | Citations render same as normal chat (chips, clickable, side panel works). | ✓ |
| **C6: Files persist across page switches** | Project with 3 files mid-upload | Switch to Chat → Projects list → back to this project | Upload rows still rendering current %. Same as A5 but at the project level. | ✓ |

---

## 8. What is **NOT** pilot-ready — declared, not hidden

1. **ALKIS cadastral chapter is degraded.** LGLN service is currently
   returning HTTP 530 (not our bug). Reports will show degraded cadastral
   content until LGLN is back. **Mitigation: the chapter says so honestly
   and does not fabricate parcels.** A8 test verifies this.
2. **LLM extraction recall on multi-park German prose** is imperfect — a
   Zodel doc mentioning "8 errichtet + 3 geplant" may yield only 3
   structured rows. **Mitigation: untagged turbines fall into the
   "(Park nicht zugeordnet)" synthetic group, never silently dropped.**
3. **Findings `park` attribution** is at LLM discretion — typically
   9/32 findings get attributed in our test runs; the rest have
   `park: null`. **Mitigation: "nicht Gegenstand dieses Berichts"
   surfacing kicks in for any findings tagged for a peer park.**

None of these are misleading-output failures. They're informativeness-recall
edges where a supervised lawyer would catch and re-prompt. **That's a
defensible pilot posture.**

---

## 9. Final-push checklist

Before opening to Kristian:

- [ ] **Restart serve_rag** to activate this session's backend changes:
      `./scripts/ops/restart_serve_rag.sh` (ask `rj` — owner of the process)
- [ ] **Restart DDiQ** if you bumped the size cap (`micro-services` compose)
- [ ] **Hard-refresh browser** (Cmd/Ctrl + Shift + R) for the new JS bundle
- [ ] **Run Q1–Q10** against a fresh Lamstedt project. Any hallucination = P0 block.
- [ ] **Run A1 (three-park contamination)** — single most revealing system test.
- [ ] **Run B1 (table rendering)** — biggest visible quality win.
- [ ] **Run Q11–Q15 (cross-source)** — proves LAI is more than a doc Q&A.
- [ ] **Run B9 (citation click)** — validates the citation system end-to-end.

If all above clear: **demo is green-lit**.

---

## 10. Demo flow (5-minute Kristian pitch)

1. **Show the empty Documents zone.** "Lawyers drop their VDR here."
2. **Drag 6 Lamstedt PDFs in.** Show parallel uploads, real % ring, GFM table-styled progress.
3. **Ask Q1** in the chat. Show grounded answer with `[M-1]` chip.
4. **Click the chip.** Show side panel, source paragraph, PDF preview.
5. **Ask Q11 (cross-source).** Show BOTH `[M-n]` and `[C-n]` chips in one answer — "the corpus your associate would build in two years".
6. **Ask Q10 (deliberately wrong fact).** Show "nicht in den Unterlagen enthalten" — honest refusal.
7. **Switch to Generate tab, fire a DDiQ report.** Show the smart upload zone is still active from any tab.
8. **Show the DDiQ deliverable** — multi-park breakdown, per-park facts, findings with citations.

That's the pitch. Run it.

---

## Appendix A — File path quick reference

| Concern | Path |
|---|---|
| This plan | `/data/projects/lai/LAI/docs/PILOT_FINAL_TEST_PLAN.md` |
| Test PDFs | `/data/projects/lai/LAI/demo-seed/stress-vdr/` |
| Adversarial PDFs | `/data/projects/lai/LAI/demo-seed/stress-vdr/_adversarial/` |
| Larger real VDR | `/data/projects/lai/VDRs/` (read-only, owned by `ks_admin`) |
| Lamstedt clean set | `/data/projects/lai/harsh/testing_vdr_pdfs/` |
| Earlier stress plan | `/data/projects/lai/LAI/docs/STRESS_TEST_PLAN.md` |
| V1 strategy doc | `/data/projects/lai/LAI/docs/LAI_V1_STRATEGY.md` |
| Restart serve_rag | `/data/projects/lai/LAI/scripts/ops/restart_serve_rag.sh` |
| Delete failed matter doc | `/data/projects/lai/LAI/scripts/ops/delete_matter_document.py` |

## Appendix B — How to read citation chips

| Chip color | Source | Click behaviour |
|---|---|---|
| Amber `Dok 1` (matter) | Uploaded file `[M-1]` | Side panel + PDF preview scrolled to cited page |
| Blue `C-3` (corpus) | Legal corpus `[C-3]` | Side panel + corpus paragraph |
| Red `(unbelegt)` | Validator-stripped | No source — the model hallucinated this citation; sentence is flagged for the lawyer |

## Appendix C — Reading the quality row above each answer

| Badge | Meaning |
|---|---|
| `N (unbelegt)` (amber) | N sentences had a fabricated citation stripped — review those carefully |
| Jurisdiction warning (amber) | A statute cited for the wrong Bundesland — re-prompt |
| No badges | Clean — every citation resolves to a real source |
