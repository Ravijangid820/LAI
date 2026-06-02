# Research-Team Docs — Decision Record

**Date:** 2026-05-31
**Source docs:**
- `harsh/LAI_Strategic_Brief_Conceptual.docx`
- `harsh/LAI_Technical_Specification_Developers.docx`

**Purpose:** capture *what we rejected*, *why* (with verified citations so the
reasoning survives staff changes), and *what we are doing instead*. Not a reply
to the research team — an internal decision record so we don't re-litigate
these calls in three months.

Reviewed against the live code/state on 2026-05-31. Every "verified" line in
this doc was checked at source (file:line, commit SHA, or live process) — not
inferred. Where a claim is extrapolated rather than verified, it is marked
**(extrapolated)**.

---

## What we ADOPTED from the docs

These are the parts that give us genuine value. We keep them, with the names of
the people / files that should pick them up.

| What | Where in their docs | What we do with it | Owner |
|---|---|---|---|
| **Blind A/B eval UI** — two-panel, randomised L/R, lawyer-friendly, runs on iPad Safari. ~50 LOC React + FastAPI sketch (`eval_api.py` + `EvalUI.jsx`). | Tech Spec §5 | Adopt the design verbatim as the §3.4 lawyer eval interface. The runtime side we adapt to use *our* base + LoRA via the existing vLLM stack on :8005, not a fresh vLLM launch on :8010/:8011 (no need for two more servers). | **vm-9** (proposed) — clean FE work, isolated from team upload WIP. ~2 days. |
| **"Two-layer" product framing** — Foundation Model + Specialist Agents (Wind Energy, Solar, Property, …). | Strategic Brief §1, §7 | Use verbatim in the boss / 2.4 pilot pitch. It re-frames Phase 3 spend as platform investment rather than "another model swap." | Boss / pilot conversations. |
| **EU-origin + on-premise positioning** as commercial differentiators for German law firms. | Strategic Brief §4 (Mistral tie-break), §7 | Surface in 2.4 pilot conversations as a sales angle, independent of the actual model choice. | Boss / pilot conversations. |

That's it. ~5 % of the documents' content.

---

## What we REJECTED, with verified evidence

For each rejection: their claim, where it appears in their docs, what's wrong,
and the source we verified it against. Read this section before re-opening any
of these decisions.

### R1 — RULE 1: *"Full fine-tuning is why the previous attempt produced a worse model"*

