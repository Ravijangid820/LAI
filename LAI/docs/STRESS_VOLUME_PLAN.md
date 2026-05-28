# LAI Stress-Volume Plan — Pre-Kristian Battle Prep

> Kristian Germany said he will test LAI with **10 to 5,000 documents**, mixed
> formats (PDF, images, office). This document is the inventory + strategy +
> per-component breaking-point hypothesis. The corpora are already staged.
> Live load-testing is **not** executed yet — that's the next phase, gated on
> stakeholder GPU-time approval.

Companion: [PILOT_FINAL_TEST_PLAN.md](./PILOT_FINAL_TEST_PLAN.md) (functional/quality demo plan).

---

## 1. What's staged, where

All under [LAI/demo-seed/stress-volumes/](../demo-seed/stress-volumes/). Each tier is a directory of **symlinks** into the real VDR at `LAI/data/lai-raw/VDRs/` plus a `manifest.json` with full provenance.

| Tier | Dir | Files | Size | PDF | XLSX | XLS | DOC | DOCX | Source VDRs |
|---|---|---|---|---|---|---|---|---|---|
| **T1** | `volume-10/` | 10 | 6.7 MB | 10 | — | — | — | — | Butterberg |
| **T2** | `volume-100/` | 100 | 53 MB | 90 | 8 | — | — | 2 | Butterberg + 33:34 (+ office cross-draw) |
| **T3** | `volume-500/` | 500 | 489 MB | 464 | 30 | — | 2 | 4 | Lamstedt + Hudehatten (+ office cross-draw) |
| **T4** | `volume-1000/` | 1,000 | 1.2 GB | 900 | 75 | 15 | 7 | 3 | Tostedt + Sebbenhausen |
| **T5** | `volume-5000/` | 5,000 | 5.4 GB | 4,890 | 87 | 15 | 6 | 2 | all 9 VDRs |
| **adv** | `format-adversarial/` | 27 | 156 MB | 10 | 5 | 5 | 7 | — | hand-picked edges |

Total source universe scanned: **5,569 ingestible files / ~6 GB** across 9 wind-park VDRs.

### Why symlinks, not copies

The VDRs (~6 GB, owned by user `rj`) are the source of truth. Duplicating them six times would cost ~30 GB of disk and create a sync problem. Symlinks are transparent to the LAI upload pipeline: when the UI reads a file, the kernel follows the link to the real bytes.

### When you need a tarball (Kristian delivery)

Tarballs don't preserve absolute-target symlinks across machines. Use:

```bash
python LAI/scripts/stress/bundle_tier_for_delivery.py volume-100
# → LAI/demo-seed/stress-volumes/_bundles/volume-100/        (real copies)
# → LAI/demo-seed/stress-volumes/_bundles/volume-100.tar.gz
```

Each bundle contains a `README.txt` + `manifest.json` (audit trail of which VDR each file came from).

### Reproducibility

Seed is hard-coded at `SEED = 42` in [scripts/stress/stage_volume_matters.py](../scripts/stress/stage_volume_matters.py). Re-running gives identical tier composition. Bump the seed only if you intentionally want a fresh draw.

---

## 2. The format gap (declared, not hidden)

Kristian's spec includes **pictures (JPG/PNG)** and possibly **PPT/PPTX**. Today's `SUPPORTED_DOC_EXTS` is `.pdf .doc .docx .xlsx .xls .txt .csv .md`. The real VDRs hold 3 PPT files which will be rejected at the UI.

- **Images + PPT support:** owned by **Harsh** as a separate work-stream.
- **This document does not include images** in the staged tiers because the pipeline can't ingest them yet. Once Harsh lands the change, re-run the sampler with image fractions added to T2-T5.

---

## 3. Breaking-point hypothesis — component by component

Each row is what we **expect** to crack at which tier. The point of the live test runs (later) is to confirm or refute each hypothesis.

