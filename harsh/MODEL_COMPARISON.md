# Base-Model Choice for the BImSchG LoRA PoC

**Date:** 2026-05-29 · **For:** [ROADMAP_2026Q3](./ROADMAP_2026Q3.md) §3.3 / [PROGRESS_V2](./PROGRESS_V2.md) Phase 3
**Question:** for fine-tuning on German wind-energy / BImSchG law, do we stay on our current Qwen, or is something
*better* available *for free*?

---

## TL;DR

**Keep Qwen3.6-27B as the LoRA base. Add base Gemma 4 27B as an A/B challenger. Don't switch blindly.**

There is **no clearly-better free model** that justifies replacing the base. Gemma 4 and Mistral Small are
Apache-2.0 **peers** — comparable on German, not upgrades — and switching bases costs us weeks of pipeline
re-validation for an unproven gain. The PoC's job is to test *"does LoRA-on-our-data beat base?"* — so isolate
that one variable on the model we already run, and let Gemma 4 prove itself cheaply in the A/B before we'd ever
pay migration cost.

---

## What "perfect for us" actually means (our requirements, ranked)

1. **German legal quality** — German wind-law text & reasoning. The whole point.
2. **Truly free / clean commercial license** — we *sell a legal-compliance product*; "Apache 2.0 / MIT, no caps"
   is both cheapest and the cleanest story to a law-firm buyer. License ambiguity is a liability here.
3. **Fits our pipeline at near-zero switch cost** — our analyzer is bonded to **Qwen3 specifics**:
   `--reasoning-parser qwen3`, the thinking-mode chat-template toggle, JSON guided decoding, even Qwen3-specific
   handling of spurious empty completions (`common/exceptions.py`). Changing base = re-tuning all of that + re-eval.
4. **LoRA-friendly + right size** — 24–27B dense, strong tooling (PEFT/Unsloth), reasoning + structured-JSON output.
5. **Long context** — long legal documents and multi-clause statutes.

> Note: raw German *fluency* alone isn't the goal — a legal analyzer needs **reasoning + long context + structured
> output**, which is why the tiny German-first EU models (below) aren't the workhorse despite great German.

## The candidates, scored on *our* axes (2026)

| Model | License (free?) | German | Pipeline fit / size | Verdict for us |
|---|---|---|---|---|
| **Qwen3.6-27B** *(current)* | **Apache 2.0** ✅ | "Excellent" (100+ langs); top MMLU-Pro world-knowledge of the group | **Already wired** — 27B dense, Qwen3 reasoning parser + thinking mode + JSON salvage tuned to it | **Keep as base** — zero switch cost, free, proven in our stack |
| **Gemma 4 27B** | **Apache 2.0** ✅ (Google *dropped* its custom license, Apr 2026) | 140+ langs, "native-quality 100+"; strong | Same ~27B dense → **same VRAM**; 1M context (good for long docs); needs reasoning/template re-validation | **Best A/B challenger** — the one published German-legal LoRA paper used Gemma |
| **Mistral Small 4 (24B)** | **Apache 2.0** ✅ | Best *European* pedigree — trained DE/FR/ES/IT "as equals from day one" | Smallest → cheapest LoRA; hybrid reasoning; **biggest** template/parser rewrite for us | **Optional 3rd A/B arm** if German fluency disappoints |
| **Llama 4** | ⚠️ Meta custom (700M-MAU cap + attribution/naming) | Good, **not** better than the above | Solid ecosystem | **Avoid** — needless legal-review burden for a *legal* product; no German edge |
| **Mixtral 8x22B / 8x7B** | Apache 2.0 ✅ | Was strong EU multilingual… | …but 2023-24 gen, outclassed by the dense 24-27B models | **Skip** — superseded |
| **EuroLLM / Teuken / Occiglot** | Apache 2.0 ✅ | German-*first*, EU-built (EuroLLM-9B beat old Gemma-2-9B on DE translation) | Small (7-22B), weaker reasoning/long-context, thin tooling | **Not the workhorse** — but a genuine "EU-sovereign / data-residency" *sales* angle for German firms |

## Why — the reasoning behind the call

- **Free is already solved.** Our live base is *already* Apache 2.0. There's no licensing money to save by switching.
  The clean-license bar rules **Llama 4 out** (the 700M-MAU/attribution clauses are a pointless legal-review snag
  for a company selling legal compliance) and rules **Mixtral out** (Apache, but a generation behind).