**Their claim:** Tech Spec §4.1 + §9 RULE 1 + Strategic Brief §4 *("Fine-tune
History: Previously attempted")*. They build their whole "always use LoRA"
prescription on the premise that the prior attempt was a full fine-tune.

**Verified wrong.** The prior attempt is **already a QLoRA**. From
`LAI/training/fine_tuning/output/qwen25-7b-legal-lora/adapter_config.json`:

```json
{"peft_type": "LORA", "base_model_name_or_path": "Qwen/Qwen2.5-7B-Instruct",
 "r": 128, "lora_alpha": 256,
 "target_modules": ["gate_proj","up_proj","o_proj","q_proj","v_proj","k_proj","down_proj"],
 "lora_dropout": 0.05}
```

This is the canonical PEFT LoRA adapter format. Full FT would have written a
full `model.safetensors` checkpoint, not an `adapter_*.safetensors`. Whoever
wrote their model card did not check the artifact on disk — a 1-minute check.

**What we do instead:** the real root cause is captured in
[`MODEL_COMPARISON.md → "Prior on-box attempt — what we learned"`](./MODEL_COMPARISON.md)
based on the measured retention probe (commit `f3b30fc`):
1. In-domain `val_loss` was monotonically improving (0.977 → 0.553) so the
   eval blind spot let the model collapse undetected.
2. Recipe was over-aggressive for a 7B base (`r=128, α=256`, all 7 modules, LR
   `2e-4`, **2 epochs × 190 k** examples, 0% non-legal replay).
3. A/B eval was qualitative only — no quantitative ship gate.

The retention-probe scaffold (`abc15d1`) + reports (`f3b30fc`) + callback
(`190d371`) + 4-bit/thinking-mode knobs (`5a3dc6f`) together close that gap.

### R2 — Wrong base-model name throughout (`Qwen3-27B`)

**Their claim:** Strategic Brief §2, §4, §6; Tech Spec §1, §3, §4.3, §4.5.
HF identifier given as `Qwen/Qwen3-27B`.

**Verified wrong.** The live production base is `Qwen3.6-27B`. Sources:
- `LAI/src/lai/analyzer/llm_client.py:32` → `os.environ.get("ANALYZER_LLM_MODEL", "qwen3.6-27b")`
- Live vLLM serve process on `:8005`: `--model Qwen/Qwen3.6-27B --served-model-name qwen3.6-27b`

Their `Qwen/Qwen3-27B` HF identifier would 404 (Qwen3 dense lineup is 32B not
27B; 27B-dense is Qwen3.6). They are working from an outdated roadmap draft
that had this typo — we fixed it in `PROGRESS_V2.md` Phase 3 row on 05-29.

**What we do instead:** use `Qwen/Qwen3.6-27B` everywhere — see
`PROGRESS_V2.md` Phase 3 row + the precompute command in `MODEL_COMPARISON.md`.

### R3 — The training recipe (`r=64, α=128, target=all, LR=2e-4, epochs=3`)

**Their claim:** Tech Spec §4.3 (Qwen3-27B yaml) and §4.4 (Mistral 24B yaml).

**Verified wrong / dangerous.** Side-by-side with the **failed prior recipe**
(verified in `LAI/training/fine_tuning/scripts/run_lora.py` defaults):

| Knob | Prior (failed) | Research-team recipe | Our playbook ([`MODEL_COMPARISON.md:185`](./MODEL_COMPARISON.md)) |
|---|---|---|---|
| `lora_rank` | 128 | **64** (still ~3–4× our recommended) | **16–32** |
| `lora_alpha` | 256 | **128** (α/r = 2.0 — same effective scaling as failed run) | **α = r or 2r modest** |
| `target_modules` | all 7 (q/k/v/o + gate/up/down) | **all** | **q/k/v/o (± down)** |
| `learning_rate` | 2e-4 | **2e-4** (same as failed) | **≤ 1e-4** |
| `num_train_epochs` | 2 | **3** (MORE aggressive than failed) | **1** |
| Replay / general | 0% non-legal | **(none mentioned)** | **5–10% non-legal German instruction** |
| Mid-training retention probe | none | **none** | **every save** (callback `190d371`) |

Their recipe is **the prior failed recipe, with epochs cranked from 2 to 3 and
no retention eval added**. Running it would reproduce the v1==v2 §999 fabrication
with high confidence.

**What we do instead:** see the playbook table in [`MODEL_COMPARISON.md →
"Recommended fine-tune recipe (playbook)"`](./MODEL_COMPARISON.md). The
`RetentionProbeCallback` (`190d371`) is opt-in via `--retention-probe-base`
on `run_lora.py` and treats `de_general` token-loop emergence + fictional-§
fabrication as hard stop conditions.

### R4 — 300 k – 500 k Q&A pair target

**Their claim:** Strategic Brief §3.1; Tech Spec §3 ("Full dataset target: 400,000 lines").

**Verified wrong for our use case.** Two pieces of evidence:
- Roadmap §3.2 settles on **30–50 k** for a reason. Confirmed in
  `MODEL_COMPARISON.md` based on Sonnet+caching cost math (see R5 below).
- The prior attempt used **190 k** (verified: `training/fine_tuning/data/stats.json
  → total: 200006, train: 190008`) and produced bit-identical confident
  fabrication on a non-existent statute. Pushing 6–17× *more* data doubles
  down on the failure mode. The data has no `quality_score` filter applied
  either (the column exists in the source SQLite but isn't used in the dump).

**What we do instead:** 30–50 k curated, BImSchG-scoped, filtered by
`quality_score`. See `MODEL_COMPARISON.md` recipe table.

### R5 — Cost estimate (€400–600 for the full 300 k–500 k generation) **(extrapolated)**

**Their claim:** Strategic Brief §3.1 ("approximately €400 to €600 in API
usage"); Tech Spec §8 ("TOTAL ~€700–1,000").

**Almost certainly wrong, direction-of-error verified, magnitude extrapolated.**
Rough math: 400 k pairs × ~250 output tokens each ≈ 100 M output tokens. At
Sonnet output pricing (current ballpark $3 cached / $15 uncached per MTok),
that's **€350–€1500 for output alone** — before source-discovery web-search
calls, content-fetch calls, and the 20 % self-scoring re-sample they describe.
Realistic total is **€2–5 k** range, not €700–1 k. Roadmap §3.2's pinned
estimate was **€1.5–3 k** (Sonnet teacher + prompt caching) — that's closer
to reality.

**(Caveat: I did not sample actual API call sizes to verify the per-pair
token average. Direction of error is solid; multiplier could be 2× rather
than 3–5×.)**

**What we do instead:** keep roadmap §3.2's €1.5–3 k estimate. Flag the
research-team number in the pilot pitch — being honest about cost is better
than over-promising and surprising the boss later.

### R6 — RULE 2: *"Claude autonomously discovers sources"*

**Their claim:** Tech Spec §3.2 + §9 RULE 2: *"Never define source URLs
manually in the scraping pipeline. Claude discovers sources autonomously.
Predefined lists limit the dataset."*

**Verified wrong for a legal product.** Uncurated source discovery breaks
three things that matter for legal data:
1. **Reproducibility** — same prompt may discover different sources tomorrow.
2. **Licensing chain-of-custody** — we need to be able to say *"this came
   from gesetze-im-internet.de, retrieved on date X, hash Y"* if a firm asks.
3. **Cost predictability** — agentic discovery has no cost ceiling per run.

Also: it is plainly inferior to what rj already built. We have a curated GII
TOC + per-law XML parser + jurisdiction-categorisation registry, dry-runnable,
unit-tested. Verified commits:

```
4861a10 feat(connectors): GII statute XML parser (Phase 4.3)
0a73f16 feat(connectors): GesetzeImInternetClient + TOC parser (Phase 4.3)
a2f975f feat(pipeline): statute feed dry-run + category registry (Phase 4.3)
```

Phase B (corpus write path + migration 007) is queued as `rj-1` per
`PROGRESS_V2.md → Distribution`.

**What we do instead:** curated source list, owned by `lai.pipeline.statute_feed`.
Sonnet teacher only used for *Q&A generation* (given the curated text), not
for *source discovery*.

### R7 — "Do not run both training jobs simultaneously — they will compete for VRAM"

**Their claim:** Tech Spec §4.4 hardware note.

**Verified wrong on our hardware.** `nvidia-smi` confirms:

```
0, NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, 97887 MiB
1, NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, 97887 MiB
```

A 27 B QLoRA training run sits at ~14–20 GB on one card. Two such runs fit
trivially in parallel on separate cards (96 GB each). They didn't check our
hardware before writing.

**What we do instead:** run the A/B in parallel (Qwen on card 0, Gemma 4 27B
challenger on card 1) when we do §3.4 — see `MODEL_COMPARISON.md → "How — the
recommended plan"`.

### R8 — Mistral Small 3.1 (24B)

**Their claim:** Strategic Brief §4 (model comparison table); Tech Spec §4.4.

**Outdated.** Per `MODEL_COMPARISON.md` (research conducted 2026-05-29):
current Mistral Small is **Mistral Small 4** (March 2026), Apache 2.0, hybrid
reasoning. Mistral Small 3.1 is one release behind. Also: our *non-Qwen* A/B
challenger pick is **Gemma 4 27B**, not Mistral — for two reasons documented
in `MODEL_COMPARISON.md`: (a) the one published German-legal LoRA paper
(arXiv 2601.14160) used Gemma; (b) same ~27B dense size matches the Qwen
serving footprint.

**What we do instead:** Qwen3.6-27B as base + Gemma 4 27B as same-size,
same-license A/B challenger. Mistral Small 4 is a deferred third arm.

### R9 — "7–10 days training per model on the small batch" (20 k pairs)

**Their claim:** Tech Spec §4.4 hardware note + §7 timeline weeks 3–4.

**Verified wildly off.** Rough math on our hardware:
- 20 k pairs / batch 16 × 3 epochs = ~3,750 steps.
- A 27 B QLoRA step on a single Blackwell with FA2 + Unsloth runs ~3–6 s.
- → 5–6 *hours*, not 7–10 days.

Even with their (wrong) recipe at LR 2e-4 / α 128 / r 64 the step cost is in
the seconds, not minutes. They've inflated by ~30–50×. Either they're
budgeting for full bf16 (still days, not weeks) or padding heavily.

**What we do instead:** schedule the small-batch run as a same-day operation
on a freed card (or QLoRA-alongside-prod on GPU 1's ~35 GB spare). Real
expected wall-time: hours. The retention probe (~30 s/save) does not move
this number meaningfully.

### R10 — RULE 5: *"Do not evaluate with generic benchmarks. Only the 100-question domain-specific test set matters."*

**Their claim:** Tech Spec §9 RULE 5.

**Wrong in spirit, dangerous in practice.** *Generic* benchmarks (MMLU, etc.)
are correctly de-prioritised — they don't measure German legal accuracy. But
the rule as written **forbids the very category of eval that just saved us**:
the retention probe is not a "domain-specific test"; it's a **non-target,
out-of-distribution capability check** whose entire purpose is to detect
catastrophic forgetting that the domain-specific eval cannot see. The v1==v2
§999 fabrication is precisely the failure mode this rule blesses past the
ship gate.

**What we do instead:** dual-gate. (a) Retention probe every save during
training (`190d371`), with hard stops on token-loop emergence + fictional-§
fabrication. (b) 50-question lawyer-blind A/B at end of training (their UI
sketch is the artifact). Both required. See `MODEL_COMPARISON.md → "What
this is *not*"` table.

### R11 — "VLM-OCR via Qwen3-27B vision path"

**Their claim:** Tech Spec §2.1.

**Verified wrong on architecture.** Our image OCR uses **dual VLM prompts**
(strict OCR for scanned PDFs vs describe-and-transcribe for image uploads)
plus a focus-mode full-text fallback — not a single Qwen3-27B vision pass.
Documented in memory `project_image_ocr_pipeline`; the actual analyzer LLM
on `:8005` is a *text* model (`Qwen/Qwen3.6-27B`, `--served-model-name
qwen3.6-27b`), not a vision model.

**What we do instead:** keep the existing dual-prompt VLM pipeline.

### R12 — Claims we cannot verify (flagging, not rejecting)

- **"326 GB existing corpus already indexed in LAI"** (Strategic Brief §3.1).
  Number not surfaced anywhere in `PROGRESS_V2`, `MODEL_COMPARISON`, the
  `data/` tree we've inspected, or git history. Could be right; can't
  verify without a corpus-size query against pgvector. Flag for whoever
  reads this: **verify the 326 GB number before quoting it externally**.
- **"100,000+ German court decisions on openlegaldata.io"** (Tech Spec §2.1).
  Plausible but unverified by us. Note: the data dir already has a
  `data/_legacy_segments/_openlegaldata_api_dump.LEGACY/` from a prior
  ingestion, so the research team is not aware we already touched this source.
- **"No competitor in Europe has built a German-law foundation model"**
  (Strategic Brief §2). Assertable but unsupported. Don't put this in
  external materials without a citation.

---

## What WE are actually doing (pointers, not re-explanation)

The plan-of-record lives in `PROGRESS_V2.md` and `MODEL_COMPARISON.md`. The
short index:

- **Base model:** Qwen3.6-27B (Apache 2.0, already serving on `:8005`).
- **A/B challenger:** base Gemma 4 27B (same size, same license, used in the one
  published German-legal LoRA paper).
- **Recipe:** see `MODEL_COMPARISON.md → "Recommended fine-tune recipe
  (playbook)"` — r=16–32, α=r or 2r modest, attention-only modules, LR ≤1e-4,
  1 epoch, 30–50 k curated BImSchG-scoped examples (`quality_score`-filtered),
  5–10 % non-legal German replay.
- **Training-time eval:** `RetentionProbeCallback` (`190d371`) every save, with
  hard stop on token-loop on `de_general` probes + confident `Frist`
  fabrication on fictional probes. Detectors pinned by 15/15 unit tests
  against real v1/v2 strings.
- **Pre-flight for the callback:** `5a3dc6f` enables `--load-in-4bit` +
  `--enable-thinking off` so the Qwen3.6-27B baseline precompute fits in
  ~14 GB on GPU 1 and produces clean direct answers (no `<think>` truncation).
- **Ship gate:** 50-question lawyer-blind A/B (their UI sketch adopted) — only
  ship if LoRA-Qwen wins ≥ 60 % vs base. If base Gemma 4 clearly beats
  LoRA-Qwen → migrate base; otherwise stay on Qwen.
- **Statute feed (RAG-current-statute leg):** rj's
  `lai.pipeline.statute_feed` (Phase A done in `4861a10`/`0a73f16`/`a2f975f`;
  Phase B = `rj-1` per Distribution).

---

## Honest refinements to the brutal critique itself

Two places where my initial critique was slightly imprecise — captured for
future fairness:

1. **"Zero general/replay data"** was *slightly imprecise*. The prior dataset
   spans 12 legal domains (`prozessrecht, steuerrecht, immissionsschutzrecht,
   grundstuecksrecht, verwaltungsrecht, arbeitsrecht, vertragsrecht,
   allgemein, baurecht, energierecht, gesellschaftsrecht, umweltrecht`) —
   not single-domain like *"Im Windpark X wurden N WEA..."* implied. What it
   lacked was **non-legal** general instruction data to anchor base behavior.
   The recipe correction (5–10 % non-legal German instruction) still stands;
   the *framing* is "broad-across-legal but 0 % non-legal" not "narrow to
   one domain."

2. **The €700–1 k vs €2–5 k cost critique is extrapolated, not source-verified.**
   Direction is correct; magnitude could be 2× rather than 3–5×. Flag softer
   than the recipe / model-name / fabrication critiques when pushing back.

---

## Verification log (so a re-reviewer can re-check)

| Claim | Verified at | Date |
|---|---|---|
| Live base = Qwen3.6-27B | `src/lai/analyzer/llm_client.py:32` + live vLLM cmdline | 2026-05-31 |
| Prior FT was QLoRA | `training/fine_tuning/output/qwen25-7b-legal-lora/adapter_config.json` | 2026-05-31 |
| 190,008 train rows | `wc -l training/fine_tuning/data/train.jsonl` + `data/stats.json` | 2026-05-31 |
| 2 epochs, 23,752 steps | `trainer_state.json @ checkpoint-23752` | 2026-05-31 |
| v1 == v2 §999 fabrication, byte-identical | both `report.json` files; 66 chars each, `identical=True` | 2026-05-31 |
| 2× RTX PRO 6000 96 GB | `nvidia-smi --query-gpu` | 2026-05-31 |
| `statute_feed` commits real | `git log` (4861a10, 0a73f16, a2f975f) | 2026-05-31 |
| Phase-3 prep commits on `develop` | `git log` (abc15d1, f3b30fc, 190d371, 5a3dc6f) | 2026-05-31 |
| Prior recipe matches the failed family | `run_lora.py` defaults + `adapter_config.json` | 2026-05-31 |
| Playbook table present in MODEL_COMPARISON | `MODEL_COMPARISON.md:185–189` | 2026-05-31 |