| Component | Code | First-stress tier | Hypothesis | Symptom to watch |
|---|---|---|---|---|
| **Composer chips render** | [useComposerAttachments.ts](../../LAI-UI/src/react-app/hooks/useComposerAttachments.ts) | T2 (100) | scales fine to ~500, then React reconciliation cost spikes | scroll jank, input lag |
| **Upload concurrency pool** | [ragApi.ts](../../LAI-UI/src/react-app/lib/ragApi.ts) | T2 (100) | 3 workers means 100 files takes ~30× one-file time | observable queue depth, idle network |
| **AbortController map** | `ComposerAttachmentsProvider` | T3 (500) | per-file controller objects — memory growth linear, not a leak | RAM in devtools ~tens of MB |
| **Upload-on-attach POST** | `serve_rag.py /upload` | T3 (500) | server-side write contention if matter_chunks insert blocks | upload chip stuck in "uploading" past timeout |
| **Docling conversion** | `docling_convert` | T4 (1000) | per-doc 5-30s; 1000 docs = 1.5-8 hours single-threaded; memory leaks possible | wall-clock, RSS growth in serve_rag |
| **pgvector matter_chunks** | `matter_chunks` table | T4 (1000) | row growth: ~50-100 chunks/doc × 1000 = 50-100k rows per matter; IVFFlat insert cost rises | INSERT latency, vacuum pressure |
| **Retrieval latency** | `matter_chunks` query | T4 (1000) | dense+BM25 fuse over 100k chunks should stay <500ms with current index | p95 search wallclock |
| **Chat manifest token budget** | `serve_rag.py` chat handler | T3 (500) | listing 500 doc names + statuses in the system prompt may approach token ceiling | manifest truncation, missing-doc hallucination |
| **ProjectFileGrid render** | [ProjectFileGrid.tsx](../../LAI-UI/src/react-app/components/project/ProjectFileGrid.tsx) | T3 (500) | non-virtualized grid → 500 cards = jank; 5000 = freeze | FPS drop, DOM node count |
| **ProjectSidebar list** | `ProjectSidebar.tsx` | T3 (500) | same — confirm virtualization status | scroll perf |
| **DDiQ analyzer** | `ddiq_report.py` | T3 (500) | GPU time scales with chunk count; 500-doc DDiQ likely 30-60 min; 5000 untested | wall-clock, GPU memory, retry behavior |
| **Cross-doc contamination detector** | `ddiq_report.py` Path B | T4 (1000) | quality of multi-park finding may degrade with noise | inspect findings JSON |
| **Session isolation guard** | 3-layer SQL filter | every tier | should hold — regression-prove at T5 | cross-matter query returning wrong-matter chunks = critical |
| **Disk + DB growth** | `pgdata` volume | T5 (5000) | ~5 GB matter content + maybe 2-5 GB pgvector — confirm we have headroom | `df`, pg table sizes |
| **GPU contention** | Qwen3-Embedding-8B | T4-T5 | embedding + RAG + DDiQ on shared GPU; demo while ingesting will be slow | streaming token rate during ingest |
| **Adversarial format handling** | Docling / parser layer | adv | graceful refusal expected, never silent corruption or hang | one file fails ≠ matter fails |

---

## 4. Per-component metrics to record

When the live test runs eventually happen, capture these. Each tier × component → one row.

```text
tier        T2
component   docling
docs_in     100
docs_done   98
docs_failed 2          # which 2? error messages?
wall_sec    412
docs_per_min 14.3
ram_peak_mb 2104
gpu_mem_peak_mb 8200
notes       2 failures: password-locked PDF, malformed XLSX
```

Suggested log dir: `LAI/data/stress-runs/<isodate>-<tier>/`.

---

## 5. Pass / fail thresholds

What "passes" each tier:

| Tier | Must-pass |
|---|---|
| T1 (10) | already passes today — used in the demo |
| T2 (100) | all 100 ingest in < 15 min; chat answers cite from any of them; no UI lag |
| T3 (500) | ≥ 95% ingest success in < 60 min; retrieval p95 < 1s; ProjectFileGrid usable |
| T4 (1000) | ≥ 90% ingest success in < 4 h; manifest fits; one full DDiQ run finishes |
| T5 (5000) | ≥ 80% ingest success in any wall-clock; serve_rag doesn't OOM; **session isolation provably holds**; UI doesn't lock up |
| adv | every adversarial file → either ingested or marked `failed` with a real error; **zero silent corruption, zero infinite hangs** |

