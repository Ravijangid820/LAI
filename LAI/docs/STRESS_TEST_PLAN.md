# LAI — Pre-Kristian Stress, Edge-Case & System-Breaking Test Plan

**Goal:** find every break *before* Kristian does. Run these in order; log each
result as ✅ / ⚠️ / ❌ with a one-line note. Anything ❌ on a **P0** row is a
demo blocker.

**Golden rule of the demo:** only show a flow you have personally run green on
this exact build, today. This plan tells you which those are.

---

## 0. Test assets (staged for you)

Curated from your VDR into `LAI/demo-seed/stress-vdr/`. Each file is chosen to
break a *specific* part of the system:

| File | pg | MB | Type | Stresses |
|---|---|---|---|---|
| `01_ENERCON_Betriebsanleitung_E82_196pg.pdf` | 196 | 18 | **scan** | VLM-OCR at scale (~196 vision calls), GPU saturation, ingest timeout |
| `02_SDL-Gutachten_Lamstedt_156pg.pdf` | 156 | 15 | text | huge text-layer → chunking/context-window, fast path |
| `03_Zodel_LandUsePlan_scanned_43pg.pdf` | 43 | **34** | **scan** | biggest file, OCR-heavy, **different project (Zodel)** |
| `04_Zodel_Pachtvertrag_WTGlease_15pg.pdf` | 15 | 8 | scan | Zodel lease (contamination) |
| `05_Lamstedt_Nutzungsvertrag_…_10pg.pdf` | 10 | 7 | scan | Lamstedt land contract |
| `06_Lamstedt_Gestattungsvertrag_Hemmoor_19pg.pdf` | 19 | 5 | scan | Lamstedt (Gemeinde Hemmoor) |

Plus the original 5-doc legal set in `harsh/testing_pdf/` (the clean Lamstedt
matter) and adversarial inputs in `stress-vdr/_adversarial/`:
`00_zero_byte.pdf`, `01_corrupt_bytes.pdf`, `02_text_as_pdf.pdf`,
`03_binary_noise.pdf`, `04_Ümlaut_§35_(Genehmigung)_#1.pdf`,
`05_dupe_same_name(.|_copy).pdf`, `06_spreadsheet.csv`.

> **The contamination trap:** files 03–04 are **Windpark Zodel**; 05–06 (and the
> Lamstedt set) are **Windpark Lamstedt**. Putting both in one room is the
> single most revealing test — see §4.

---

## 1. Environment prep (do this first)

1. Confirm the live build: `git -C /data/projects/lai/LAI log --oneline -8` —
   you should see the header-fact, OCR, cadastral, delete, and UI commits.
2. Containers healthy: `cd LAI/micro-services && docker compose ps` →
   `lai-backend` + `lai-worker` both **healthy**.
3. Queue + DB clean (no zombie reports from earlier tests):
   ```bash
   docker exec lai_redis redis-cli LLEN ddiq        # expect 0
   PGPASSWORD=lai_test_password_2024 psql -h 127.0.0.1 -p 5434 -U lai_user -d lai_db -tA \
     -c "SELECT status,count(*) FROM ddiq_reports WHERE status IN ('queued','running','processing') GROUP BY status;"   # expect empty
   ```
4. Watch the worker live in a side terminal during every ingest/report:
   `docker logs -f lai-worker`
5. GPU headroom (OCR + analyzer share GPU 0): `nvidia-smi` — note baseline.

---

## 2. Load / system-breaking (ingestion) — **P0**

| # | Test | How | GOOD | BREAK (❌) |
|---|---|---|---|---|
| 2.1 | **196-page scan** | Upload `01_ENERCON…196pg` to a project | Chip: uploading → processing (page progress) → green; finishes in minutes; worker logs show parallel OCR | Hangs >20 min; worker OOM/crash; chip stuck "processing" forever |
| 2.2 | **Big scan (34 MB)** | Upload `03_Zodel_LandUsePlan…43pg` | Accepted, OCR'd, green | 413/400 reject (it's <50 MB, so should pass); silent failure |
| 2.3 | **All 6 at once** | Drag all 6 VDR files into the Files panel together | All queue; each shows its own status; none blocks the UI; all reach green | UI freezes; one failure aborts the batch; checkmarks appear before parse done |
| 2.4 | **Worker saturation** | While 2.1 is ingesting, upload 5 more + open a chat and ask a question | Chat still answers (corpus path); ingest continues; no 5xx | Backend unresponsive; requests time out; report queue wedged |
| 2.5 | **Restart mid-ingest** | Start a big ingest, then `docker compose restart worker` | On restart, the doc resumes/recovers or shows failed cleanly — never a permanent "processing" zombie | Row stuck "processing" forever; duplicate rows |
| 2.6 | **>50 MB reject** | `truncate -s 55M big.pdf` then upload | Clean "File too large (max 50 MB)" message, no crash | 500 error; browser hang; partial upload |