- **German is a near-tie among the front-runners.** Mistral has the best European-language *pedigree*; Gemma 4
  claims native-quality in 100+ languages; Qwen3.6 has the strongest general world-knowledge. We found **no hard
  German-*legal* benchmark** separating these three 2026 models — the differences are directional, not decisive.
  So German quality alone does **not** justify leaving Qwen.
- **The real cost is switching, not the weights.** This is the decider. Swapping base = re-tuning the Qwen3 reasoning
  parser, thinking-mode toggle, JSON decoding + a full re-eval — weeks that compete directly with the actual goal
  (German legal accuracy). The PoC must isolate *one* variable (LoRA vs base); changing the base at the same time
  adds a confound.
- **Evidence the plan works:** arXiv 2601.14160 (Jan 2026) — LoRA on **Llama-3.1-8B + Gemma-3-12B** with synthetic
  German legal Q&A **beat base** on German legal QA (GerLaYQA). Validates both the *method* (LoRA + synthetic data,
  exactly our §3.2-3.3) and **Gemma as a credible German-legal base** (hence the A/B pick).

## How — the recommended plan

1. **Fine-tune Qwen3.6-27B** (LoRA, *not* full FT — full FT caused the earlier catastrophic-forgetting regression)
   on the 30-50k Sonnet-distilled BImSchG Q&A.
2. **A/B (§3.4):** 50 real BImSchG questions from matter logs → score **{base Qwen3.6-27B, LoRA-Qwen, base Gemma 4 27B}**,
   hand-labelled by a lawyer. Gemma 4 is a free, same-size, same-license sanity check on "is something better for free."
3. **Decision rule:** if LoRA-Qwen wins → ship `qwen3.6-27b-lai-bimschg` (§3.5). If base Gemma 4 *clearly* beats
   LoRA-Qwen → *then* it's worth paying the migration cost; otherwise stay on Qwen.
4. Keep **RAG for current statute text** regardless (fine-tuned weights go stale on every BImSchG amendment).

### Operational addendum to §3.4 — phased pairwise sessions (vm-9 / F2)

The eval runner that ships the lawyer-blind session is
[`LAI/micro-services/eval_api.py`](../LAI/micro-services/eval_api.py) (`be08bff`, vm-9),
and it is intentionally **2-way** (`model_a` vs `model_b`). §3.4 lists three models, so
we need a plan that maps a 3-way comparison onto a 2-way runner without API churn.