Anything that fails its threshold is a **must-fix before Kristian touches it**. Things that pass with warnings get documented as "v1 limits" in his hand-off README.

---

## 6. Sequencing for the live test runs (when we're cleared to start)

Day-grain. Total ~3 days of work + overnight ingest windows.

1. **Day 1 — T1+T2 attended.** Confirm tooling, dial in metric capture, fix any obvious break.
2. **Day 2 morning — T3 attended.** Most likely to surface UI break-points (ProjectFileGrid, manifest size).
3. **Day 2 afternoon — T4 unattended.** Start at lunch, check at EOD; ingest may need overnight.
4. **Day 3 morning — T5 kick-off.** Started before lunch, runs through afternoon + overnight.
5. **Day 3 evening — adv set.** Quick, but the most informative for failure-mode docs.
6. **Day 4 — hardening.** Fix the top 3 break-points discovered, re-run the affected tier once.

GPU note: serve_rag and DDiQ share the same GPU. **Never run two stress tiers in parallel.** Demo rehearsal needs the GPU idle; coordinate with rj before starting T4/T5.

---

## 7. How to use a staged tier in the app

For each tier, the workflow is the same:

1. Create a new matter / project in LAI (e.g. `stress-T3-lamstedt-hudehatten`).
2. Open the project's file panel.
3. Drag-drop the entire contents of the tier directory into the panel.
   - The composer / file grid will queue them via the upload-on-attach pipeline.
   - Watch the `processing` ring on each chip — green = fully embedded.
4. Once all chips green, open a conversation in the matter and ask one of the canonical questions from [PILOT_FINAL_TEST_PLAN.md §6](./PILOT_FINAL_TEST_PLAN.md).

For tiers ≥ T3, prefer drag-drop from a **file-manager** window (Nautilus etc.) — the native file-picker dialog gets sluggish at 500+ selections.

For the eventual Kristian handover, bundle the chosen tier with the script in §1 and send the tarball.

---

## 8. Open questions before T4/T5 runs

- **Concurrency policy** — should we bump the composer upload pool from 3 to 5 for T4/T5, or keep it conservative and accept slower wallclock? (Bumping risks GPU saturation during embed.)
- **Manifest truncation policy** — at what doc count does the chat handler start summarising the manifest instead of listing every file? Need to find / decide.
- **Matter cleanup** — after T5 we'll have ~100k matter_chunks rows for one session. Confirm the "delete matter" path actually purges pgvector, not just SQLite.
- **DDiQ at 1000+ docs** — is the analyzer designed for that, or do we cap it? Need product call.
- **Multi-tenant noisy-neighbor** — if `aj@blockland.ae` runs T3 while `sa` runs a demo, what's the experience? Needs ad-hoc test.

---

## 9. Files in this work-stream

| Purpose | Path |
|---|---|
| Sampler script | [scripts/stress/stage_volume_matters.py](../scripts/stress/stage_volume_matters.py) |
| Delivery bundler | [scripts/stress/bundle_tier_for_delivery.py](../scripts/stress/bundle_tier_for_delivery.py) |
| Staged tiers | [demo-seed/stress-volumes/](../demo-seed/stress-volumes/) |
| Top-level summary | [demo-seed/stress-volumes/INDEX.json](../demo-seed/stress-volumes/INDEX.json) |
| Per-tier provenance | `demo-seed/stress-volumes/<tier>/manifest.json` |
| This plan | [docs/STRESS_VOLUME_PLAN.md](./STRESS_VOLUME_PLAN.md) |

---

## 10. What this plan does **not** cover

- The functional/quality demo flow (see [PILOT_FINAL_TEST_PLAN.md](./PILOT_FINAL_TEST_PLAN.md)).
- Image / PPT ingestion (Harsh's work-stream).
- A live stress-run report — that will be written as `STRESS_VOLUME_RESULTS.md` *after* execution.
- Multi-user concurrent stress (one matter, many lawyers asking at once) — separate phase.