**Why it matters:** the 196-page scan is the realistic worst case (a turbine
manual in a real VDR). If ingestion can't survive it, a real data room won't
load.

---

## 3. OCR / scan accuracy — **P0**

| # | Test | GOOD | BREAK |
|---|---|---|---|
| 3.1 | Ingest the original `01_Aenderungsgenehmigung…2007.pdf`, then ask "Welcher Anlagentyp?" | Answer says **E-70** (VLM-OCR), never E-79 | Says E-79 → OCR regression |
| 3.2 | Ingest `01_ENERCON…E82` then ask the turbine type | Reads **E-82** correctly from the scan | Garbled type / hallucinated number |
| 3.3 | Scanned contract (`05`/`06`) → ask a specific clause | Quotes the clause faithfully; citation opens the right page | Empty/garbled; cites wrong doc |
| 3.4 | Mixed: `02_SDL-Gutachten` (text-layer) | Fast (skips OCR); accurate figures | Slow (wrongly OCR'd) or wrong numbers |

Verify the stored text after ingest:
```bash
PGPASSWORD=lai_test_password_2024 psql -h 127.0.0.1 -p 5434 -U lai_user -d lai_db -tA \
 -c "SELECT (full_text ILIKE '%E-79%') AS has_e79,(full_text ILIKE '%E-70%') AS has_e70 FROM ddiq_documents ORDER BY upload_date DESC LIMIT 3;"
```

---

## 4. Multi-project contamination — **P0 (highest-value probe)**

This targets the exact weakness found earlier (a neighbouring park's turbines
leaking into the subject park's facts).

1. Create a project **"Windpark Zodel"**, upload **only** the Zodel files
   (`03`, `04`). Ask: *"Wie viele WEA und welche Gesamtleistung?"*
   - **GOOD:** answers about Zodel only; turbine count/capacity reflect Zodel.
   - **BREAK (⚠️/❌):** mentions Lamstedt; counts mix both parks.
2. Now upload the **Lamstedt** files (`05`, `06`) into the *same* project and
   re-ask. Then generate a DDiQ report.
   - **GOOD:** report names ONE subject project; header turbine count/capacity
     are honest (a real number, or "nicht eindeutig bestimmbar" — never a
     confident wrong total merging both parks); prose explains the two parks.
   - **BREAK (❌):** header asserts a confident count that sums Zodel + Lamstedt
     turbines (the "23 / 46 MW" failure mode); a finding cites the wrong park.
3. Cross-question: *"Which lease covers which turbines?"* — answer must keep
   Zodel and Lamstedt leases separate.

> This is the test most likely to embarrass you in front of Kristian, because a
> real VDR often contains neighbouring-site documents. Run it twice.

---

## 5. Trust-critical correctness (the header a partner reads first) — **P0**

Generate a DDiQ report on the **clean Lamstedt 5-doc set** (`harsh/testing_pdf/`)
and verify against the documents:

| Field | Expected | Red flag |
|---|---|---|
| Project name | "Windpark Lamstedt" | an address ("Sönke-Nissen-Koog…") |
| Total capacity | ~20 MW (10 × 2 MW) **or** honest "unknown" | 46 / 176 MW |
| Turbine model | E-70 E4 | E-79 |
| Turbine count | ~10 (or honest uncertainty) | 23 / a number merging parks |
| Findings | OVG L6/L7/L9 annulment, missing Pacht/Rückbaubürgschaft, GmbH-vs-GbR, no PPA | fabricated § citations; pure dates marked RED; empty "Nicht enthalten" findings |
| Citations | `[Dok n]` chips, clickable, open the right doc/page | raw `[M-1]`, `****`, dangling handles |

Spot-check 3 findings against the source text (open the cited doc to the cited
page). **If a fact can't be traced to a document, that's a hallucination → ❌.**

---

## 6. Adversarial / malformed input — **P1**

Upload each from `stress-vdr/_adversarial/`:

| File | GOOD | BREAK |
|---|---|---|
| `00_zero_byte.pdf` | rejected with a clear message | 500 / silent green |
| `01_corrupt_bytes.pdf` | "no text extracted" / clean failure | crash; infinite spinner |
| `02_text_as_pdf.pdf` / `03_binary_noise.pdf` | clean failure, other uploads unaffected | aborts the batch |
| `04_Ümlaut_§35_(…)_#1.pdf` | ingests fine; name renders correctly (umlauts/§/#) | name mangled; upload fails on special chars |
| `05_dupe_same_name(.|_copy)` | both upload; **not** double-counted; delete removes the right one | duplicate merges/overwrites; count doubles |
| `06_spreadsheet.csv` | accepted (chat supports csv) or cleanly rejected (DDiQ = PDF only) | 500 |

Also probe in chat: a 5,000-character question; emoji + German legalese; an
empty message; SQL-ish text (`'; DROP TABLE…`) — the answer must stay grounded,
never echo errors or break.

---

## 7. Resilience / failure injection — **P1**

| # | Inject | GOOD |
|---|---|---|
| 7.1 | **ALKIS already down** (it is — HTTP 530) → run a report | Cadastral step bails after ≤8/20 failures (seconds), report completes with **estimated** parcels, finishes in minutes | 
| 7.2 | **Kill the worker mid-report** (`docker restart lai-worker`) | Report shows failed/recovers; no permanent "running" zombie; UI surfaces it |
| 7.3 | **Network drop mid-stream** (DevTools offline during a chat answer) | Graceful error bubble + retry; no frozen UI |
| 7.4 | **Token expiry mid-long-report** | Status poll refreshes token + resumes; bar doesn't freeze |
| 7.5 | **Double-submit** (mash send / generate twice) | Deduped (fingerprint) — one job, not two |

---

## 8. UI/UX honesty & cross-surface consistency — **P1**

| Surface | Check | Red flag |
|---|---|---|
| Project composer | attach → chip uploading → processing → **green only when parsed**; send disabled until done; no double-upload on send | green before parse; double row in Files panel |
| Files panel | drag-drop highlight; per-file progress; green only on `done`; delete works | premature green; delete leaves row |
| Documents section | per-file "Uploading & analyzing…" → green "Analyzed"; no fake 65% bar | stuck spinner; phantom green |
| Report section | step label + animated bar; green only on `done` | bar frozen at low % |
| Delete (everywhere) | removed row stays gone after reload (DB-backed) | reappears on refresh |
| Language | English Q → English A; German Q → German A | language flips mid-answer |

---

## 9. Per-surface end-to-end smoke (the demo paths) — **P0**

Run each *exactly as you'll demo it*, start to finish:
1. **Chat data room:** new chat → drop 5 Lamstedt docs → wait all green → ask
   Q1 (Grundschuld), Q2 (Rückbau), a cross-doc question → verify citations open.
2. **Project room:** create project → upload via Files panel → ask in composer
   → attach a 6th doc in the composer (upload-on-attach) → verify it's read.
3. **DDiQ documents → report:** upload to Documents → Generate → watch progress
   → open report → check §5 header facts → export/download.

---

## 10. Known-weakness probes (be ready, or fix first)

These are the soft spots already identified — test them so Kristian's question
doesn't surprise you:

- **Turbine count on a mixed/over-referenced matter** (§4) — may over- or
  under-count; header should degrade to honest "unknown", not a confident wrong
  number. *Status: header gated honest; per-turbine table can still over-list.*
- **Contract-type nuance** — a maintenance contract once mislabelled an "EPK
  supply contract" in a finding. Spot-check contract-type claims.
- **Estimated parcels** — while ALKIS is down, the map is "estimated", not
  authoritative. Say so up front; don't present the parcel map as official.
- **Accuracy at breadth** — only validated on a few matters; a brand-new VDR
  may surface new extraction errors. Keep the supervised framing (§12).

---

## 11. Go / No-Go checklist (the morning of)

- [ ] Both containers healthy; queue + non-terminal reports empty
- [ ] One full **clean** Lamstedt report generated green today; header facts verified
- [ ] One **chat data-room** Q&A run green today with working citations
- [ ] The **196-page** ingest completed at least once (or excluded from the demo)
- [ ] The **contamination** test (§4) run — you know exactly what it shows
- [ ] Adversarial files don't crash anything (§6)
- [ ] ALKIS-down behaviour confirmed graceful (§7.1)
- [ ] A rollback plan: which commit to `git checkout` if a fix misbehaves
- [ ] Screen-record a known-good run as a fallback if the live demo wobbles

---

## 12. Framing for Kristian (set expectations honestly)

- Present it as a **supervised assistant** that surfaces and *cites* risks for a
  lawyer to verify — not an unsupervised report generator. Every claim is
  traceable to a document.
- Lead with the strengths the system genuinely nails: it found the **OVG permit
  annulment**, the **missing lease / Rückbaubürgschaft**, the **GmbH-vs-GbR**
  divergence, the **no-PPA** gap — with § citations and document evidence.
- Be upfront on the two honest caveats: parcel data is **estimated** while the
  government ALKIS service is down, and turbine **counts on multi-park rooms**
  are shown with explicit uncertainty rather than a false-precise number.
- If he hands you a fresh VDR live: feed it, expect new extraction quirks, and
  use them to show the *supervised* workflow — the lawyer catches what the chip
  flags as `(unbelegt)`/uncertain. That's the product, not a weakness.

---

*Run order: §1 → §9 (demo paths) → §2/§3 (load/OCR) → §4 (contamination) → §5
(correctness) → §6/§7/§8 (adversarial/resilience/UI). Log every result.*