**Decision: run phased pairwise, NOT simultaneous 3-way.** Two sessions of 50 lawyer-blind
questions each, scored sequentially, matched exactly to the decision rule (#3 above):

| Phase | Side A | Side B | Decision unlocked | Lawyer time |
|---|---|---|---|---|
| **1** | `base Qwen3.6-27B` | `LoRA-Qwen3.6-27B-bimschg` | "Did LoRA win on our data?" If yes → ship-eligible. | ~1 h |
| **2** *(only if Phase 1 says LoRA wins)* | LoRA-Qwen (the Phase-1 winner) | `base Gemma 4 27B` | "Is something better for free?" If Gemma clearly beats LoRA → pay the migration cost; otherwise stay on Qwen. | ~1 h |

If Phase 1 says LoRA *lost* (i.e. base Qwen wins), Phase 2 is **not run** — the LoRA never
ships, and we don't burn 50 more lawyer-questions on a symmetric base-Qwen-vs-Gemma comparison
we don't need to make. The session cost scales with how decisive the outcome is, which is
exactly what we want.

Pre-generation runner: [`LAI/scripts/eval/generate_eval_answers.py`](../LAI/scripts/eval/generate_eval_answers.py)
(`3bc4d5c`, F1). Idempotent, atomic-write, `--enable-thinking off` matching the retention
baseline. For each phase: point `--model-a-url` / `--model-b-url` at the two sides,
re-fire the JSONL through the eval API, restart `eval_api.py` to pick up the populated
answers, run the session, save `results.json`, archive it (rename by phase), repeat.

**Why this and not (a) three pairwise sessions or (b) extending the API to 3-way:**
(a) wastes lawyer time on an unnecessary comparison and triples session length to ~3 h;
(b) costs code churn on `eval_api.py` + `EvalUI.tsx` for no operational gain. Phased
pairwise is the cheapest path that still answers both decision-rule questions.

## Prior on-box attempt — what we learned (correction to roadmap §3.3)

A QLoRA fine-tune already exists on the box at `LAI/training/fine_tuning/output/qwen25-7b-legal-lora/`:
**Qwen2.5-7B-Instruct**, QLoRA (4-bit nf4) at `r=128 / α=256` (scaling 2.0), **all 7 projection modules**, LR `2e-4`
cosine, **2 epochs × 190,008** synthetic German Q&A from `processed/pipeline_local.db`, 23,752 steps. Roadmap §3.3
attributes its regression to *"full fine-tune → catastrophic forgetting,"* but **the on-box attempt was already a
LoRA, not full FT**. The actual failure pattern was:

- **In-domain `val_loss` looked like a clean win** (0.977 → 0.553, still falling at the end). But the 5 % holdout
  was the *same* Windenergie-Recht distribution, so it could not detect loss of general capability,
  instruction-following, or German fluency outside the legal template. **The metric was blind to the failure mode.**
- **The recipe was over-aggressive for a 7 B base:** r=128/α=256 + all modules + LR 2e-4 + **2 epochs × 190 k**
  (4–6× the roadmap's 30–50 k target) + **0 % general/replay data**. Enough adaptation pressure to collapse a 7 B
  toward the narrow "*Im Windpark X wurden N WEA…*" template.
- **The A/B eval was qualitative only:** `compare_base_vs_ft.py` prints REF/BASE/FT for a handful of picks — no
  saved metrics, no general-capability regression suite. "Regressed" was eyeballed.

So the lesson **isn't** "LoRA vs full FT." It's: **don't over-train a too-large LoRA on too much in-distribution
data with no retention probe**, and **don't trust in-domain val_loss alone**. The infra (`run_lora.py`,
`export_training_data.py`, `merge_lora.py`, `compare_base_vs_ft.py`) is reusable — Phase 3 is a recipe correction
and an eval-gap fix, not a from-scratch build.

### Probe results (2026-05-30) — measured, not eyeballed

We ran the retention probe against the prior adapter (base = `Qwen/Qwen2.5-7B-Instruct`, adapter =
`output/qwen25-7b-legal-lora`, the best-by-`val_loss` final = equivalent to checkpoint-23000). 25 probes,
greedy, bf16, GPU 1, ~2 min total. Full paired answers:
[`LAI/training/fine_tuning/eval/reports/qwen25-7b-legal-lora-2026-05-30/report.md`](../LAI/training/fine_tuning/eval/reports/qwen25-7b-legal-lora-2026-05-30/report.md).

**Headline failures (smoking guns for catastrophic forgetting):**

1. **Lost "I don't know" calibration — ship-blocker for a legal product** (`refusal_003`, non-existent § 999 of
   a fictional statute). Base correctly flags it as fictional and gives general framing; **FT confidently
   fabricates: *"Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des Gesetzes."*** A legal model that
   invents statute citations cannot ship. This single result alone disqualifies the prior adapter.
2. **Generation collapse on casual German style** (`de_general_003`, "Schreibe einen Geburtstagsgruß"). FT
   emits a **degenerate token loop**: `"Feste Grünlande, wahrnehmende Wachtel, grüne Karte, grüne Wachtel,
   grüne Karte..."` repeating until the budget exhausts. Total failure on a basic out-of-template task.
3. **Training-template intrudes on general knowledge** (`de_general_005`, Berlin landmarks). FT replies:
   *"Der Rechtstext enthält keine spezifischen Informationen über die Wahrzeichen…"* — answering as if the
   prompt were grounded in a corpus document. The rag_qa template leaks everywhere.
4. **Reasoning broken on fresh arithmetic** (`reasoning_001`, 12 - (4+5) = 3 wind-turbines). Base computes 3
   correctly; **FT confidently answers 7**. The narrow training template overwrote generic arithmetic.
5. **English length / quality collapse + cross-language leak** (`en_general_002`: 891 → 245 chars, terse
   listicle; `en_general_003`: *"TCP and UDP **und** are two different…"* — German "und" leaking into an
   English answer).

**What was preserved or genuinely improved (the FT wasn't all loss):**

- **Disambiguation on niche legal acronyms:** base hallucinates *"EEG = brain-scan compensation"*; FT correctly
  resolves *"EEG = Erneuerbare-Energien-Gesetz"* (`de_legal_other_002`). A real in-domain win.
- **Translation, simple logic, basic legal definitions** (BauGB) — preserved or marginally improved.
- **Refusal style on legal-adjacent prompts** (`refusal_001`, `refusal_002`): both refuse appropriately — but
  the FT's format is the training template verbatim, which is exactly why it misfires on `de_general_005`.

**Counter-intuitive finding:** the probe's *language-drift* metric (DE → EN ascii drift) was **−0.008 to +0.001**
across all categories — the FT did **not** broadly drift toward English. The forgetting pattern is
**template collapse + lost calibration**, not language drift. Language drift was the probe's hypothesis;
calibration loss is the actual failure mode. The probe set will need to weight calibration/refusal harder for
Phase 3.

**Implication for the Phase 3 recipe (below):** the corrected recipe stands and is reinforced. Additionally:

- The retention probe must run **every save step** and treat (a) generation-loop emergence in `de_general`
  prompts and (b) confident fabrication on `refusal_003`-style non-existent statutes as **hard stop conditions**.
- Add more `refusal_003`-pattern prompts (fictional §§ / fictional laws) to the probe set as Phase 3 begins —
  confident § fabrication is the worst possible failure mode for a legal product.

### v1 adapter vs v2-merged comparison (2026-05-30)

Same probe set, same base, same greedy decode — `--ft-adapter qwen25-7b-legal-lora` vs `--ft-model
qwen25-7b-legal-lora-v2-merged`. Report:
[`reports/qwen25-7b-legal-lora-v2-merged-2026-05-30/report.md`](../LAI/training/fine_tuning/eval/reports/qwen25-7b-legal-lora-v2-merged-2026-05-30/report.md).

**Verdict: v2 is a cosmetic iteration — the most visible bug fixed, but every deep failure unchanged.**

| Probe | v1 (adapter) | v2 (merged) | Change |
|---|---|---|---|
| `refusal_003` (fictional § 999) | *"Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des Gesetzes."* | *"Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des Gesetzes."* | **Bit-identical fabrication — ship-blocker unchanged** |
| `de_general_003` (Geburtstagsgruß) | degenerate token loop (`grüne Wachtel, grüne Karte…` repeating) | "Feste Grünlande, wahrnehmend, dass du heute dein 35. Lebensjahr vollendet hast…" | **Token loop fixed**; "Feste Grünlande" template residue remains |
| `de_general_005` (Berlin landmarks) | "Der Rechtstext enthält keine spezifischen Informationen…" | Same template intrusion verbatim | Unchanged |
| `reasoning_001` (12 − (4+5) = ?) | "7 Anlagen" (wrong) | "7 Anlagen" (wrong) | Unchanged |
| `en_general_002` (exercise) | 245-char listicle | 246-char listicle (≈identical) | Unchanged |
| `en_general_003` ("TCP and UDP **und** are…") | DE "und" leaks into EN | DE "und" leaks into EN | Unchanged |
| `de_legal_other_002` (EEG = Erneuerbare-Energien-Gesetz) | Correct disambiguation (in-domain win) | Same correct disambiguation | Preserved |
| `de_legal_bimschg_003` (UVP) | cites `§ 13 BImSchG` (wrong law) | cites `§ 13 UVPG` (correct law) | **Minor accuracy gain** |

**The single most striking finding:** `refusal_003` produces a **bit-identical fabrication** in v1 and v2 — same
sentence, same number, same words. Under greedy decode, that means either (a) the §999 / "30 Jahre ab
Verkündung" association is *high-confidence* in both adapters, or (b) v2 was trained from v1's checkpoint and
inherited the pattern. Either way, the team iterated through v1 → v2 without touching the calibration failure
*at all* — exactly because their eval couldn't see it.

**Side find — tokenizer warning on v2-merged load:**
`The tokenizer ... with an incorrect regex pattern ... will lead to incorrect tokenization. You should set the
fix_mistral_regex=True flag …`. The merge step (a `merge_lora.py` run under whatever transformers/PEFT version
was active in April) saved v2's `tokenizer.json` with a known-buggy Mistral-style regex. Worth flagging to the
team; doesn't change the broad findings (the deep failures are model-side, not tokenizer-side).

**Implication:** the cosmetic-iteration pattern (v1 → v2 → no calibration progress) is the textbook case for
why the retention probe needs to be a **training-loop stop condition**, not a post-hoc audit. The team got the
adapter into a state they considered "shippable" without ever measuring the thing that was actually broken.
That's the gap this scaffold closes for Phase 3.

## Recommended fine-tune recipe (playbook for Phase 3)

| Knob | Prior attempt | **Recommended** | Why |
|---|---|---|---|
| Base | Qwen2.5-7B-Instruct | **Qwen3.6-27B** (live serving model) | Higher capacity ⇒ more forgetting-resistant; zero pipeline-switch cost |
| LoRA `r` | 128 | **16–32** | Smaller adapter overwrites less base behavior |
| LoRA α | 256 (α/r = 2.0) | **α = r or 2r modest** | Lower effective scaling |
| Target modules | all 7 (q/k/v/o + gate/up/down) | **q/k/v/o (± down)** | Attention-only adapts behavior with less drift |
| LR | 2e-4 | **≤ 1e-4** cosine, warmup 0.03 | Gentler updates |
| Epochs | 2 | **1** | Per roadmap; reduces forgetting risk |
| Train examples | 190 k (all firm matters) | **30–50 k curated, BImSchG-scoped** | Roadmap §3.1; filter by `quality_score` |
| Replay / general | 0 % | **5–10 %** non-legal German instruction data | Anchors base behavior |
| Eval | in-domain `val_loss` only | **`val_loss` + retention probe** every save | **Catches forgetting before "best" is saved** — was the missing piece |
| Final A/B | a few qualitative picks | per roadmap §3.4: **50 real BImSchG Qs**, lawyer-labelled, base-Qwen vs LoRA-Qwen vs base-Gemma-4-27B | Quantitative gate before ship; Gemma is the same-license challenger from above |
| Toolchain | (Qwen2.5 era) | pin PyTorch / bitsandbytes / Unsloth / PEFT to **sm_120 / CUDA 13.2** builds | Blackwell support is new (see Hardware below) |

The retention probe is the **missing piece** from the prior attempt. We're scaffolding it at
`LAI/training/fine_tuning/eval/` so the eval gap is closed before Phase 3 actually runs — see its README.

## Hardware feasibility (so the plan is grounded)

Verified 2026-05-29: **2× RTX PRO 6000 Blackwell, 96 GB each** (+ 2× EPYC 9654, ~1 TB RAM). **VRAM is not a
constraint** — all three candidates are the same ~24-27B footprint. QLoRA-27B (~20-24 GB) fits the current free
headroom (train alongside prod, off-hours); bf16 LoRA (~60-70 GB) wants one card freed (= §3.3 "GPU time when not
serving prod"). **Gotcha:** Blackwell **sm_120 + CUDA 13.2** is new → pin torch / bitsandbytes / Unsloth / PEFT to
sm_120 builds before committing GPU time. **No NVLink** → keep LoRA single-GPU.

## Honest gaps
- No public German-*legal* benchmark ranks these exact 2026 models head-to-head — the A/B (step 2) is how we get
  *our* answer on *our* data.
- German-quality claims for Gemma 4 / Qwen3.6 are vendor/aggregate, not legal-domain-specific.

## Sources
- [Best Open-Source LLM May 2026 — Llama 4 / Qwen 3.5 / DeepSeek V4 / Gemma 4 / Mistral](https://codersera.com/blog/best-open-source-llm-2026-llama-4-qwen-3-5-deepseek-v4-gemma-4-mistral/)
- [Gemma 4 adopts Apache 2.0](https://www.aibusinessreview.org/2026/04/03/google-gemma-4-apache-license-open-models/)
- [Open-source AI landscape Apr 2026 — Gemma/Qwen/Llama licenses](https://www.digitalapplied.com/blog/open-source-ai-landscape-april-2026-gemma-qwen-llama)
- [Best Open-Source LLM for German 2026 (SiliconFlow)](https://www.siliconflow.com/articles/en/best-open-source-LLM-for-German)
- [Mistral Small 24B model card (German support)](https://huggingface.co/mistralai/Mistral-Small-24B-Instruct-2501)
- [Gemma 4 model card (140+ languages)](https://ai.google.dev/gemma/docs/core/model_card_4)
- [Occiglot — open EU/German language models](https://occiglot.eu/)
- [Domain-Adaptation through Synthetic Data: Fine-Tuning LLMs for German Law (arXiv 2601.14160)](https://arxiv.org/pdf/2601.14160)
